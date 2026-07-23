"""테스트 C — LLM 판정 (2단계).

C-1 프롬프트 안정화: 모델 1개로 스키마 위반·과잉/과소 선택을 잡아 프롬프트 확정.
C-2 모델 비교:      확정 프롬프트로 여러 모델을 돌려 판정 모델을 근거 있게 확정.

판정 모델은 계약상 미확정(GMS: GPT-5.x/Gemini/Claude). 태스크가 '후보에서 고르기'라
구조화 출력 준수가 핵심 → 경량 tier로 충분한지 본다.

GMS 게이트웨이는 도메인마다 네이티브 인증을 통과시킨다:
  openai    : Authorization: Bearer   (chat/completions)
  anthropic : x-api-key               (v1/messages)
  gemini    : x-goog-api-key          (v1beta/models/{m}:generateContent)
모든 도메인이 같은 GMS 키를 쓰고, base 경로만 다르다(GMS root에서 파생).

사용:
  python test_c_judge.py --provider openai --model gpt-5-mini    # C-1
  python test_c_judge.py --compare                               # C-2 (MODELS 일괄)
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import httpx
import numpy as np
import yaml

from embed import config, cosine_matrix, embed, load_presets, preset_embed_text

HERE = Path(__file__).resolve().parent

# C-2 비교 대상 (3사, 경량 tier). GMS에서 검증된 모델명.
MODELS = [
    ("openai", "gpt-5-mini"),
    ("openai", "gpt-5-nano"),
    ("anthropic", "claude-haiku-4-5-20251001"),
    ("gemini", "gemini-2.5-flash"),
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
    "- 주차·화장실·직원 응대·가격 같은 부대시설이나 서비스 이야기는 장소의 Keyword가 아닙니다. "
    "장소에서 무엇을 했는지·누구와·어떤 분위기였는지만 고르세요.\n"
    "- confidence는 근거의 확실함을 0~1로 나타냅니다. 애매하면 낮게 줍니다."
)


def gms_root() -> str:
    return config()["base"].split("/gmsapi/")[0] + "/gmsapi"


def build_user(context_text: str, cands: list[dict]) -> str:
    lines = []
    for p in cands:
        ex = " · ".join(p.get("examples", []))
        lines.append(
            f"- id={p['id']} | {p['display_name']} ({p['category']}) | 의미: {p['description']} | 예: {ex}"
        )
    return f"[Context]\n{context_text}\n\n[후보 Keyword]\n" + "\n".join(lines)


def _props(enum_ids):
    kid = {"type": "integer"}
    if enum_ids is not None:
        kid["enum"] = enum_ids
    return {
        "selected": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["keywordId", "confidence"],
                "properties": {
                    "keywordId": kid,
                    "confidence": {"type": "number"},
                },
            },
        }
    }


def judge(provider: str, model: str, user: str, cand_ids: list[int]):
    """(result_dict, meta{latency,in_tok,out_tok,parse_error})."""
    key = config()["key"]
    root = gms_root()
    t0 = time.time()

    if provider == "openai":
        schema = {"type": "object", "additionalProperties": False,
                  "required": ["selected"], "properties": _props(cand_ids)}
        r = httpx.post(
            f"{root}/api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model,
                  "messages": [{"role": "system", "content": SYSTEM},
                               {"role": "user", "content": user}],
                  "tools": [{"type": "function", "function": {"name": "select_keywords", "parameters": schema}}],
                  "tool_choice": {"type": "function", "function": {"name": "select_keywords"}}},
            timeout=90.0)
        r.raise_for_status()
        j = r.json(); lat = time.time() - t0
        u = j.get("usage", {})
        meta = {"latency": lat, "in_tok": u.get("prompt_tokens", 0), "out_tok": u.get("completion_tokens", 0)}
        try:
            args = j["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
            out = json.loads(args) if isinstance(args, str) else args
            meta["parse_error"] = False
        except Exception:
            out, meta["parse_error"] = {"selected": []}, True
        return out, meta

    if provider == "anthropic":
        schema = {"type": "object", "additionalProperties": False,
                  "required": ["selected"], "properties": _props(cand_ids)}
        r = httpx.post(
            f"{root}/api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": model, "max_tokens": 512, "system": SYSTEM,
                  "tools": [{"name": "select_keywords", "input_schema": schema}],
                  "tool_choice": {"type": "tool", "name": "select_keywords"},
                  "messages": [{"role": "user", "content": user}]},
            timeout=90.0)
        r.raise_for_status()
        j = r.json(); lat = time.time() - t0
        u = j.get("usage", {})
        meta = {"latency": lat, "in_tok": u.get("input_tokens", 0), "out_tok": u.get("output_tokens", 0), "parse_error": True}
        out = {"selected": []}
        for b in j.get("content", []):
            if b.get("type") == "tool_use":
                out, meta["parse_error"] = b["input"], False
        return out, meta

    if provider == "gemini":
        # function-calling 모드는 2.5-flash에서 코드형 호출로 malformed 되므로,
        # 네이티브 구조화 출력(responseSchema)을 쓴다. 정수 enum 없이(사후 필터가 보장).
        # thinkingBudget=0으로 사고 비활성화(경량 태스크 + malformed·토큰낭비 방지).
        schema = {"type": "object", "required": ["selected"], "properties": _props(None)}
        r = httpx.post(
            f"{root}/generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": key, "content-type": "application/json"},
            json={"systemInstruction": {"parts": [{"text": SYSTEM}]},
                  "contents": [{"role": "user", "parts": [{"text": user}]}],
                  "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema,
                                       "maxOutputTokens": 2048, "thinkingConfig": {"thinkingBudget": 0}}},
            timeout=90.0)
        r.raise_for_status()
        j = r.json(); lat = time.time() - t0
        u = j.get("usageMetadata", {})
        meta = {"latency": lat, "in_tok": u.get("promptTokenCount", 0), "out_tok": u.get("candidatesTokenCount", 0), "parse_error": True}
        out = {"selected": []}
        try:
            text = j["candidates"][0]["content"]["parts"][0]["text"]
            out, meta["parse_error"] = json.loads(text), False
        except Exception:
            pass
        return out, meta

    raise ValueError(f"unknown provider {provider}")


def run(provider, model, samples, sim, presets, by_id, k, floor, verbose=True):
    st = {"violation": 0, "over": 0, "zero_expected": 0, "parse_error": 0, "err": 0,
          "sel_counts": [], "confs": [], "latency": [], "in_tok": 0, "out_tok": 0, "n_llm": 0}
    for si, s in enumerate(samples):
        order = [pi for pi in np.argsort(-sim[si])[:k] if sim[si, pi] >= floor]
        cand_ids = [presets[pi]["id"] for pi in order]
        cands = [presets[pi] for pi in order]
        if not cand_ids:
            if s.get("expect"):
                st["zero_expected"] += 1
            if verbose:
                print(f"[{si:02d}] 후보0 → 미호출  | {s['text']}")
            continue
        try:
            out, meta = judge(provider, model, build_user(s["text"], cands), cand_ids)
        except Exception as e:
            st["err"] += 1
            if verbose:
                print(f"[{si:02d}] ERR {str(e)[:80]}")
            continue
        st["n_llm"] += 1
        st["latency"].append(meta["latency"]); st["in_tok"] += meta["in_tok"]; st["out_tok"] += meta["out_tok"]
        if meta["parse_error"]:
            st["parse_error"] += 1
        sel = out.get("selected", []) or []
        kept = [x for x in sel if x.get("keywordId") in cand_ids]
        if len(sel) != len(kept):
            st["violation"] += 1
        if len(kept) > 3:
            st["over"] += 1
        st["sel_counts"].append(len(kept))
        st["confs"].extend(float(x["confidence"]) for x in kept if "confidence" in x)
        if verbose:
            names = ", ".join(f"{by_id[x['keywordId']]['code']}({float(x.get('confidence',0)):.2f})" for x in kept)
            print(f"[{si:02d}] {names or '(0개)'}  | {s['text']}")
    return st


def summarize(tag, st):
    lat = np.array(st["latency"]) if st["latency"] else np.array([0.0])
    conf = np.array(st["confs"]) if st["confs"] else np.array([0.0])
    sc = np.array(st["sel_counts"]) if st["sel_counts"] else np.array([0])
    tot = st["in_tok"] + st["out_tok"]
    print(f"\n## {tag}")
    print(f"  호출 {st['n_llm']}  에러 {st['err']}  스키마위반 {st['violation']}  파싱실패 {st['parse_error']}  과잉(>3) {st['over']}  expect-후보0 {st['zero_expected']}")
    print(f"  선택개수 mean {sc.mean():.2f}  분포 {np.bincount(sc, minlength=1).tolist()}")
    print(f"  confidence mean {conf.mean():.2f}  std {conf.std():.2f}")
    print(f"  지연 mean {lat.mean():.2f}s  p90 {np.percentile(lat,90):.2f}s")
    n = max(st["n_llm"], 1)
    print(f"  토큰 in {st['in_tok']} / out {st['out_tok']} / total {tot}  (호출당 {tot//n})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="openai")
    ap.add_argument("--model", default="gpt-5-mini")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--floor", type=float, default=0.30)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--compare", action="store_true")
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
                print(f"\n## {prov}:{model} — 실패: {str(e)[:100]}")
    else:
        print(f"# 테스트 C-1 — {args.provider}:{args.model} (samples={len(samples)}, K={args.k}, floor={args.floor})\n")
        st = run(args.provider, args.model, samples, sim, presets, by_id, args.k, args.floor, verbose=True)
        summarize(f"{args.provider}:{args.model}", st)


if __name__ == "__main__":
    main()
