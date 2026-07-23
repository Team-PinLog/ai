"""테스트 C — LLM 판정 (2단계).

C-1 프롬프트 안정화: 모델 1개로 스키마 위반·과잉/과소 선택을 잡아 프롬프트 확정.
C-2 모델 비교:      확정 프롬프트로 여러 모델을 돌려 판정 모델을 근거 있게 확정.

판정 모델은 계약상 미확정(GMS 목록: GPT-5.x/Gemini/Claude). 태스크가 '후보에서
고르기'라 구조화 출력 준수가 핵심 → 경량 tier로 충분한지 먼저 본다.

각 샘플에 대해 (1) 임베딩으로 top-K 후보를 뽑고 (2) prompts/keyword_judgment.md
프롬프트로 구조화 출력을 요청해 (3) 후보 밖 id 폐기 후 지표를 집계한다.

Provider:
  anthropic  : Claude Messages API (tool_use).  env ANTHROPIC_API_KEY/ANTHROPIC_BASE_URL
  openai     : OpenAI 호환 chat/completions (tools).  env GMS_API_KEY/GMS_BASE_URL (GMS 경로)

사용:
  python test_c_judge.py --provider anthropic --model claude-haiku-4-5   # C-1
  python test_c_judge.py --provider openai    --model gpt-5-mini         # C-2 (GMS)
  python test_c_judge.py --compare            # 아래 MODELS 목록 일괄 비교
"""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import numpy as np
import yaml

from embed import cosine_matrix, config, embed, load_presets, preset_embed_text

HERE = Path(__file__).resolve().parent

# C-2 비교 대상 (경량 tier 우선). GMS에서 쓸 수 있는 실제 모델명으로 조정.
MODELS = [
    ("anthropic", "claude-haiku-4-5"),
    ("openai", "gpt-5-mini"),
    ("openai", "gemini-2.5-flash"),  # GMS가 OpenAI 호환으로 노출하는 경우
]

SYSTEM = (
    "당신은 장소 기록 서비스의 Keyword 분류기입니다.\n"
    "사용자가 장소를 저장한 이유를 적은 짧은 글(Context)과 후보 Keyword 목록이 주어집니다.\n"
    "후보 목록에서 이 Context에 실제로 들어맞는 Keyword만 고르세요.\n"
    "규칙:\n"
    "- 반드시 후보 목록의 keyword_id 중에서만 고릅니다. 목록에 없는 것을 만들지 마세요.\n"
    "- 글에서 근거를 찾을 수 있는 것만 고릅니다. 그럴듯하다는 이유로 넣지 마세요.\n"
    "- 하나도 맞지 않으면 빈 목록을 반환합니다. 억지로 채우지 마세요.\n"
    "- 보통 0~3개입니다. 많이 고를수록 정확도가 떨어집니다.\n"
    "- description은 의미 범위, examples는 실제 말투 예시입니다. 둘 다 참고하세요.\n"
    "- confidence는 근거의 확실함을 0~1로 나타냅니다. 애매하면 낮게 줍니다."
)


def build_user(context_text: str, cands: list[dict]) -> str:
    lines = []
    for p in cands:
        ex = " · ".join(p.get("examples", []))
        lines.append(
            f"- id={p['id']} | {p['display_name']} ({p['category']}) | 의미: {p['description']} | 예: {ex}"
        )
    return f"[Context]\n{context_text}\n\n[후보 Keyword]\n" + "\n".join(lines)


def _schema(cand_ids: list[int]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["selected"],
        "properties": {
            "selected": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["keywordId", "confidence"],
                    "properties": {
                        "keywordId": {"type": "integer", "enum": cand_ids},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                },
            }
        },
    }


