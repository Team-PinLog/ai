"""결합 신뢰도 게이트 임계값 오프라인 재측정 — S15P11A705-401.

배경은 `OFFTOPIC-CONFIDENCE-GATE-HANDOFF-DRAFT.md`(중앙 조정 세션 인계 문서) §4다. 그
문서는 결과를 아예 숨길지 정하는 새 게이트를 제안한다 — 신호가 S1(벡터 컷 통과) 하나뿐이고
그 유사도가 임계값 미만이면 응답에서 뺀다. **임계값 후보로 0.35 를 들었지만, 그 값은 이
용도로 측정된 적이 없다.** 0.35 의 원출처는 `SEARCH_KEYWORD_RERANK_FLOOR`
(`app/core/config.py`) — 키워드 재정렬에서 "질의와 Preset 이 같은 의미인지" 판단하는
값이다. 그 값이 재정렬 전용 구조로 바뀌었을 때도 이 레포는 그대로 재사용하지 않고
`fusion_rerank_sweep.py`(P49 작업 4)로 **다시 쟀다** — 우연히 같은 숫자가 다시 채택됐을
뿐, 그것은 다른 측정의 결과였다(`2026-08-06-rerank-adoption.md`). 이 스크립트는 그
선례를 따른다: "벡터 신호 단독일 때 그것이 약한지"는 "질의-Preset 코사인이 같은
의미인지"와 다른 질문이므로, 같은 값을 가정하지 않고 이 용도로 다시 잰다.

**DB 도 GMS 도 부르지 않는다.** 기존에 커밋된 행렬만 읽는다.

    matrix.json          문장형 정답 12건 · 무관 15건 (S15P11A705-213)
    word_grid.json        단어형 정답 66건 · 무관 45건 · 타인소유 96건 (S15P11A705-266)
    recall_probe.json     진단 프로브 (S15P11A705-255)
    lexical_matrix.json   S2(문자열 매치) 원본 — 채택 게이트 G1(단어형 한정·부분일치, I54)
    keyword_matrix.json   S3(키워드 매치) 원본 — 채택값 binary·floor 0.35·weight 0.05·
                           top_k 3 (I57, `app/core/config.py` 정본)

## 게이트 규칙

컷을 통과한 후보마다 S1(항상 참) · S2(문자열 매치) · S3(키워드 매치)를 센다. **S2 도 S3
도 없고** 유사도가 `threshold` 미만이면 그 후보를 뺀다. 하나라도 있으면 유사도와 무관하게
남긴다 — 이 스크립트가 답하는 것은 그 `threshold` 값이다.

이 게이트는 컷·재정렬과 **독립된 마지막 단계**다(OFFTOPIC 문서 §4.1 — 기존 계약
"재정렬은 후보를 추가·제거하지 않는다"는 재정렬 자신의 계약이고 이 게이트의 계약이
아니다). 그래서 여기서는 재정렬의 순서 결과를 쓰지 않고 신호의 **존재 여부**만 쓴다.

    python tools/search_cut/gate_sweep.py
    python tools/search_cut/gate_sweep.py --threshold 0.30,0.35,0.40
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fusion as F  # noqa: E402
from rank_score import (  # noqa: E402
    SERVICE_LIMIT,
    GuardError,
    cut,
    is_word_query,
)

ROOT = Path(__file__).resolve().parents[2]
SEARCH = ROOT / ".search"

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def log(msg: str = "") -> None:
    print(msg, flush=True)


def head(title: str) -> None:
    log("\n" + "=" * 100)
    log(title)
    log("=" * 100)


# 채택 재정렬 파라미터 — S3 신호를 재현하는 데 쓴다. 정본은 `app/core/config.py`,
# 실측 근거는 `docs/implements/2026-08-06-rerank-adoption.md`(I57).
RERANK_METHOD = F.BINARY
RERANK_FLOOR = 0.35
RERANK_TOP_K = 3

# 채택 문자열 게이트 — S2 신호를 재현하는 데 쓴다(I54: 단어형 한정 · 부분일치).
LEXICAL_GATE = "G1"

# 이 티켓이 훑는 임계값 후보. 0.35 를 격자 한가운데 두고 촘촘히(0.01) 잡는다 —
# `word_sweep.py` 가 τ_abs 를 잡은 것과 같은 간격 선택 이유다(채택 후보가 데이터점
# 하나에 붙을 수 있다).
THRESHOLDS = [0.0] + [round(0.16 + 0.01 * i, 3) for i in range(25)]


# ------------------------------------------------------------------------- 적재


def load(search_dir: Path = SEARCH):
    paths = {
        "matrix": search_dir / "matrix.json",
        "word_grid": search_dir / "word_grid.json",
        "recall_probe": search_dir / "recall_probe.json",
        "lexical": search_dir / "lexical_matrix.json",
        "keyword": search_dir / "keyword_matrix.json",
    }
    for k, p in paths.items():
        if not p.exists():
            raise GuardError(
                f"{p.relative_to(ROOT)} 가 없다. README(tools/search_cut/README.md)의 "
                "해당 matrix 스크립트를 먼저 돌려라."
            )
    data = {k: json.loads(p.read_text(encoding="utf-8")) for k, p in paths.items()}

    profiles = {k: d.get("profile") for k, d in data.items()}
    if len(set(profiles.values())) != 1 or None in profiles.values():
        raise GuardError(f"Profile 이 어긋난다 — 한 표에 놓을 수 없다: {profiles}")

    lex = {e["query"]: e["matches"] for e in data["lexical"]["queries"]}

    kw = data["keyword"]
    presets = {
        p["id"]: F.Preset(id=p["id"], version=p["version"], visibility=p["visibility"])
        for p in kw["presets"]
    }
    contexts = [
        F.ContextKeywords(
            context_id=c["context_id"],
            record_id=c["record_id"],
            keyword_status=c["keyword_status"],
            keywords=tuple((k["keyword_id"], k["confidence"]) for k in c["keywords"]),
        )
        for c in kw["contexts"]
    ]
    by_user: dict[int, list] = {}
    for c, raw in zip(contexts, kw["contexts"]):
        by_user.setdefault(raw["user_id"], []).append(c)
    query_cos = {
        e["query"]: {c["preset_id"]: c["cos"] for c in e["cos"]}
        for e in kw["query_preset"]
    }

    # recall_probe 는 `is_expected` 가 없다 — `expect`(장소명)로 합성한다(rank_score.py
    # `_probe_segment` 와 같은 규칙).
    for e in data["recall_probe"]["queries"]:
        want = e.get("expect")
        for r in e.get("results") or []:
            r["is_expected"] = r.get("name") == want

    return data, lex, presets, by_user, query_cos


# --------------------------------------------------------------------------- 신호


def lexical_ids(lex: dict, query: str, uid, gate: str = LEXICAL_GATE) -> set[int]:
    """S2 — 채택 문자열 게이트(G1)를 통과한 매치 Record id."""
    if gate in ("G1", "G2") and not is_word_query(query):
        return set()
    entries = (lex.get(query) or {}).get(str(uid)) or []
    if gate == "G2":
        entries = [e for e in entries if e["boundary"]]
    return {e["record_id"] for e in entries}


def keyword_ids(query_cos: dict, presets: dict, by_user: dict, query: str, uid) -> set[int]:
    """S3 — 채택 재정렬 파라미터로 신호가 있는(> 0) Record id."""
    qc = query_cos.get(query)
    if qc is None or uid not in by_user:
        return set()
    cand = F.preset_candidates(qc, presets, top_k=RERANK_TOP_K, floor=RERANK_FLOOR)
    sig = F.record_signals(by_user.get(uid, []), cand, presets, method=RERANK_METHOD)
    return {rid for rid, s in sig.items() if s > 0}


def gate(
    rows: list[dict], query: str, uid, threshold: float, *,
    lex: dict, query_cos: dict, presets: dict, by_user: dict, limit: int = SERVICE_LIMIT,
) -> list[dict]:
    """S1/S2/S3 를 세어 게이트를 적용한다(OFFTOPIC 문서 §4.3).

    S2·S3 가 하나도 없고 S1 유사도가 `threshold` 미만이면 뺀다. `threshold=0.0` 은
    게이트가 없는 현재 동작과 같다(모든 것이 통과).
    """
    kept = cut(rows, query, limit=limit)
    if not kept:
        return kept
    lex_ids = lexical_ids(lex, query, uid)
    kw_ids = keyword_ids(query_cos, presets, by_user, query, uid)
    out = []
    for r in kept:
        rid = r["record_id"]
        if rid not in lex_ids and rid not in kw_ids and float(r["sim"]) < threshold:
            continue
        out.append(r)
    return out


# --------------------------------------------------------------------------- 평가


def eval_answerable(entries: list[dict], threshold: float, ctx: dict, default_uid=None) -> dict:
    """기대 정답이 있는 질의 집합. 게이트가 정답까지 지웠는지를 센다."""
    miss_all = miss_partial = lost_top1 = 0
    lost: list[dict] = []
    for e in entries:
        q, uid = e["query"], e.get("user_id", default_uid)
        rows = e.get("results") or []
        base = cut(rows, q, limit=SERVICE_LIMIT)
        want = [r for r in base if r.get("is_expected")]
        if not want:
            continue
        gated = gate(rows, q, uid, threshold, **ctx)
        gated_ids = {r["record_id"] for r in gated}
        top1 = next((r for r in want if r.get("rank") == 1), None)
        if top1 and top1["record_id"] not in gated_ids:
            lost_top1 += 1
        alive = [r for r in want if r["record_id"] in gated_ids]
        if not alive:
            miss_all += 1
            lost.append({"query": q, "sim": max(r["sim"] for r in want)})
        elif len(alive) < len(want):
            miss_partial += 1
    return {"n": len(entries), "miss_all": miss_all, "miss_partial": miss_partial,
            "lost_top1": lost_top1, "lost": lost}


def eval_control(entries: list[dict], threshold: float, ctx: dict, default_uid=None) -> dict:
    """정답이 없는 질의 집합. 게이트를 더 걸수록 침묵(0건)이 늘어야 개선이다."""
    before_silenced = after_silenced = 0
    for e in entries:
        q, uid = e["query"], e.get("user_id", default_uid)
        rows = e.get("results") or []
        if not cut(rows, q, limit=SERVICE_LIMIT):
            before_silenced += 1
        if not gate(rows, q, uid, threshold, **ctx):
            after_silenced += 1
    n = len(entries)
    return {"n": n, "before": before_silenced, "after": after_silenced,
            "gained": after_silenced - before_silenced}


def table(title: str, rows: list[dict]) -> None:
    head(title)
    log(f"  {'threshold':>9} │ {'문장정답손실':>10} {'단어정답손실':>10} {'프로브손실':>8} "
        f"{'1위손실(문/단)':>14} │ {'무관-문장 신규침묵':>16} {'무관-단어 신규침묵':>16} "
        f"{'타인소유 신규침묵':>16}")
    log("  " + "-" * 120)
    for r in rows:
        sa, wa, pa = r["sentence_answer"], r["word_answer"], r["probe"]
        so, wo, co = r["sentence_offtopic"], r["word_offtopic"], r["cross"]
        top1 = f"{sa['lost_top1']}/{wa['lost_top1']}"
        log(f"  {r['threshold']:>9.3f} │ {sa['miss_all']:>6}/{sa['n']:<3} "
            f"{wa['miss_all']:>6}/{wa['n']:<3} {pa['miss_all']:>4}/{pa['n']:<3} "
            f"{top1:>14} │ {so['gained']:>10}/{so['n']:<3} {wo['gained']:>10}/{wo['n']:<3} "
            f"{co['gained']:>10}/{co['n']:<3}")


def main() -> int:
    ap = argparse.ArgumentParser(description="결합 신뢰도 게이트 임계값 재측정 (S15P11A705-401)")
    ap.add_argument("--threshold", default="", help="비우면 0.0 및 0.16~0.40 (0.01 간격)")
    ap.add_argument("--out", default=str(SEARCH / "gate_sweep.json"))
    args = ap.parse_args()

    try:
        data, lex, presets, by_user, query_cos = load()
    except GuardError as exc:
        print(f"[가드] {exc}", file=sys.stderr)
        return 1

    ctx = {"lex": lex, "query_cos": query_cos, "presets": presets, "by_user": by_user}

    thresholds = ([float(x) for x in args.threshold.split(",") if x.strip()] or THRESHOLDS)

    head("입력")
    log(f"  문장형   정답 {data['matrix']['query_count']}건 · 무관 "
        f"{data['matrix']['offtopic_count']}건")
    log(f"  단어형   정답 {data['word_grid']['word_count']}건 · 무관 "
        f"{data['word_grid']['offtopic_count']}건 · 타인소유 {data['word_grid']['cross_count']}건")
    log(f"  진단프로브  {len(data['recall_probe']['queries'])}건")
    log(f"  S2 게이트  {LEXICAL_GATE}(단어형 한정 · 부분일치, I54 채택값)")
    log(f"  S3 파라미터  {RERANK_METHOD} · floor={RERANK_FLOOR} · top_k={RERANK_TOP_K} "
        "(I57 채택값, app/core/config.py)")
    log("  호출     DB 0회 · GMS 0회")

    probe_uid = data["recall_probe"].get("user_id")

    rows = []
    for t in thresholds:
        rows.append({
            "threshold": t,
            "sentence_answer": eval_answerable(data["matrix"]["queries"], t, ctx),
            "word_answer": eval_answerable(data["word_grid"]["queries"], t, ctx),
            "probe": eval_answerable(data["recall_probe"]["queries"], t, ctx,
                                      default_uid=probe_uid),
            "sentence_offtopic": eval_control(data["matrix"]["offtopic"], t, ctx),
            "word_offtopic": eval_control(data["word_grid"]["offtopic"], t, ctx),
            "cross": eval_control(data["word_grid"]["cross"], t, ctx),
        })

    table("threshold 격자 — 정답 손실 대 무관 신규 침묵", rows)

    head("현재 후보값 0.35 에서 잃는 정답")
    at_035 = next((r for r in rows if abs(r["threshold"] - 0.35) < 1e-9), None)
    if at_035 is None:
        log("  격자에 0.35 가 없다 — --threshold 로 추가해서 다시 돌려라.")
    else:
        for label, seg in (("문장형", at_035["sentence_answer"]), ("단어형", at_035["word_answer"]),
                           ("프로브", at_035["probe"])):
            if seg["lost"]:
                log(f"  {label}: " + "; ".join(
                    f"「{d['query']}」(sim={d['sim']:.4f})" for d in seg["lost"]))
            else:
                log(f"  {label}: 손실 없음")

    # 정답 손실이 전혀 없는(문장형·단어형·프로브 miss_all=0, lost_top1=0) 임계값만
    # 후보로 남긴다 — word_sweep.py 의 「safe」와 같은 원칙이다. 그중 무관/타인소유
    # 신규 침묵이 가장 큰 값을 위로 올린다.
    safe = [
        r for r in rows
        if r["sentence_answer"]["miss_all"] == 0 and r["sentence_answer"]["lost_top1"] == 0
        and r["word_answer"]["miss_all"] == 0 and r["word_answer"]["lost_top1"] == 0
        and r["probe"]["miss_all"] == 0
    ]
    head(f"정답 손실 0 인 threshold: {len(safe)}/{len(rows)}")
    if safe:
        ranked = sorted(
            safe,
            key=lambda r: -(r["sentence_offtopic"]["gained"] + r["word_offtopic"]["gained"]
                            + r["cross"]["gained"]),
        )
        table("정답 손실 0 후보 — 무관/타인소유 신규 침묵 많은 순", ranked)
        best = ranked[0]
        log(f"\n  이 데이터에서 정답 손실 없이 가장 많이 침묵시키는 threshold: "
            f"{best['threshold']}")
        def _silenced(r: dict) -> int:
            return (r["sentence_offtopic"]["gained"] + r["word_offtopic"]["gained"]
                    + r["cross"]["gained"])

        if abs(best["threshold"] - 0.35) > 1e-9:
            gap = (_silenced(best) - _silenced(at_035)) if at_035 else "?"
            log(f"  → 0.35 와 다르다. 0.35 를 그대로 채택하면 이 데이터 기준으로 "
                f"{gap}건만큼 침묵 기회를 덜 쓰는 것이다"
                "(안전한 방향이지만 최적은 아니다).")
        else:
            log("  → 0.35 가 이 데이터에서도 최적이다. 재사용을 채택해도 된다.")
    else:
        log("  정답 손실이 0 인 threshold 가 격자에 없다 — 이 게이트 설계 자체를")
        log("  재검토해야 한다(모든 임계값이 어떤 정답을 희생시킨다).")

    log("\n주의")
    log("  · Record 42건 · 소유자 3명 규모다. 절대 채택값이 아니라 **방향과 상대 비교**로")
    log("    읽는다(rank_score.py 의 같은 경고와 원칙이 같다).")
    log("  · 유사도 값 자체의 회차 간 흔들림은 재측정하지 않았다 — 기존 실측(T68,")
    log("    |Δsim| 최대 0.0044)을 그대로 인용한다. 이 스크립트는 그 흔들림 폭보다")
    log("    좁은 임계값 비교(0.01 간격)에 대해서는 인접 값 결론을 보류해야 한다.")
    log("  · S2·S3 는 이 스크립트가 채택 파라미터로 재구성한 값이다 — 실서버 게이트")
    log("    구현(S15P11A705-400) 이후에는 실서버 대조로 한 번 더 검증해야 한다.")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"ticket": "S15P11A705-401", "lexical_gate": LEXICAL_GATE,
         "rerank_params": {"method": RERANK_METHOD, "floor": RERANK_FLOOR,
                            "top_k": RERANK_TOP_K},
         "grid": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"\n  → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
