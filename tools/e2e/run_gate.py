"""검색 고도화 검증 게이트 러너 (P49 §7) — back 경유 전체 경로 E2E.

통합 브랜치 빌드의 back(Spring)과 ai(FastAPI)를 함께 띄운 상태에서, 사용자와 같은
경로(`POST /v1/search/records`, JWT 인증)로 검색해 게이트 5기준을 판정한다. FastAPI 를
직접 치는 기존 `run_search.py` 와 달리 **Spring 의 문자열 검색·병합과 Core 재검증까지**
경로에 들어온다 — 기준 ①의 `신한` 회복이 Spring 몫이라 이 경로가 아니면 잴 수 없다.

    # 수집 (서버 기동 상태에서 phase 별로)
    python tools/e2e/run_gate.py collect --phase off      # 전 플래그 꺼짐
    python tools/e2e/run_gate.py collect --phase on       # 전 플래그 켜짐
    python tools/e2e/run_gate.py collect --phase degraded # 켜짐 + LLM 타임아웃 강제

    # 판정 (세 phase 수집 후)
    python tools/e2e/run_gate.py judge

수집 결과는 `.search/gate_<phase>.json`, 판정은 `.search/gate_verdict.json` 에 남는다.
토큰은 back 레포 `loadtest/tools/mint-tokens.sh` 가 만든 `tokens.json` 을 쓴다(서버와
같은 JWT 키 전제). DB 는 멤버 매핑과 장소명 조회에만 읽기로 쓰고, 포트 가드(:25432)가
시연 DB(:15432)·e2e DB(:5433) 를 막는다.

기준과 확인 방법 (P49 §7 표 그대로):
    ① 잔존 3건(신한·부캠·신한 부캠) 회복      → phase on 에서 기대 정답 포함
    ② 무관 무노출 ≥ 11/15                    → phase on 의 무관 질의 빈 결과 수
    ③ 시연 정본 12건 무퇴행                   → phase on 의 1위 = demo_data.yaml 기대
    ④ 전 플래그 off = 현행 동일               → phase off 가 현행 기대와 일치 + 유사도 내림차순
    ⑤ LLM 타임아웃 시 벡터 복귀               → phase degraded 가 200 응답 + 재작성 무효과
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.db import Database  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def log(msg: str = "") -> None:
    print(msg, flush=True)


SEARCH = ROOT / ".search"
EXPECT_PORT = "25432"
DEMO_PROVIDER = "demo-seed"

# 잔존 실패 3건과 대조 유지 2건. 전부 jeongheon 소유 세그먼트다(선행 실측과 동일).
# expect 는 장소명이다. recover 가 True 인 3건이 기준 ① 의 대상이다.
CASES = [
    {"query": "신한", "expect": "카츠요", "recover": True, "signal": "문자열 검색(Spring)"},
    {"query": "부캠", "expect": "카츠요", "recover": True, "signal": "LLM 재작성"},
    {"query": "신한 부캠", "expect": "카츠요", "recover": True, "signal": "LLM 재작성"},
    {"query": "그네", "expect": "동교어린이공원", "recover": False, "signal": "현행 유지"},
    {"query": "스팟", "expect": "동교어린이공원", "recover": False, "signal": "현행 유지"},
]

# 관련 없는 문장형 질의 5종 × 소유자 3명(host·gahyeon·jeongheon) = 15건.
# 현행(재작성 전) 무노출 11/15 가 기준선이다 — matrix.json 실측과 같은 셋.
OFFTOPIC_QUERIES = [
    "자동차 엔진오일 교환 정비소",
    "치과 임플란트 상담 받을 곳",
    "겨울 스키장 리프트권 파는 데",
    "노트북 액정 수리 서비스센터",
    "강아지 예방접종 동물병원",
]
OFFTOPIC_OWNERS = ["host", "gahyeon", "jeongheon"]

_MEMBERS = "SELECT provider_user_id, member_id FROM core.social_account WHERE provider = $1"


class GuardError(SystemExit):
    pass


def load_demo_queries() -> list[dict]:
    """시연 정본 질의 12건. expect(record key)를 장소명으로 푼다 — 대조는 장소명으로 한다."""
    data = yaml.safe_load(
        (ROOT / "tools" / "demo_seed" / "demo_data.yaml").read_text(encoding="utf-8"))
    place_by_key = {}
    for member in data["members"]:
        for record in member.get("records", []):
            place_by_key[record["key"]] = record["place"]["name"]
    queries = []
    for q in data["demo_queries"]:
        queries.append({
            "query": q["query"],
            "expect": place_by_key[q["expect"]],
            "as": q.get("as", "host"),
        })
    if len(queries) != 12:
        raise GuardError(f"시연 정본 질의가 12건이 아니다: {len(queries)}건")
    return queries


async def member_map(settings) -> dict[str, int]:
    db = Database(settings.database_url)
    await db.connect()
    try:
        async with db.acquire() as conn:
            rows = await conn.fetch(_MEMBERS, DEMO_PROVIDER)
    finally:
        await db.disconnect()
    return {r["provider_user_id"]: r["member_id"] for r in rows}


async def collect(args) -> int:
    settings = get_settings()
    if EXPECT_PORT not in settings.database_url:
        raise GuardError(
            f"DATABASE_URL 이 :{EXPECT_PORT}(스냅샷 DB)를 가리키지 않는다 — 재지 않고 멈춘다.")

    minted = json.loads(Path(args.tokens).read_text(encoding="utf-8"))
    # mint-tokens.sh 산출물은 {baseUrl, ttl, tokens:{member_id: token}} 구조다.
    tokens = minted.get("tokens", minted)
    members = await member_map(settings)
    demo = load_demo_queries()

    plan = []  # (kind, query, member_id, expect)
    for q in demo:
        plan.append(("demo", q["query"], members[q["as"]], q["expect"]))
    for c in CASES:
        plan.append(("case", c["query"], members["jeongheon"], c["expect"]))
    for q in OFFTOPIC_QUERIES:
        for owner in OFFTOPIC_OWNERS:
            plan.append(("offtopic", q, members[owner], None))

    # 인증은 Authorization 헤더가 아니라 access_token **쿠키**다. CSRF 는 예비 GET 이
    # 내려주는 XSRF-TOKEN 을 같은 요청의 쿠키+`X-XSRF-TOKEN` 헤더로 함께 실어야 통과하고,
    # 서버(CsrfCookieFilter)가 매 요청 재발급하므로 응답의 회전 값으로 갱신한다 — back
    # `loadtest/k6/lib/http.js` 가 확립한 프로토콜 그대로다. 쿠키를 클라이언트 항아리에
    # 맡기지 않고 요청마다 직접 싣는 이유: XSRF-TOKEN 이 Secure 쿠키라 httpx 항아리가
    # http:// 재전송에서 떨어뜨린다(로컬 검증은 평문 http 다).
    xsrf: dict[int, str] = {}

    async def fetch_xsrf(client: httpx.AsyncClient, member_id: int, token: str) -> str:
        resp = await client.get(
            f"{args.back}/v1/collections", cookies={"access_token": token})
        value = resp.cookies.get("XSRF-TOKEN")
        if not value:
            raise GuardError(f"member {member_id}: XSRF-TOKEN 을 받지 못했다 (HTTP {resp.status_code})")
        return value

    results = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        for kind, query, member_id, expect in plan:
            token = tokens.get(str(member_id))
            if token is None:
                raise GuardError(f"member {member_id} 의 토큰이 없다 — mint-tokens.sh 로 발급한다.")
            if member_id not in xsrf:
                xsrf[member_id] = await fetch_xsrf(client, member_id, token)
            resp = await client.post(
                f"{args.back}/v1/search/records",
                json={"query": query},
                cookies={"access_token": token, "XSRF-TOKEN": xsrf[member_id]},
                headers={"X-XSRF-TOKEN": xsrf[member_id]},
            )
            rotated = resp.cookies.get("XSRF-TOKEN")
            if rotated:
                xsrf[member_id] = rotated
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            items = (body.get("data") or {}).get("items") or []
            results.append({
                "kind": kind,
                "query": query,
                "member_id": member_id,
                "expect": expect,
                "status": resp.status_code,
                "items": [
                    {"recordId": i["recordId"], "similarity": i["similarity"],
                     "place": i["place"]["name"]}
                    for i in items
                ],
            })
            mark = "" if resp.status_code == 200 else f"  [HTTP {resp.status_code}]"
            log(f"  {kind:9s} {query!r} (m{member_id}): {len(items)}건{mark}")

    out = SEARCH / f"gate_{args.phase}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"phase": args.phase, "back": args.back, "results": results},
        ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"\n  → {out}")
    return 0


def _load_phase(phase: str) -> list[dict]:
    return json.loads((SEARCH / f"gate_{phase}.json").read_text(encoding="utf-8"))["results"]


def _first_place(row: dict) -> str | None:
    return row["items"][0]["place"] if row["items"] else None


def _names(row: dict) -> list[str]:
    return [i["place"] for i in row["items"]]


def judge(args) -> int:
    off = _load_phase("off")
    on = _load_phase("on")
    degraded = _load_phase("degraded")
    verdict = {}
    failed = []

    def by(rows, kind):
        return [r for r in rows if r["kind"] == kind]

    # 모든 phase 에서 HTTP 200 이 전제다 — 어느 기준이든 오류 응답 위에서 판정하지 않는다.
    for phase_name, rows in (("off", off), ("on", on), ("degraded", degraded)):
        bad = [r for r in rows if r["status"] != 200]
        if bad:
            failed.append(f"{phase_name}: HTTP 오류 {len(bad)}건")

    # ① 잔존 3건 회복 (on)
    rec = {}
    for c in CASES:
        row = next(r for r in by(on, "case") if r["query"] == c["query"])
        hit = c["expect"] in _names(row)
        rec[c["query"]] = hit
        if c["recover"] and not hit:
            failed.append(f"기준①: {c['query']!r} 미회복 ({c['signal']})")
    verdict["1_recover"] = rec

    # ② 무관 무노출 ≥ 11/15 (on)
    silent = sum(1 for r in by(on, "offtopic") if not r["items"])
    verdict["2_offtopic_silent"] = {"silent": silent, "total": 15, "baseline": 11}
    if silent < 11:
        failed.append(f"기준②: 무관 무노출 {silent}/15 < 11")

    # ③ 시연 정본 12건 무퇴행 (on) — 「기대 정답이 전부 유지된다」의 기준선은 현행이다.
    #    현행의 1위 적중은 12건 중 10건이 실측 기록이다(matrix.json 문장형 hit@1 0.8333,
    #    I53 표와 동일). 그래서 1위 일치가 아니라 **기대 정답 포함 유지 + off 대비 순위
    #    비악화**를 판정한다. 1위 일치 수는 참고로 남긴다.
    def expect_rank(row: dict) -> int | None:
        names = _names(row)
        return names.index(row["expect"]) + 1 if row["expect"] in names else None

    off_demo = {r["query"]: r for r in by(off, "demo")}
    demo_bad = []
    for r in by(on, "demo"):
        rank_on = expect_rank(r)
        rank_off = expect_rank(off_demo[r["query"]])
        if rank_on is None or (rank_off is not None and rank_on > rank_off):
            demo_bad.append(f"{r['query']}({rank_off}위→{rank_on}위)")
    top1_on = sum(1 for r in by(on, "demo") if _first_place(r) == r["expect"])
    verdict["3_demo_no_regression"] = {
        "regressed": demo_bad, "top1": top1_on, "total": 12}
    if demo_bad:
        failed.append(f"기준③: 시연 퇴행 {len(demo_bad)}건 {demo_bad}")

    # ④ off = 현행 동일 — 시연 1위 불일치가 **현행 실측(matrix.json)의 불일치 목록과
    #    정확히 같은지**로 대조한다(현행 동일의 실증). 사례·무관·유사도 내림차순 정렬도
    #    함께 본다.
    matrix = json.loads((SEARCH / "matrix.json").read_text(encoding="utf-8"))
    current_top1_bad = set()
    for q in matrix["queries"]:
        rows = q["results"]
        expected = {r["name"] for r in rows if r.get("is_expected")}
        if rows and rows[0]["name"] not in expected:
            current_top1_bad.add(q["query"])
    off_demo_bad = [r["query"] for r in by(off, "demo") if _first_place(r) != r["expect"]]
    if set(off_demo_bad) != current_top1_bad:
        failed.append(
            f"기준④: off 1위 불일치({sorted(off_demo_bad)})가 현행 실측"
            f"({sorted(current_top1_bad)})과 다르다")
    off_demo_bad = sorted(set(off_demo_bad) - current_top1_bad)  # 현행과 같은 불일치는 정상
    off_case = {r["query"]: r for r in by(off, "case")}
    off_case_bad = []
    for c in CASES:
        included = c["expect"] in _names(off_case[c["query"]])
        want = not c["recover"]  # 현행: 회복 대상 3건은 없어야, 유지 2건은 있어야
        if included is not want:
            off_case_bad.append(c["query"])
    off_silent = sum(1 for r in by(off, "offtopic") if not r["items"])
    unsorted = [
        r["query"] for r in off
        if [i["similarity"] for i in r["items"]]
        != sorted((i["similarity"] for i in r["items"]), reverse=True)
    ]
    verdict["4_off_is_current"] = {
        "demo_top1_failed": off_demo_bad, "case_mismatch": off_case_bad,
        "offtopic_silent": off_silent, "unsorted": unsorted,
    }
    if off_demo_bad or off_case_bad or unsorted or off_silent != 11:
        failed.append(
            f"기준④: off 현행 불일치 — demo {off_demo_bad}, case {off_case_bad}, "
            f"무관 {off_silent}/15(기대 11), 정렬 위반 {unsorted}")

    # ⑤ LLM 타임아웃 시 벡터 복귀 (degraded) — 응답이 실패하지 않고, 재작성 효과만
    #    사라진다. 재작성 몫(부캠·신한 부캠)은 off 와 같아지고, LLM 과 무관한 문자열
    #    회복(신한)과 유지 2건·시연 1위는 on 과 같아야 한다.
    deg_case = {r["query"]: r for r in by(degraded, "case")}
    deg_bad = []
    for c in CASES:
        included = c["expect"] in _names(deg_case[c["query"]])
        if c["signal"] == "LLM 재작성":
            want = False  # 재작성이 죽었으므로 회복이 사라져야 한다
        elif c["signal"] == "문자열 검색(Spring)":
            want = True   # LLM 과 무관 — 회복이 유지돼야 한다
        else:
            want = True
        if included is not want:
            deg_bad.append(f"{c['query']}(기대 {'포함' if want else '미포함'})")
    deg_demo_bad = []
    for r in by(degraded, "demo"):
        rank_deg = expect_rank(r)
        rank_off = expect_rank(off_demo[r["query"]])
        if rank_deg is None or (rank_off is not None and rank_deg > rank_off):
            deg_demo_bad.append(f"{r['query']}({rank_off}위→{rank_deg}위)")
    verdict["5_degraded"] = {"case_mismatch": deg_bad, "demo_regressed": deg_demo_bad}
    if deg_bad or deg_demo_bad:
        failed.append(f"기준⑤: 강등 동작 불일치 — case {deg_bad}, demo {deg_demo_bad}")

    verdict["failed"] = failed
    verdict["passed"] = not failed
    out = SEARCH / "gate_verdict.json"
    out.write_text(json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")

    log("\n== 게이트 판정 (P49 §7) ==")
    log(f"  ① 잔존 3건 회복        : {'PASS' if all(rec[c['query']] for c in CASES if c['recover']) else 'FAIL'}  {rec}")
    log(f"  ② 무관 무노출          : {'PASS' if silent >= 11 else 'FAIL'}  {silent}/15 (기준 11)")
    log(f"  ③ 시연 12건 무퇴행     : {'PASS' if not demo_bad else 'FAIL'}  퇴행 {len(demo_bad)}건 · 1위 {top1_on}/12 (현행 10/12)")
    ok4 = not (off_demo_bad or off_case_bad or unsorted) and off_silent == 11
    log(f"  ④ off = 현행 동일       : {'PASS' if ok4 else 'FAIL'}")
    log(f"  ⑤ LLM 타임아웃 복귀     : {'PASS' if not (deg_bad or deg_demo_bad) else 'FAIL'}")
    log(f"\n  종합: {'전부 통과' if not failed else 'FAIL — ' + ' / '.join(failed)}")
    log(f"  → {out}")
    return 0 if not failed else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--phase", required=True, choices=["off", "on", "degraded"])
    c.add_argument("--back", default="http://localhost:8082/api/core")
    c.add_argument("--tokens", default=str(
        ROOT.parents[3] / "back" / ".claude" / "worktrees" / "integration"
        / "loadtest" / "artifacts" / "tokens.json"))
    j = sub.add_parser("judge")
    args = ap.parse_args()
    if args.cmd == "collect":
        return asyncio.run(collect(args))
    return judge(args)


if __name__ == "__main__":
    raise SystemExit(main())