def judge(provider: str, model: str, user: str, cand_ids: list[int]) -> tuple[dict, dict]:
    """(결과, 메타{latency, tokens, parse_error}). 파싱 실패 시 selected=[]·parse_error=True."""
    import httpx

    schema = _schema(cand_ids)
    t0 = time.time()
    if provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY")
        base = (os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip("/")
        if not key:
            raise SystemExit("ANTHROPIC_API_KEY 필요")
        tool = {"name": "select_keywords", "description": "후보 중 선택", "input_schema": schema}
        r = httpx.post(
            f"{base}/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": model, "max_tokens": 512, "system": SYSTEM,
                  "tools": [tool], "tool_choice": {"type": "tool", "name": "select_keywords"},
                  "messages": [{"role": "user", "content": user}]},
            timeout=60.0,
        )
        r.raise_for_status()
        j = r.json()
        lat = time.time() - t0
        usage = j.get("usage", {})
        tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        out = {"selected": []}
        perr = True
        for b in j["content"]:
            if b.get("type") == "tool_use":
                out, perr = b["input"], False
        return out, {"latency": lat, "tokens": tokens, "parse_error": perr}

    # openai 호환 (GMS)
    cfg = config()  # GMS_API_KEY/GMS_BASE_URL 재사용
    tool = {"type": "function", "function": {"name": "select_keywords", "parameters": schema}}
    r = httpx.post(
        f"{cfg['base']}/chat/completions",
        headers={"Authorization": f"Bearer {cfg['key']}"},
        json={"model": model, "messages": [{"role": "system", "content": SYSTEM},
                                            {"role": "user", "content": user}],
              "tools": [tool], "tool_choice": {"type": "function", "function": {"name": "select_keywords"}}},
        timeout=60.0,
    )
    r.raise_for_status()
    j = r.json()
    lat = time.time() - t0
    tokens = j.get("usage", {}).get("total_tokens", 0)
    import json as _json
    try:
        args = j["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
        out = _json.loads(args) if isinstance(args, str) else args
        perr = False
    except Exception:
        out, perr = {"selected": []}, True
    return out, {"latency": lat, "tokens": tokens, "parse_error": perr}


def run(provider: str, model: str, samples, sim, presets, by_id, k, floor, verbose=True):
    stats = {"violation": 0, "over": 0, "zero_expected": 0, "parse_error": 0,
             "sel_counts": [], "confs": [], "latency": [], "tokens": [], "n_llm": 0}
    for si, s in enumerate(samples):
        order = [pi for pi in np.argsort(-sim[si])[:k] if sim[si, pi] >= floor]
        cands = [presets[pi] for pi in order]
        cand_ids = [p["id"] for p in cands]
        if not cand_ids:
            if s.get("expect"):
                stats["zero_expected"] += 1
            if verbose:
                print(f"[{si:02d}] 후보0 → 미호출  | {s['text']}")
            continue
        out, meta = judge(provider, model, build_user(s["text"], cands), cand_ids)
        stats["n_llm"] += 1
        stats["latency"].append(meta["latency"]); stats["tokens"].append(meta["tokens"])
        if meta["parse_error"]:
            stats["parse_error"] += 1
        sel = out.get("selected", [])
        kept = [x for x in sel if x.get("keywordId") in cand_ids]
        if len(sel) != len(kept):
            stats["violation"] += 1
        if len(kept) > 3:
            stats["over"] += 1
        stats["sel_counts"].append(len(kept))
        stats["confs"].extend(x["confidence"] for x in kept)
        if verbose:
            names = ", ".join(f"{by_id[x['keywordId']]['code']}({x['confidence']:.2f})" for x in kept)
            print(f"[{si:02d}] {names or '(0개)'}  | {s['text']}")
    return stats


def summarize(tag, st):
    lat = np.array(st["latency"]) if st["latency"] else np.array([0.0])
    conf = np.array(st["confs"]) if st["confs"] else np.array([0.0])
    sc = np.array(st["sel_counts"]) if st["sel_counts"] else np.array([0])
    print(f"\n## {tag}")
    print(f"  LLM 호출: {st['n_llm']}  | 스키마위반: {st['violation']}  파싱실패: {st['parse_error']}  과잉(>3): {st['over']}  expect-후보0: {st['zero_expected']}")
    print(f"  선택개수  mean {sc.mean():.2f}  분포 {np.bincount(sc, minlength=1).tolist()}")
    print(f"  confidence mean {conf.mean():.2f}  std {conf.std():.2f} (변별력)")
    print(f"  지연 mean {lat.mean():.2f}s  p90 {np.percentile(lat,90):.2f}s  | 토큰합 {int(sum(st['tokens']))}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="anthropic")
    ap.add_argument("--model", default="claude-haiku-4-5")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--floor", type=float, default=0.30)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--compare", action="store_true", help="MODELS 목록 일괄 비교 (C-2)")
    args = ap.parse_args()

    presets = load_presets()
    by_id = {p["id"]: p for p in presets}
    pv = embed([preset_embed_text(p) for p in presets])
    samples = yaml.safe_load((HERE / "samples.yaml").read_text(encoding="utf-8"))["samples"]
    if args.limit:
        samples = samples[: args.limit]
    sv = embed([s["text"] for s in samples])
    sim = cosine_matrix(sv, pv)

    if args.compare:
        print(f"# 테스트 C-2 — 모델 비교 (samples={len(samples)}, K={args.k}, floor={args.floor})")
        for prov, model in MODELS:
            try:
                st = run(prov, model, samples, sim, presets, by_id, args.k, args.floor, verbose=False)
                summarize(f"{prov}:{model}", st)
            except Exception as e:
                print(f"\n## {prov}:{model}  — 실패: {e}")
    else:
        print(f"# 테스트 C-1 — {args.provider}:{args.model} (samples={len(samples)}, K={args.k}, floor={args.floor})\n")
        st = run(args.provider, args.model, samples, sim, presets, by_id, args.k, args.floor, verbose=True)
        summarize(f"{args.provider}:{args.model}", st)


if __name__ == "__main__":
    main()
