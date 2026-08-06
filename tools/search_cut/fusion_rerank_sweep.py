"""Keyword 재정렬 전용 병합 비교 — P49 §8 작업 4 (`S15P11A705-339`).

**DB 도 GMS 도 부르지 않는다.** `keyword_matrix.py` 가 만든 artifact 와 기존 벡터
행렬만 읽는다. `fusion_sweep.py` 와 다른 병합 의미를 잰다 — 저쪽은 P48 구조(후보
합집합 후 병합 점수에 컷)이고, 이쪽은 P49 §4 가 확정한 구조(현행 컷이 후보를 먼저
확정하고 keyword 신호는 그 순서만 조정)다. I53 의 채택값(floor 0.35 · binary w=0.05)은
P48 구조의 관측이라 이 구조에서는 재측정해야 한다(P49 §9).

    python tools/search_cut/fusion_rerank_sweep.py
    python tools/search_cut/fusion_rerank_sweep.py --json .search/fusion_rerank_sweep.json

## 비교 범위 — 티켓이 고정했다

    BASE      keyword 신호 없음. 현행 컷 통과 집합 그대로 (현행 검색과 같다)
    binary    맞으면 고정 보너스 — 정렬 점수 = 코사인 + weight, floor·weight 격자
    rrf       벡터 순위와 keyword 순위의 역수 합 (k=60)

confidence·IDF 는 재개방하지 않는다 — P48 실측(I53)에서 주 채택 근거가 아니었다.

## 이 구조에서 무관 노출은 구조적 불변이다

재정렬은 컷 통과 집합 안에서만 움직이므로, 관련 없는 질의에서 벡터 후보가 0건이면
재정렬할 대상도 0건이다(P49 §5). 그래서 무관 세그먼트는 지표가 BASE 와 완전히 같은지
**확인만** 하고, floor·weight 는 노출 방어가 아니라 순위 품질의 축으로 읽는다.

후보 집합 불변도 같은 이유로 질의마다 검사한다 — 재정렬 전후의 Record id 집합이
다르면 수치를 내지 않고 멈춘다. 런타임 쪽은 같은 계약을 on/off 계약 테스트가 고정한다.

## artifact 신선도 가드

행렬·keyword artifact 가 현행 환경과 어긋나면 **수치를 내지 않고 실패한다**
(`fusion_sweep.load` 의 가드 + 이 스크립트의 추가 가드).

    profile 불일치           행렬 간 불일치(기존) + 현행 profile 과 불일치(추가)
    preset_version 불일치    keyword artifact 의 판이 현행 판이 아니면 실측이 무효다
    데이터셋 불일치          record_count(기존) · 소유자 집합(추가)

현행 값의 정본은 `app/core/config.py`(profile)와 시연 DB(preset_version)다. 하네스가
앱을 import 하지 않는 것은 기존 sweep 과 같으므로(rank_score.py 머리말) 여기 상수로
비추고, 값이 갈리면 --expect-* 인자로 덮어쓴다 — 출력에 실제 사용값을 적는다.

## 결정성

입력이 파일뿐이고 난수도 시각도 쓰지 않으므로 같은 입력·같은 인자에 같은 출력이다.
`--json` 은 입력 SHA-256 을 함께 적는다 — 두 번 돌려 파일이 같은지로 회차를 확인한다
(T68 절차의 오프라인 축소판. 임베딩 API 비결정성 재측정은 artifact 재생성이 필요해
이 티켓 범위 밖이다).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fusion as F  # noqa: E402
from fusion_sweep import build_index, load  # noqa: E402
from rank_score import (  # noqa: E402
    RATIO,
    SERVICE_LIMIT,
    TAU_ABS,
    TAU_ABS_WORD,
    GuardError,
    _sha256,
    aggregate,
    cut,
    metrics_for,
    zero_rate,
)

ROOT = Path(__file__).resolve().parents[2]
SEARCH = ROOT / ".search"

# cp949 콘솔에서 `—` 한 글자에 죽지 않게 한다(T28·T77) — 기존 sweep 과 같은 방어다.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

CASES = ("신한", "부캠", "그네", "스팟")

# 현행 환경의 거울값. profile 정본은 `app/core/config.py` 의 기본값이고,
# preset_version 정본은 시연 DB(`ai.keyword_preset.version`)다 — 27건 v1(I53 측정 조건).
CURRENT_PROFILE = "openai-text-embedding-3-small-1536-cosine-v1"
CURRENT_PRESET_VERSION = 1


def log(msg: str = "") -> None:
    print(msg, flush=True)


# ── 신선도 가드 (기존 load 가드 위에 얹는다) ─────────────────────────────────

def check_freshness(
    data: dict, kw: dict, *, expect_profile: str, expect_preset_version: int
) -> None:
    """artifact 가 현행 환경의 것인지 확인한다. 어긋나면 수치를 내지 않는다."""
    if kw.get("profile") != expect_profile:
        raise GuardError(
            f"keyword artifact 의 profile 이 현행과 다르다: "
            f"{kw.get('profile')} ≠ {expect_profile}\n"
            "  Profile 이 바뀌었으면 artifact 전량을 다시 떠야 한다(GMS 비용) — "
            "이 티켓 범위 밖이므로 중앙에 보고한다."
        )
    if kw.get("preset_version") != expect_preset_version:
        raise GuardError(
            f"keyword artifact 의 preset_version 이 현행과 다르다: "
            f"{kw.get('preset_version')} ≠ {expect_preset_version}\n"
            "  Preset 이 개정됐으면 keyword_matrix.py 를 다시 돌린다(GMS 배치 1회)."
        )
    stale = sorted(
        p["id"] for p in kw.get("presets", [])
        if p.get("version") != expect_preset_version
    )
    if stale:
        raise GuardError(
            f"keyword artifact 안에 preset_version ≠ {expect_preset_version} 인 "
            f"Preset 이 있다: {stale} — 판이 섞였다. keyword_matrix.py 를 다시 돌린다."
        )
    # 데이터셋 식별 — 행렬 질의가 참조하는 소유자가 keyword artifact 에 전부 있어야
    # 한다. 없으면 그 질의의 신호가 조용히 0 이 된다(재시딩으로 user_id 가 바뀐 경우).
    # record_count 는 load() 의 기존 가드가 본다.
    kw_owners = {c["user_id"] for c in kw.get("contexts", [])}
    referenced: set[int] = set()
    for name in ("matrix", "word_grid"):
        for sec in ("queries", "offtopic", "cross"):
            for e in data[name].get(sec) or []:
                if e.get("user_id") is not None:
                    referenced.add(e["user_id"])
    if data["recall_probe"].get("user_id") is not None:
        referenced.add(data["recall_probe"]["user_id"])
    missing = sorted(referenced - kw_owners)
    if missing:
        raise GuardError(
            f"행렬이 참조하는 user_id 가 keyword artifact 에 없다: {missing}\n"
            "  재시딩으로 데이터셋이 바뀌었다 — keyword_matrix.py 를 다시 돌린다."
        )


# ── 한 조합 평가 ─────────────────────────────────────────────────────────────

def rerank_one(
    e: dict, *, presets, by_user, query_cos,
    method: str | None, weight: float, floor: float, top_k: int, rrf_k: float,
    cut_kw: dict,
) -> tuple[list[dict], int, bool]:
    """질의 하나를 재정렬하고 (결과 행, 컷 통과 수, 신호 반영 여부)를 돌려준다.

    후보 집합 불변을 여기서 검사한다 — 재정렬 전후 Record id 집합이 다르면
    `FusionError` 가 나고 상위에서 가드 실패로 처리된다(rerank 내부 검사).
    """
    q, rows = e["query"], (e.get("results") or [])
    kept = cut(rows, q, **cut_kw)
    if method is None:  # BASE
        return kept, len(kept), False

    qc = query_cos.get(q)
    if qc is None:
        raise GuardError(f"keyword artifact 에 질의가 없다: `{q}` — 다시 뜬다")
    cand = F.preset_candidates(qc, presets, top_k=top_k, floor=floor)
    uid = e.get("user_id")
    if uid is None:
        raise GuardError(f"질의 `{q}` 에 user_id 가 없다 — 신호를 조인할 수 없다")
    sig = F.record_signals(
        by_user.get(uid, []), cand, presets,
        method=method, null_policy=F.NULL_INCLUDE,
    )
    reranked = F.rerank(kept, sig, method=method, weight=weight, rrf_k=rrf_k)

    kept_ids = {r["record_id"] for r in kept}
    if {r["record_id"] for r in reranked} != kept_ids:
        raise GuardError(f"질의 `{q}`: 재정렬이 후보 집합을 바꿨다 — 구현 오류")
    signal_applied = any(r["keyword_signal"] > 0 for r in reranked)
    return reranked, len(kept), signal_applied


def evaluate(
    entries: list[dict], **kw
) -> tuple[dict, int]:
    """세그먼트 하나를 재고 (지표, 신호가 반영된 질의 수)를 돌려준다."""
    per, applied = [], 0
    for e in entries:
        rows = e.get("results") or []
        n_rel = sum(1 for r in rows if r.get("is_expected"))
        final, _, signal_applied = rerank_one(e, **kw)
        if signal_applied:
            applied += 1
        per.append(metrics_for(final, n_rel))
    agg = aggregate(per)
    if agg:
        agg["zero_rate"] = zero_rate(per)
    return agg, applied


def case_ranks(entries: list[dict], **kw) -> dict:
    out = {}
    for e in entries:
        q = e["query"]
        if q not in CASES:
            continue
        final, returned, _ = rerank_one(e, **kw)
        hit = next((i for i, r in enumerate(final, 1) if r.get("is_expected")), None)
        out[q] = {"rank": hit, "returned": returned}
    return out


# ── 실행 ─────────────────────────────────────────────────────────────────────

def parse_floats(spec: str) -> list[float]:
    return [float(x) for x in spec.split(",") if x.strip()]


COLS = ("n", "hit@1", "hit@3", "mrr", "recall@3", "ndcg@3", "zero_rate", "returned")


def row_str(label: str, m: dict, extra: str = "") -> str:
    if not m:
        return f"  {label:<18} (0건)"
    cells = "".join(
        (str(m[c]).rjust(9) if c == "n" else f"{m[c]:.4f}".rjust(9)) for c in COLS
    )
    return f"  {label:<18}{cells}  {extra}"


def build_grid(weights: list[float]) -> list[tuple[str, str | None, float]]:
    """(label, method|None, weight). BASE → binary 격자 → RRF 순서다."""
    rows: list[tuple[str, str | None, float]] = [("BASE(신호 없음)", None, 0.0)]
    rows += [(f"binary w={w:g}", F.BINARY, w) for w in weights]
    rows.append(("rrf", F.RRF, 0.0))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Keyword 재정렬 전용 병합 비교 (P49 작업 4)"
    )
    ap.add_argument("--keyword-matrix", default=str(SEARCH / "keyword_matrix.json"))
    ap.add_argument("--search-dir", default=str(SEARCH),
                    help="행렬 디렉터리. 기본은 .search/ — 픽스처 검증용 인자다")
    ap.add_argument("--json")
    ap.add_argument("--top-k", type=int, default=3)
    ap.add_argument("--floors", default="0.25,0.30,0.32,0.35,0.40",
                    help="query→Preset 코사인 하한 격자 (쉼표 구분)")
    ap.add_argument("--weights", default="0.02,0.05,0.10,0.20",
                    help="binary 가중치 격자 (쉼표 구분)")
    ap.add_argument("--rrf-k", type=float, default=60.0)
    ap.add_argument("--tau", type=float, default=TAU_ABS)
    ap.add_argument("--tau-word", type=float, default=TAU_ABS_WORD)
    ap.add_argument("--ratio", type=float, default=RATIO)
    ap.add_argument("--limit", type=int, default=SERVICE_LIMIT)
    ap.add_argument("--expect-profile", default=CURRENT_PROFILE)
    ap.add_argument("--expect-preset-version", type=int,
                    default=CURRENT_PRESET_VERSION)
    args = ap.parse_args()

    kw_path = Path(args.keyword_matrix)
    search_dir = Path(args.search_dir)
    try:
        data, kw = load(kw_path, search_dir)
        check_freshness(
            data, kw,
            expect_profile=args.expect_profile,
            expect_preset_version=args.expect_preset_version,
        )
        presets, by_user, query_cos = build_index(kw)
    except GuardError as exc:
        print(f"[가드] {exc}", file=sys.stderr)
        return 1

    floors = parse_floats(args.floors)
    weights = parse_floats(args.weights)
    grid = build_grid(weights)
    cut_kw = dict(tau=args.tau, tau_word=args.tau_word,
                  ratio=args.ratio, limit=args.limit)

    segs = {
        "문장형(정답)": data["matrix"]["queries"],
        "단어형(정답)": data["word_grid"]["queries"],
        "무관-문장형": data["matrix"]["offtopic"],
        "무관-단어형": data["word_grid"]["offtopic"],
    }

    log("=" * 100)
    log("Keyword 재정렬 전용 병합 비교 — P49 작업 4")
    log("=" * 100)
    log(f"  Profile         {kw['profile']} (현행 일치 확인)")
    log(f"  Preset          {kw['preset_count']}건 · version={kw['preset_version']}"
        " (현행 일치 확인)")
    log("  병합 의미       컷 통과 집합 고정 · 순서만 조정 (P49 §4)")
    log(f"  query→Preset    top_k={args.top_k} · floor 격자={args.floors}")
    log(f"  binary weight   격자={args.weights}")
    log(f"  RRF             k={args.rrf_k} · 2차 절단 없음")
    log(f"  컷              tau={args.tau} · tau_word={args.tau_word} · r={args.ratio}"
        f" · limit={args.limit}")
    log("  호출            DB 0회 · GMS 0회")

    result: dict = {
        "stage": "P49-rerank",
        "params": {k: v for k, v in vars(args).items() if k != "json"},
        "inputs": {
            **{k: _sha256(search_dir / f"{k}.json") for k in ("matrix", "word_grid")},
            "keyword_matrix": _sha256(kw_path),
        },
        "grid": {},
        "cases": {},
    }

    # recall_probe 사례 행 준비 (fusion_sweep 과 같은 정규화)
    probe_uid = data["recall_probe"].get("user_id")
    probe_entries = [
        dict(e,
             user_id=e.get("user_id", probe_uid),
             results=[dict(r, is_expected=(r.get("name") == e.get("expect")))
                      for r in (e.get("results") or [])])
        for e in data["recall_probe"]["queries"]
    ]

    invariance_checked = 0
    base_metrics: dict[str, dict] = {}

    for floor in floors:
        log(f"\n─── floor={floor:g} " + "─" * 80)
        for seg_name, entries in segs.items():
            log(f"\n{seg_name}")
            log("  " + "방식".ljust(18) + "".join(c.rjust(9) for c in COLS)
                + "  신호 반영 질의")
            for label, method, weight in grid:
                if method is None and floor != floors[0]:
                    continue  # BASE 는 floor 와 무관 — 첫 격자에서 한 번만
                try:
                    m, applied = evaluate(
                        entries, presets=presets, by_user=by_user,
                        query_cos=query_cos, method=method, weight=weight,
                        floor=floor, top_k=args.top_k, rrf_k=args.rrf_k,
                        cut_kw=cut_kw,
                    )
                except (GuardError, F.FusionError) as exc:
                    print(f"[가드] {exc}", file=sys.stderr)
                    return 1
                invariance_checked += len(entries) if method is not None else 0
                note = f"{applied}건" if method is not None else "(현행과 동일)"
                log(row_str(label, m, note))
                key = "BASE" if method is None else f"floor={floor:g}·{label}"
                if method is None:
                    base_metrics[seg_name] = m
                    result["grid"].setdefault(seg_name, {})["BASE"] = {
                        "metrics": m, "applied": 0,
                    }
                else:
                    result["grid"].setdefault(seg_name, {})[key] = {
                        "metrics": m, "applied": applied,
                        "method": method, "weight": weight, "floor": floor,
                        "rrf_k": args.rrf_k if method == F.RRF else None,
                    }
                    # 무관 세그먼트 구조적 불변 확인 — BASE 와 다르면 구현 오류다.
                    if seg_name.startswith("무관") and m != base_metrics[seg_name]:
                        print(f"[가드] {seg_name} `{key}` 지표가 BASE 와 다르다 — "
                              "재정렬이 무관 노출을 바꿨다. 구현 오류.", file=sys.stderr)
                        return 1

        log("\n사례별 (컷 후 정답 순위 · `—` 는 잘림)")
        log("  " + "방식".ljust(18) + "".join(q.rjust(10) for q in CASES))
        for label, method, weight in grid:
            if method is None and floor != floors[0]:
                continue
            try:
                ranks = case_ranks(
                    probe_entries, presets=presets, by_user=by_user,
                    query_cos=query_cos, method=method, weight=weight,
                    floor=floor, top_k=args.top_k, rrf_k=args.rrf_k, cut_kw=cut_kw,
                )
            except (GuardError, F.FusionError) as exc:
                print(f"[가드] {exc}", file=sys.stderr)
                return 1
            cells = "".join(
                (str(ranks[q]["rank"]) if ranks.get(q, {}).get("rank") else "—").rjust(10)
                for q in CASES
            )
            log(f"  {label:<18}{cells}")
            key = "BASE" if method is None else f"floor={floor:g}·{label}"
            result["cases"][key] = ranks

    log(f"\n후보 집합 불변   재정렬 {invariance_checked}회 전부 전후 Record id 집합 일치"
        " (어긋나면 위에서 이미 멈췄다)")
    log("\n읽는 법")
    log("  · BASE 는 현행 컷 통과 집합 그대로다 — 모든 방식과 같은 자(rank_score)로 쟀다.")
    log("  · 무관 세그먼트는 구조적으로 BASE 와 같아야 한다(재정렬은 후보를 못 바꾼다).")
    log("    다르면 이 스크립트가 멈춘다 — 확인용이지 조절 축이 아니다.")
    log("  · floor·weight 는 순위 품질의 축이다. 인접 값의 차이가 10⁻⁴ 규모(임베딩 API")
    log("    흔들림, T68)라면 그 차이로 채택을 가르지 않는다.")
    log("  · 「신호 반영 질의」 = keyword 신호가 0 이 아닌 행이 하나라도 있던 질의 수.")

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        log(f"\n저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
