"""Keyword 재정렬의 실서버 검증 — 플래그 on/off 실응답 대조 (S15P11A705-339).

`verify_live.py` 와 목적이 다르다 — 저쪽은 「오프라인 컷 재구성이 실서버와 같은가」를
한 서버로 재고, 이쪽은 **같은 코드로 플래그만 다르게 띄운 두 서버**의 실응답을 대조해
재정렬 런타임의 다섯 계약을 판정한다.

    ① 후보 집합 불변    같은 질의에서 off/on 응답의 Record id 집합이 같다 (순서만 차이)
    ② 재정렬 정확성     on 응답의 순서 = on 응답의 유사도 + 오프라인 keyword 신호로
                        재구성한 순서 (binary · floor 0.35 · weight 0.05 · top_k 3)
    ③ 무관 무노출 유지  무관 질의 15건의 0건 반환 집합이 off/on/오프라인 기대와 같다
    ④ 정답 무퇴행       기대 정답의 포함이 off/on 모두 유지되고 순위가 오프라인 예측과 같다
    ⑤ off = 현행 동일   off 응답의 순서가 유사도 내림차순이다 (재정렬 도입 전 동작)

서버는 **이 브랜치 코드**로, venv 경로를 명시해 띄운다(T29) — `python -m uvicorn` 은
시스템 Python 을 타고 조용히 죽는다. 로그는 파이프가 아니라 리디렉션으로 받는다(T30).

    DATABASE_URL=<스냅샷 :25432> SEARCH_KEYWORD_RERANK_ENABLED=false \
      .venv/Scripts/python.exe -m uvicorn app.main:app --port 8011 > .search/uvicorn_off.log 2>&1
    python tools/search_cut/rerank_verify_live.py collect --ai http://127.0.0.1:8011 \
      --out .search/rerank_live_off.json
    (서버 내리고 SEARCH_KEYWORD_RERANK_ENABLED=true 로 다시 띄운 뒤)
    python tools/search_cut/rerank_verify_live.py collect --ai http://127.0.0.1:8011 \
      --out .search/rerank_live_on.json
    python tools/search_cut/rerank_verify_live.py judge \
      --off .search/rerank_live_off.json --on .search/rerank_live_on.json \
      --json .search/rerank_live_report.json

GMS 임베딩이 질의당·서버당 1회 나간다(질의 97건 × 2 서버 ≈ 194회).

## 임베딩 흔들림의 처리 (T68)

off 와 on 은 별도 요청이라 같은 질의도 임베딩이 미세하게 다를 수 있다(|Δsim| 최대
0.0044 실측). 그래서 집합·판정이 어긋나면 곧바로 실패로 판정하지 않고 **경계 거리**를
함께 기록한다 — 어긋난 Record 의 유사도가 컷 경계(τ 또는 r×top1)에서 0.0044 이내면
「경계 위 흔들림」으로 분류하고 재시도로 확인한다(`collect --queries` 로 해당 질의만
다시 던질 수 있다). 그 밖이면 재정렬 결함이다. 재정렬 정확성(②)의 어긋남은 질의-Preset
코사인의 floor(0.35) 경계 거리로 같은 분류를 한다.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
SEARCH = ROOT / ".search"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fusion as F  # noqa: E402
from rank_score import cut, is_word_query  # noqa: E402

from app.core.config import get_settings  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

CASES = ("신한", "부캠", "그네", "스팟")
WOBBLE = 0.0044          # T68 실측 상한 — 이 이내의 경계 거리는 흔들림으로 분류
ROUND_TIE = 1e-4 + 1e-9  # 응답 similarity 가 4자리 반올림이라 이 이내는 동점일 수 있다

# 채택값 (config 기본값의 거울 — 값이 갈리면 판정이 틀리므로 judge 가 config 와 대조한다)
ADOPTED_FLOOR = 0.35
ADOPTED_WEIGHT = 0.05
ADOPTED_TOP_K = 3


def log(msg: str = "") -> None:
    print(msg, flush=True)


# ── 질의 셋 ──────────────────────────────────────────────────────────────────

def build_query_set() -> list[dict]:
    """행렬 3종에서 (query, user_id, tag, 기대 정답 record id 목록)을 모은다."""
    matrix = json.loads((SEARCH / "matrix.json").read_text(encoding="utf-8"))
    word = json.loads((SEARCH / "word_grid.json").read_text(encoding="utf-8"))
    probe = json.loads((SEARCH / "recall_probe.json").read_text(encoding="utf-8"))

    out: list[dict] = []
    for e in matrix["queries"]:
        out.append({
            "query": e["query"], "user_id": e["user_id"], "tag": "문장형",
            "expected": [r["record_id"] for r in e["results"] if r.get("is_expected")],
        })
    for e in matrix["offtopic"]:
        out.append({
            "query": e["query"], "user_id": e["user_id"], "tag": "무관",
            "expected": [],
        })
    for e in word["queries"]:
        out.append({
            "query": e["query"], "user_id": e["user_id"], "tag": "단어형",
            "expected": [r["record_id"] for r in e["results"] if r.get("is_expected")],
        })
    seen = {(q["query"], q["user_id"]) for q in out}
    for e in probe["queries"]:
        if e["query"] not in CASES:
            continue
        key = (e["query"], probe["user_id"])
        if key in seen:
            continue
        out.append({
            "query": e["query"], "user_id": probe["user_id"], "tag": "사례",
            "expected": [r["record_id"] for r in e["results"]
                         if r.get("name") == e.get("expect")],
        })
    return out


# ── collect — 서버에 던져 결과를 저장한다 ────────────────────────────────────

async def collect(args) -> int:
    settings = get_settings()
    queries = build_query_set()
    if args.queries:
        wanted = {q.strip() for q in args.queries.split(",") if q.strip()}
        queries = [q for q in queries if q["query"] in wanted]
    log(f"  서버 {args.ai} · 질의 {len(queries)}건 (GMS 임베딩 질의당 1회)")

    entries = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        # 기동 확인 — 죽은 서버에 GMS 호출을 낭비하지 않는다.
        ready = await client.get(f"{args.ai}/ready")
        if ready.status_code != 200:
            log(f"  [중단] /ready → HTTP {ready.status_code}")
            return 1
        for q in queries:
            resp = await client.post(
                f"{args.ai}/internal/v1/search",
                headers={"X-Internal-Secret": settings.internal_shared_secret},
                json={
                    "userId": q["user_id"],
                    "query": q["query"],
                    "limit": args.limit,
                    "embeddingProfile": settings.embedding_profile,
                },
            )
            entry = dict(q)
            entry["status"] = resp.status_code
            entry["results"] = resp.json()["results"] if resp.status_code == 200 else []
            entries.append(entry)
            if resp.status_code != 200:
                log(f"  [FAIL] '{q['query']}' → HTTP {resp.status_code}")

    out = Path(args.out)
    out.write_text(json.dumps({
        "ai": args.ai, "limit": args.limit,
        "rerank_flag": args.flag,
        "entries": entries,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    n_fail = sum(1 for e in entries if e["status"] != 200)
    log(f"  저장: {out} · HTTP 200 {len(entries) - n_fail}/{len(entries)}")
    return 0 if n_fail == 0 else 1


# ── judge — off/on 파일을 대조한다 ───────────────────────────────────────────

def _cut_boundary_dist(sim: float, query: str, top1: float, settings) -> float:
    """유사도가 컷 경계에서 얼마나 떨어져 있나 — 흔들림 분류의 근거."""
    floor = (settings.search_similarity_floor_word
             if is_word_query(query, settings.search_word_query_max_chars)
             else settings.search_similarity_floor)
    return min(abs(sim - floor), abs(sim - settings.search_top_ratio * top1))


def _load_keyword_index():
    kw = json.loads((SEARCH / "keyword_matrix.json").read_text(encoding="utf-8"))
    presets = {
        p["id"]: F.Preset(id=p["id"], version=p["version"], visibility=p["visibility"])
        for p in kw["presets"]
    }
    by_user: dict[int, list] = {}
    for c in kw["contexts"]:
        by_user.setdefault(c["user_id"], []).append(F.ContextKeywords(
            context_id=c["context_id"], record_id=c["record_id"],
            keyword_status=c["keyword_status"],
            keywords=tuple((k["keyword_id"], k["confidence"]) for k in c["keywords"]),
        ))
    query_cos = {
        e["query"]: {c["preset_id"]: c["cos"] for c in e["cos"]}
        for e in kw["query_preset"]
    }
    return presets, by_user, query_cos


def _matched_records(query: str, user_id: int, presets, by_user, query_cos) -> tuple[set, float]:
    """오프라인 keyword 신호로 match 된 Record 집합과 floor 경계 거리 최솟값."""
    qc = query_cos.get(query)
    if qc is None:
        return set(), float("inf")
    cand = F.preset_candidates(qc, presets, top_k=ADOPTED_TOP_K, floor=ADOPTED_FLOOR)
    boundary = min((abs(c - ADOPTED_FLOOR) for c in qc.values()), default=float("inf"))
    sig = F.record_signals(by_user.get(user_id, []), cand, presets, method=F.BINARY)
    return {rid for rid, s in sig.items() if s > 0}, boundary


def judge(args) -> int:
    settings = get_settings()
    if (settings.search_keyword_rerank_floor != ADOPTED_FLOOR
            or settings.search_keyword_rerank_weight != ADOPTED_WEIGHT
            or settings.search_keyword_rerank_top_k != ADOPTED_TOP_K):
        log("  [중단] config 채택값과 이 도구의 거울값이 다르다 — 판정 기준이 갈린다")
        return 1

    off = {(e["query"], e["user_id"]): e
           for e in json.loads(Path(args.off).read_text(encoding="utf-8"))["entries"]}
    on = {(e["query"], e["user_id"]): e
          for e in json.loads(Path(args.on).read_text(encoding="utf-8"))["entries"]}
    keys = [k for k in off if k in on]
    presets, by_user, query_cos = _load_keyword_index()

    # 무관 질의의 오프라인 기대 — artifact 유사도에 현행 컷을 걸어 0건인 집합.
    # **(query, user_id) 로 키를 잡는다** — 같은 질의 문구를 소유자 3명이 공유하므로
    # 질의 문구만으로 키를 잡으면 다른 소유자의 기대가 덮어써진다.
    matrix = json.loads((SEARCH / "matrix.json").read_text(encoding="utf-8"))
    offtopic_rows = {
        (e["query"], e["user_id"]): e["results"] for e in matrix["offtopic"]
    }
    offtopic_zero = {
        k for k, rows in offtopic_rows.items()
        if len(cut(rows, k[0],
                   tau=settings.search_similarity_floor,
                   tau_word=settings.search_similarity_floor_word,
                   ratio=settings.search_top_ratio)) == 0
    }

    verdicts = {f"J{i}": {"pass": 0, "wobble": [], "fail": []} for i in range(1, 6)}
    detail = []

    for key in keys:
        q, uid = key
        eo, en = off[key], on[key]
        rows_off, rows_on = eo["results"], en["results"]
        ids_off = [r["recordId"] for r in rows_off]
        ids_on = [r["recordId"] for r in rows_on]
        sims_off = {r["recordId"]: r["similarity"] for r in rows_off}
        sims_on = {r["recordId"]: r["similarity"] for r in rows_on}
        d = {"query": q, "user_id": uid, "tag": eo["tag"],
             "off_ids": ids_off, "on_ids": ids_on}

        # ① 후보 집합 불변
        if set(ids_off) == set(ids_on):
            verdicts["J1"]["pass"] += 1
        else:
            diff = set(ids_off) ^ set(ids_on)
            dists = {}
            for rid in diff:
                src = (rows_off, sims_off) if rid in sims_off else (rows_on, sims_on)
                top1 = max(r["similarity"] for r in src[0])
                dists[rid] = _cut_boundary_dist(src[1][rid], q, top1, settings)
            d["set_diff"] = {str(r): round(v, 4) for r, v in dists.items()}
            bucket = "wobble" if all(v <= WOBBLE for v in dists.values()) else "fail"
            verdicts["J1"][bucket].append(d["query"])

        # ② 재정렬 정확성 — on 응답 자신의 유사도 + 오프라인 신호로 재구성
        matched, cand_boundary = _matched_records(q, uid, presets, by_user, query_cos)
        scored = [
            (r["recordId"],
             r["similarity"] + (ADOPTED_WEIGHT if r["recordId"] in matched else 0.0))
            for r in rows_on
        ]
        expect_on = [rid for rid, _ in
                     sorted(scored, key=lambda t: -t[1])]  # 안정 정렬 = 동점 시 on 순서
        if ids_on == expect_on:
            verdicts["J2"]["pass"] += 1
        else:
            d["on_expected"] = expect_on
            d["matched"] = sorted(matched & set(ids_on))
            d["cand_floor_dist"] = round(cand_boundary, 4)
            score = dict(scored)
            tie_ok = all(
                score[ids_on[i]] >= score[ids_on[i + 1]] - ROUND_TIE
                for i in range(len(ids_on) - 1)
            )
            bucket = "wobble" if (tie_ok or cand_boundary <= WOBBLE) else "fail"
            verdicts["J2"][bucket].append(q)

        # ③ 무관 무노출 — 오프라인 기대(무노출 11건)와 off/on 실응답이 같아야 한다
        if eo["tag"] == "무관":
            expected_zero = (q, uid) in offtopic_zero
            live_zero = (len(ids_off) == 0, len(ids_on) == 0)
            d["offtopic"] = {"expected_zero": expected_zero,
                             "off_n": len(ids_off), "on_n": len(ids_on)}
            if live_zero == (expected_zero, expected_zero):
                verdicts["J3"]["pass"] += 1
            else:
                # 기대와 어긋남 — 경계 거리로 흔들림/결함을 가른다. 살아 있는 행이
                # 있으면 그 top 행의, 없으면 artifact top 행의 컷 경계 거리를 본다.
                rows = rows_off or rows_on
                if rows:
                    top1 = max(r["similarity"] for r in rows)
                    dist = _cut_boundary_dist(top1, q, top1, settings)
                else:
                    art = offtopic_rows.get((q, uid)) or []
                    top1 = art[0]["sim"] if art else 0.0
                    dist = _cut_boundary_dist(top1, q, top1, settings) if art else 0.0
                d["offtopic"]["boundary_dist"] = round(dist, 4)
                same_onoff = live_zero[0] == live_zero[1]
                bucket = "wobble" if (same_onoff and dist <= WOBBLE) else "fail"
                verdicts["J3"][bucket].append(q)

        # ④ 정답 무퇴행 (기대 정답이 있는 질의만)
        if eo["expected"]:
            want = set(eo["expected"])
            in_off = want & set(ids_off)
            in_on = want & set(ids_on)
            if in_off == in_on:
                verdicts["J4"]["pass"] += 1
            else:
                lost = (in_off - in_on) | (in_on - in_off)
                dists = {}
                for rid in lost:
                    sims = sims_off if rid in sims_off else sims_on
                    rows = rows_off if rid in sims_off else rows_on
                    top1 = max(r["similarity"] for r in rows)
                    dists[rid] = _cut_boundary_dist(sims[rid], q, top1, settings)
                d["expected_diff"] = {str(r): round(v, 4) for r, v in dists.items()}
                bucket = ("wobble" if dists and all(v <= WOBBLE for v in dists.values())
                          else "fail")
                verdicts["J4"][bucket].append(q)
            d["expected_rank_off"] = [
                ids_off.index(r) + 1 for r in eo["expected"] if r in ids_off]
            d["expected_rank_on"] = [
                ids_on.index(r) + 1 for r in eo["expected"] if r in ids_on]

        # ⑤ off = 유사도 내림차순
        mono = all(
            rows_off[i]["similarity"] >= rows_off[i + 1]["similarity"] - ROUND_TIE
            for i in range(len(rows_off) - 1)
        )
        if mono:
            verdicts["J5"]["pass"] += 1
        else:
            verdicts["J5"]["fail"].append(q)

        detail.append(d)

    names = {
        "J1": f"① 후보 집합 불변      (전 {len(keys)}건)",
        "J2": f"② 재정렬 정확성       (전 {len(keys)}건)",
        "J3": "③ 무관 무노출 유지    (무관 15건)",
        "J4": "④ 정답 무퇴행         (기대 정답 있는 질의)",
        "J5": f"⑤ off 유사도 내림차순 (전 {len(keys)}건)",
    }
    log("=" * 88)
    log("재정렬 실서버 검증 — off/on 대조")
    log("=" * 88)
    all_ok = True
    for j, name in names.items():
        v = verdicts[j]
        ok = not v["fail"]
        all_ok = all_ok and ok
        line = f"  [{'PASS' if ok else 'FAIL'}] {name}  통과 {v['pass']}"
        if v["wobble"]:
            line += f" · 경계 흔들림 {len(v['wobble'])}건 {v['wobble']}"
        if v["fail"]:
            line += f" · 결함 {len(v['fail'])}건 {v['fail']}"
        log(line)
    log("\n  경계 흔들림 = 어긋난 Record 의 컷 경계 거리(또는 후보 floor 거리)가 "
        f"{WOBBLE} 이내 (T68). 재시도로 확인한다 — 결함으로 세지 않되 보고에 남긴다.")

    if args.json:
        Path(args.json).write_text(json.dumps({
            "verdicts": {j: {"pass": v["pass"], "wobble": v["wobble"],
                             "fail": v["fail"]} for j, v in verdicts.items()},
            "adopted": {"floor": ADOPTED_FLOOR, "weight": ADOPTED_WEIGHT,
                        "top_k": ADOPTED_TOP_K},
            "wobble_threshold": WOBBLE,
            "detail": detail,
        }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        log(f"\n  저장: {args.json}")
    return 0 if all_ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="재정렬 실서버 검증 (S15P11A705-339)")
    sub = ap.add_subparsers(dest="mode", required=True)

    c = sub.add_parser("collect", help="서버에 질의 셋을 던져 응답을 저장")
    c.add_argument("--ai", default="http://127.0.0.1:8011")
    c.add_argument("--out", required=True)
    c.add_argument("--flag", default="", help="기록용 — 서버의 재정렬 플래그 상태")
    c.add_argument("--limit", type=int, default=20)
    c.add_argument("--queries", default="", help="쉼표 구분 — 재시도용 부분 수집")

    j = sub.add_parser("judge", help="off/on 수집 파일을 대조해 5판정")
    j.add_argument("--off", required=True)
    j.add_argument("--on", dest="on", required=True)
    j.add_argument("--json")

    args = ap.parse_args()
    if args.mode == "collect":
        return asyncio.run(collect(args))
    return judge(args)


if __name__ == "__main__":
    raise SystemExit(main())
