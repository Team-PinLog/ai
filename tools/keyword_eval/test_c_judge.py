"""테스트 C — LLM 판정 + 프롬프트 검증.

각 샘플에 대해 (1) 임베딩으로 top-K 후보를 뽑고 (2) prompts/keyword_judgment.md의
프롬프트로 LLM에 구조화 출력을 요청해 (3) 후보 밖 id 폐기 후 결과를 본다.

임베딩: GMS(OpenAI 호환). 판정 LLM: 이 세션은 ANTHROPIC_BASE_URL의 Claude 사용
(계약상 판정 모델은 미고정 — 프롬프트는 모델 이식성 유지).

환경변수(임베딩은 embed.py 참조):
  ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL : 판정 LLM(Claude Messages API)
  PINLOG_JUDGE_MODEL                     : 기본 claude-sonnet-5

사용: python test_c_judge.py [--k 10] [--floor 0.30] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np
import yaml

from embed import cosine_matrix, embed, load_presets, preset_embed_text

HERE = Path(__file__).resolve().parent

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


def judge(system: str, user: str, cand_ids: list[int]) -> dict:
    import httpx

    key = os.environ.get("ANTHROPIC_API_KEY")
    base = (os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip("/")
    model = os.environ.get("PINLOG_JUDGE_MODEL", "claude-sonnet-5")
    if not key:
        raise SystemExit("판정 LLM 키 없음: ANTHROPIC_API_KEY 설정 필요")

    tool = {
        "name": "select_keywords",
        "description": "후보 중 들어맞는 Keyword만 선택",
        "input_schema": {
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
        },
    }
    resp = httpx.post(
        f"{base}/v1/messages",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 512,
            "system": system,
            "tools": [tool],
            "tool_choice": {"type": "tool", "name": "select_keywords"},
            "messages": [{"role": "user", "content": user}],
        },
        timeout=60.0,
    )
    resp.raise_for_status()
    for block in resp.json()["content"]:
        if block.get("type") == "tool_use":
            return block["input"]
    return {"selected": []}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--floor", type=float, default=0.30)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    presets = load_presets()
    by_id = {p["id"]: p for p in presets}
    pv = embed([preset_embed_text(p) for p in presets])

    samples = yaml.safe_load((HERE / "samples.yaml").read_text(encoding="utf-8"))["samples"]
    if args.limit:
        samples = samples[: args.limit]
    sv = embed([s["text"] for s in samples])
    sim = cosine_matrix(sv, pv)

    print(f"# 테스트 C — LLM 판정 (samples={len(samples)}, K={args.k}, floor={args.floor})\n")
    n_violation = n_over = n_zero_expected = 0
    for si, s in enumerate(samples):
        order = [pi for pi in np.argsort(-sim[si])[: args.k] if sim[si, pi] >= args.floor]
        cands = [presets[pi] for pi in order]
        cand_ids = [p["id"] for p in cands]
        if not cand_ids:
            print(f"[{si:02d}] 후보 0개 → LLM 미호출, 선택 0개  | {s['text']}")
            if s.get("expect"):
                n_zero_expected += 1
            continue
        out = judge(SYSTEM, build_user(s["text"], cands), cand_ids)
        sel = out.get("selected", [])
        kept = [x for x in sel if x["keywordId"] in cand_ids]
        dropped = [x for x in sel if x["keywordId"] not in cand_ids]
        if dropped:
            n_violation += 1
        if len(kept) > 3:
            n_over += 1
        names = ", ".join(f"{by_id[x['keywordId']]['code']}({x['confidence']:.2f})" for x in kept)
        flag = " ⚠후보밖" if dropped else ""
        print(f"[{si:02d}] {names or '(선택 0개)'}{flag}  | {s['text']}")

    print("\n## 요약")
    print(f"  스키마 위반(후보 밖 id 반환): {n_violation}건")
    print(f"  과잉 선택(>3): {n_over}건")
    print(f"  expect 있는데 후보 0개: {n_zero_expected}건")


if __name__ == "__main__":
    main()
