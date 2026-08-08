"""문자열 검색 병합 규칙 비교 — P49 §8 작업 3.

**DB 도 GMS 도 부르지 않는다.** `lexical_matrix.py` 가 만든 매치 artifact 와 기존 행렬만
읽는다. 병합 규칙과 게이트를 바꿔 가며 순위 지표와 무관 질의 노출을 비교하는 것이 목적이다.

    python tools/search_cut/lexical_sweep.py

## 무엇을 비교하나

**게이트 3단** — 문자열 매치를 후보로 인정하는 조건.

    G0  모든 질의 · 부분일치           (게이트 없음 — 대조군)
    G1  단어형 질의만 · 부분일치        (문장형의 조사·부사 우연 일치 차단)
    G2  단어형 질의만 · 어절 시작 경계   (「대신한」류 문자열 우연 차단)

**병합 규칙 3종** — 게이트를 통과한 문자열 후보를 결과에 넣는 방법.

    M1  뒤에 추가     벡터 컷 통과자를 그대로 두고, 문자열 후보를 그 뒤에 유사도순으로 추가
    M2  RRF 결합     벡터 컷 통과자와 문자열 후보의 합집합을 순위 결합(k=60)으로 재정렬
    M3  앞에 고정     문자열 후보를 맨 앞에 두고 벡터 컷 통과자를 뒤에

지표는 `rank_score.py` 의 것을 그대로 쓴다 — baseline 과 다른 자로 재면 비교가 성립하지
않는다. 컷은 현행 그대로 벡터 경로에만 적용한다. 문자열 후보는 컷을 거치지 않고 게이트가
자격을 정한다(P49 §4 — 「어떤 후보도 근거 없이 들어오지 않는다」의 문자열 쪽 근거가 게이트다).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rank_score import (  # noqa: E402
    SERVICE_LIMIT,
    GuardError,
    aggregate,
    cut,
    is_word_query,
    metrics_for,
    zero_rate,
)

ROOT = Path(__file__).resolve().parents[2]
SEARCH = ROOT / ".search"

CASES = ("신한", "부캠", "그네", "스팟")
RRF_K = 60.0

GATES = ("G0", "G1", "G2")
RULES = ("M1", "M2", "M3")


def log(msg: str = "") -> None:
    print(msg, flush=True)


def load(search_dir: Path = SEARCH) -> tuple[dict, dict]:
    paths = {
        "matrix": search_dir / "matrix.json",
        "word_grid": search_dir / "word_grid.json",
        "recall_probe": search_dir / "recall_probe.json",
        "lexical": search_dir / "lexical_matrix.json",
    }
    for k, p in paths.items():
        if not p.exists():
            raise GuardError(f"파일이 없다: {p}")
    data = {k: json.loads(p.read_text(encoding="utf-8")) for k, p in paths.items()}
    profiles = {k: d.get("profile") for k, d in data.items()}
    if len(set(profiles.values())) != 1 or None in profiles.values():
        raise GuardError(f"Profile 이 어긋난다: {profiles}")
    lex = {e["query"]: e["matches"] for e in data["lexical"]["queries"]}
    return data, lex


def lexical_candidates(lex: dict, query: str, uid, gate: str) -> list[int]:
    """게이트를 통과한 문자열 매치 Record id 목록."""
    if gate in ("G1", "G2") and not is_word_query(query):
        return []
    entries = (lex.get(query) or {}).get(str(uid)) or []
    if gate == "G2":
        entries = [e for e in entries if e["boundary"]]
    return [e["record_id"] for e in entries]


def merge(rows: list[dict], kept: list[dict], lex_ids: list[int],
          rule: str, limit: int) -> list[dict]:
    """컷 통과자(kept)와 문자열 후보(lex_ids)를 병합한다. rows 는 유사도 내림차순 전량이다."""
    by_rid = {}
    vrank = {}
    for i, r in enumerate(rows, 1):
        rid = r["record_id"]
        if rid not in by_rid:
            by_rid[rid] = r
            vrank[rid] = i
    kept_ids = [r["record_id"] for r in kept]
    lex_rows = [by_rid[rid] for rid in lex_ids if rid in by_rid]
    lex_rows.sort(key=lambda r: -float(r["sim"]))

    if rule == "M1":
        out = list(kept) + [r for r in lex_rows if r["record_id"] not in kept_ids]
    elif rule == "M3":
        lex_set = {r["record_id"] for r in lex_rows}
        out = lex_rows + [r for r in kept if r["record_id"] not in lex_set]
    elif rule == "M2":
        lrank = {r["record_id"]: i for i, r in enumerate(lex_rows, 1)}
        cand = {r["record_id"] for r in kept} | set(lrank)
        scored = []
        for rid in cand:
            s = 1.0 / (RRF_K + vrank[rid])
            if rid in lrank:
                s += 1.0 / (RRF_K + lrank[rid])
            scored.append((s, vrank[rid], rid))
        scored.sort(key=lambda x: (-x[0], x[1], x[2]))
        out = [by_rid[rid] for _, _, rid in scored]
    else:
        raise GuardError(f"알 수 없는 병합 규칙: {rule}")
    return out[:limit]


def evaluate(entries: list[dict], *, lex: dict, gate: str, rule: str,
             limit: int, default_uid=None) -> tuple[dict, dict]:
    """세그먼트 하나를 재고 (지표, 부가 진단)을 돌려준다."""
    per, diag = [], {"lex_hits": 0, "lex_only_added": 0, "gate_dropped_expected": 0}
    for e in entries:
        q, rows = e["query"], (e.get("results") or [])
        uid = e.get("user_id", default_uid)
        if uid is None:
            raise GuardError(f"질의 `{q}` 에 user_id 가 없다")
        n_rel = sum(1 for r in rows if r.get("is_expected"))
        kept = cut(rows, q, limit=limit)
        lex_ids = lexical_candidates(lex, q, uid, gate)
        diag["lex_hits"] += len(lex_ids)
        kept_ids = {r["record_id"] for r in kept}
        diag["lex_only_added"] += sum(1 for rid in lex_ids if rid not in kept_ids)
        # 게이트가 자른 기대 정답 — 부분일치로는 매치인데 이 게이트에선 제외된 것
        all_ids = set(lexical_candidates(lex, q, uid, "G0"))
        expected_ids = {r["record_id"] for r in rows if r.get("is_expected")}
        diag["gate_dropped_expected"] += len((all_ids - set(lex_ids)) & expected_ids)
        final = merge(rows, kept, lex_ids, rule, limit)
        per.append(metrics_for(final, n_rel))
    agg = aggregate(per)
    if agg:
        agg["zero_rate"] = zero_rate(per)
    return agg, diag


COLS = ("n", "hit@1", "hit@3", "mrr", "recall@3", "ndcg@3", "zero_rate", "returned")


def row_str(label: str, m: dict, extra: str = "") -> str:
    if not m:
        return f"  {label:<14} (0건)"
    cells = "".join(
        (str(m[c]).rjust(9) if c == "n" else f"{m[c]:.4f}".rjust(9)) for c in COLS
    )
    return f"  {label:<14}{cells}  {extra}"


def main() -> int:
    ap = argparse.ArgumentParser(description="문자열 병합 규칙 비교 (P49 작업 3)")
    ap.add_argument("--json")
    ap.add_argument("--limit", type=int, default=SERVICE_LIMIT)
    args = ap.parse_args()

    try:
        data, lex = load()
    except GuardError as exc:
        print(f"[가드] {exc}", file=sys.stderr)
        return 1

    probe_uid = data["recall_probe"].get("user_id")
    segs = {
        "문장형(정답)": (data["matrix"]["queries"], None),
        "단어형(정답)": (data["word_grid"]["queries"], None),
        "무관-문장형": (data["matrix"]["offtopic"], None),
        "무관-단어형": (data["word_grid"]["offtopic"], None),
        "타인소유-단어형": (data["word_grid"]["cross"], None),
    }

    log("=" * 100)
    log("문자열 병합 규칙 비교 — P49 작업 3")
    log("=" * 100)
    log(f"  Profile   {data['lexical']['profile']}")
    log(f"  매치 원본  lexical_matrix.json (질의 {len(lex)}건 · DB {data['lexical']['db_port']})")
    log(f"  컷        현행 그대로 벡터 경로에만 · limit={args.limit}")
    log("  호출       DB 0회 · GMS 0회")

    result: dict = {"stage": "P49-lexical-sweep", "grid": {}}

    for seg_name, (entries, default_uid) in segs.items():
        log(f"\n{seg_name}")
        log("  " + "게이트·규칙".ljust(12) + "".join(c.rjust(9) for c in COLS)
            + "  매치/단독추가/게이트제외")
        base, _ = evaluate(entries, lex=lex, gate="G1", rule="M1", limit=args.limit)
        # baseline: 문자열 후보 없이 컷 통과자만 — G1·M1 에서 lex 를 비우는 대신 직접 계산
        per = []
        for e in entries:
            rows = e.get("results") or []
            n_rel = sum(1 for r in rows if r.get("is_expected"))
            per.append(metrics_for(cut(rows, e["query"], limit=args.limit), n_rel))
        base = aggregate(per)
        base["zero_rate"] = zero_rate(per)
        log(row_str("현행(벡터만)", base))
        result["grid"].setdefault(seg_name, {})["baseline"] = base
        for gate in GATES:
            for rule in RULES:
                m, diag = evaluate(entries, lex=lex, gate=gate, rule=rule,
                                   limit=args.limit, default_uid=default_uid)
                d = diag
                extra = f"{d['lex_hits']}/{d['lex_only_added']}/{d['gate_dropped_expected']}"
                log(row_str(f"{gate}·{rule}", m, extra))
                result["grid"][seg_name][f"{gate}.{rule}"] = {"metrics": m, "diag": diag}

    # 사례 4건 — recall_probe (소유자 1명)
    log("\n사례별 (병합 후 정답 순위 · `—` 는 없음)")
    probe = [
        dict(e, user_id=e.get("user_id", probe_uid),
             results=[dict(r, is_expected=(r.get("name") == e.get("expect")))
                      for r in (e.get("results") or [])])
        for e in data["recall_probe"]["queries"] if e["query"] in CASES
    ]
    log("  " + "게이트·규칙".ljust(12) + "".join(q.rjust(10) for q in CASES))
    cells = []
    for e in probe:
        kept = cut(e["results"], e["query"], limit=args.limit)
        hit = next((i for i, r in enumerate(kept, 1) if r["is_expected"]), None)
        cells.append((e["query"], hit))
    order = {q: i for i, q in enumerate(CASES)}
    cells.sort(key=lambda x: order.get(x[0], 99))
    log("  " + "현행(벡터만)".ljust(12)
        + "".join((str(h) if h else "—").rjust(10) for _, h in cells))
    result["cases"] = {"baseline": dict(cells)}
    for gate in GATES:
        for rule in RULES:
            row = {}
            for e in probe:
                rows_, q, uid = e["results"], e["query"], e["user_id"]
                kept = cut(rows_, q, limit=args.limit)
                final = merge(rows_, kept, lexical_candidates(lex, q, uid, gate),
                              rule, args.limit)
                row[q] = next((i for i, r in enumerate(final, 1) if r["is_expected"]), None)
            log("  " + f"{gate}·{rule}".ljust(12)
                + "".join((str(row.get(q)) if row.get(q) else "—").rjust(10) for q in CASES))
            result["cases"][f"{gate}.{rule}"] = row

    log("\n읽는 법")
    log("  · 「매치/단독추가/게이트제외」 = 게이트 통과 매치 총수 / 벡터 컷 통과자에 없어서")
    log("    문자열이 단독으로 추가한 수 / 부분일치인데 이 게이트가 제외한 기대 정답 수.")
    log("  · 무관·타인소유 세그먼트는 zero_rate 가 baseline 에서 떨어지면 그 규칙이 관련 없는")
    log("    결과를 노출시킨 것이다 — 기각 사유.")
    log("  · 컷은 현행 그대로다. 문자열 후보의 자격은 게이트가 정한다.")

    if args.json:
        out = Path(args.json)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")
        log(f"\n저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
