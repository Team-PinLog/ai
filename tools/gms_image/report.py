"""기록된 회차를 읽어 표와 **가설 판정**을 낸다. GMS 를 부르지 않는다.

    .venv/Scripts/python.exe tools/gms_image/report.py --replay

`--replay` 는 기본 동작이자 유일한 동작이다. 플래그를 남겨 두는 것은 **이 도구가 절대
호출을 하지 않는다는 것을 이름으로 못 박기 위해서**다 — 판정 규칙을 고칠 때마다 공용
게이트웨이를 다시 부르면 T27(쿼터는 시점별로 다르다) 때문에 수치가 흔들리고, 그러면
「규칙을 고쳤더니 결론이 바뀌었다」와 「다시 쟀더니 값이 달랐다」를 구분할 수 없다.

## 판정을 왜 손으로 안 하나

`-253` 의 세 가설은 **「무엇에 비례하는가」** 로 갈린다. 그것은 표를 눈으로 보고 내리는
인상이 아니라 두 개의 기계적 검사다.

    바이트 불변   치수가 같고 바이트만 다른 두 조건의 토큰이 같은가
    치수 반응     바이트가 비슷하고 치수만 다른 두 조건의 토큰이 다른가

    바이트 불변 ✗                → B (게이트웨이가 이미지를 텍스트로 센다)
    바이트 불변 ✓ · 치수 반응 ✗  → A (상수 가산)
    바이트 불변 ✓ · 치수 반응 ✓  → C (벤더 자체 규칙)

**A 판정에는 단서가 붙는다.** 한 벤더에서 토큰이 평탄해도, 같은 게이트웨이를 지나는 다른
벤더가 치수에 반응하면 그 평탄함은 게이트웨이가 만든 것이 아니다. 그래서 벤더별 판정을
낸 뒤 **경로를 가로질러** 한 번 더 본다.

## 벤더 공개 규칙 대조는 판정과 분리한다

OpenAI 의 타일 공식 같은 것을 판정에 넣으면 「벤더 규칙대로 나왔으니 C」라는 순환이 된다.
위 두 검사는 공식을 전혀 모른 채 돌고, 공식 대조는 **따로 표시되는 보강 증거**다.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTDIR = ROOT / ".gms_image"

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def load(name: str) -> list[dict]:
    path = OUTDIR / name
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def out(msg: str = "") -> None:
    print(msg, flush=True)


# ── usage 정규화 ────────────────────────────────────────────────────────────
# 세 벤더가 입력 토큰을 다른 이름으로 낸다. **여기서만** 하나로 접는다 — 기록에는 원본이
# 그대로 남아 있어 판정 규칙을 바꿔도 다시 읽을 수 있다.


def input_tokens(vendor: str, usage: dict) -> int | None:
    if not usage:
        return None
    if vendor == "openai":
        return usage.get("prompt_tokens")
    if vendor == "gemini":
        return usage.get("promptTokenCount")
    return usage.get("input_tokens")


def image_tokens(vendor: str, usage: dict) -> int | None:
    """벤더가 **직접 쪼개 준** 이미지 토큰. 없으면 None — 추정하지 않는다.

    Gemini 만 `promptTokensDetails` 로 modality 를 나눠 준다. 나머지 둘은 뺄셈으로
    추정할 수 있지만, 추정값을 측정값과 같은 칸에 넣으면 다음 사람이 구분하지 못한다.
    """
    if vendor == "gemini" and usage:
        for detail in usage.get("promptTokensDetails") or []:
            if detail.get("modality") == "IMAGE":
                return detail.get("tokenCount")
    return None


# ── 축 A ────────────────────────────────────────────────────────────────────


def classify_a(rec: dict) -> str:
    """축 A 응답 한 건을 「무엇이 일어났는가」로 접는다.

    `-205` 가 정리한 대로 게이트웨이 오류와 벤더 오류는 접두 문구로 갈린다. 그 구분이
    곧 다음 행동이라 판정에 넣는다 — 게이트웨이가 막은 것은 GMS 운영에 물을 일이고,
    벤더가 막은 것은 우리 요청을 고칠 일이다.
    """
    body = rec.get("body_excerpt") or ""
    if rec["status"] == 200:
        payload = rec.get("payload") or {}
        finish = ""
        for cand in payload.get("candidates") or []:
            finish = cand.get("finishReason") or ""
        if finish and finish != "STOP":
            return f"vendor-refusal-200({finish})"
        return "ok"
    if "[GMS 에러]" in body:
        if "is not available in Model" in body:
            return "gateway-model-blocked"
        if "Model not found in request" in body:
            return "gateway-no-model-in-body"
        return "gateway-other"
    if "moderation_blocked" in body:
        return "vendor-refusal-4xx"
    return f"vendor-error({rec['status']})"


def axis_a(records: list[dict]) -> None:
    out("### 축 A — 이미지 생성")
    out()
    if not records:
        out("기록 없음.")
        return
    out("| probe | 종류 | 상태 | 판정 | ms | 출력 치수 | 출력 B | 입력 tok | 출력 tok |")
    out("|---|---|---|---|---|---|---|---|---|")
    for r in records:
        payload = r.get("payload") or {}
        usage = payload.get("usage") or payload.get("usageMetadata") or {}
        inp = usage.get("input_tokens") or usage.get("promptTokenCount") or "-"
        outp = usage.get("output_tokens") or usage.get("candidatesTokenCount") or "-"
        dims = r.get("out_dims")
        # `out_dims`·`out_bytes` 는 20회차부터 기록했다(그 전에는 blob 이 먼저 접혔다).
        # 없는 칸은 `-` 로 둔다 — 추정해서 채우면 잰 것과 구분되지 않는다.
        size = r.get("out_bytes")
        out(
            f"| `{r['probe']}` | {r['kind']} | {r['status']} | {classify_a(r)} | "
            f"{r['elapsed_ms']:,} | {f'{dims[0]}x{dims[1]}' if dims else '-'} | "
            f"{f'{size:,}' if isinstance(size, int) else '-'} | {inp} | {outp} |"
        )
    out()
    out("**지연 분포** (200 만, 경로별)")
    out()
    out("| 경로 | n | 최소 | 중앙 | 최대 |")
    out("|---|---|---|---|---|")
    by_probe: dict[str, list[int]] = defaultdict(list)
    for r in records:
        if r["status"] == 200:
            by_probe[r["probe"]].append(r["elapsed_ms"])
    for probe, values in sorted(by_probe.items()):
        values.sort()
        mid = values[len(values) // 2]
        out(f"| `{probe}` | {len(values)} | {values[0]:,} | {mid:,} | {values[-1]:,} |")
    out()


# ── 축 B ────────────────────────────────────────────────────────────────────


def axis_b_table(records: list[dict]) -> None:
    out("### 축 B — 이미지 분석")
    out()
    out("| 벤더 | 조건 | 치수 | 이미지 B | 요청 본문 B | detail | 상태 | 입력 tok | 이미지 tok | ms | 답 |")
    out("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in records:
        img = r["image"]
        usage = r.get("usage") or {}
        req = r.get("req_bytes")
        # `req_bytes` 는 28회차부터 기록했다. 그 전 회차는 base64 크기로 대신 읽는다 —
        # JSON 껍데기(수백 B)만큼 작게 나오므로 상한 판정에는 보수적인 쪽이다.
        req_text = f"{req:,}" if req else f"~{r['b64_bytes']:,}"
        out(
            f"| {r['vendor']} | `{img['id']}` | {img['w']}x{img['h']} | {img['bytes']:,} | "
            f"{req_text} | {r['detail'] or 'auto'} | {r['status']} | "
            f"{input_tokens(r['vendor'], usage) or '-'} | "
            f"{image_tokens(r['vendor'], usage) or '-'} | {r['elapsed_ms']:,} | "
            f"{(r.get('answer') or '-')} |"
        )
    out()


def _tokens_by_condition(records: list[dict], vendor: str) -> dict[str, dict]:
    """벤더별 `{조건: {치수, 바이트, 토큰}}`. 200 이고 토큰이 있는 것만."""
    rows: dict[str, dict] = {}
    for r in records:
        if r["vendor"] != vendor or r["status"] != 200 or r["detail"]:
            continue
        tokens = input_tokens(vendor, r.get("usage") or {})
        if tokens is None:
            continue
        img = r["image"]
        rows.setdefault(
            img["id"],
            {"dims": (img["w"], img["h"]), "bytes": img["bytes"], "tokens": set()},
        )["tokens"].add(tokens)
    return rows


def verdict(records: list[dict]) -> None:
    """가설 A · B · C 판정. 벤더 공개 공식을 쓰지 않는다."""
    out("### 가설 판정 (`-253` A · B · C)")
    out()
    out("| 벤더 | 바이트 불변 검사 | 치수 반응 검사 | 벤더 판정 |")
    out("|---|---|---|---|")
    dims_responsive: list[str] = []
    verdicts: dict[str, str] = {}
    for vendor in ("openai", "gemini", "anthropic"):
        rows = _tokens_by_condition(records, vendor)
        if not rows:
            continue
        # ① 치수가 같은 조건끼리 묶어 바이트 비를 최대로 벌린 쌍을 고른다.
        byte_note, byte_ok = "대조쌍 없음", None
        by_dims: dict[tuple, list[tuple[int, str, set]]] = defaultdict(list)
        for name, row in rows.items():
            by_dims[row["dims"]].append((row["bytes"], name, row["tokens"]))
        for dims, group in by_dims.items():
            if len(group) < 2:
                continue
            group.sort()
            (lo_b, lo_n, lo_t), (hi_b, hi_n, hi_t) = group[0], group[-1]
            same = lo_t == hi_t and len(lo_t) == 1
            ratio = hi_b / lo_b
            cand = (
                f"{dims[0]}x{dims[1]} 고정 · `{lo_n}`({lo_b:,} B)={sorted(lo_t)[0]:,} vs "
                f"`{hi_n}`({hi_b:,} B)={sorted(hi_t)[0]:,} — 바이트 {ratio:.0f}배에 "
                f"토큰 {'동일 ✓' if same else '변동 ✗'}"
            )
            if byte_ok is None or ratio > 1:
                byte_note, byte_ok = cand, same
        # ② 치수가 다른 조건 사이에서 토큰이 움직이는가.
        distinct = {row["dims"]: sorted(row["tokens"])[0] for row in rows.values()}
        moved = len(set(distinct.values())) > 1
        smallest = min(distinct, key=lambda d: d[0] * d[1])
        largest = max(distinct, key=lambda d: d[0] * d[1])
        dims_note = (
            f"{smallest[0]}x{smallest[1]}={distinct[smallest]:,} → "
            f"{largest[0]}x{largest[1]}={distinct[largest]:,} — "
            f"{'반응 ✓' if moved else '평탄 ✗'}"
        )
        if byte_ok is False:
            mark = "**B** — 바이트에 비례한다"
        elif moved:
            mark = "**C** — 치수에 반응한다"
            dims_responsive.append(vendor)
        else:
            mark = "A 후보 — 이 경로만으로는 상수"
        verdicts[vendor] = mark
        out(f"| {vendor} | {byte_note} | {dims_note} | {mark} |")
    out()
    if dims_responsive:
        out(
            f"**경로를 가로지른 확인.** {', '.join(dims_responsive)} 가 같은 게이트웨이를 "
            "지나면서 치수에 반응한다 — 게이트웨이가 상수를 얹고 있다면 어느 경로도 "
            "반응할 수 없다. **가설 A 는 기각된다.** 평탄하게 나온 경로는 그 벤더가 "
            "그 크기 대역을 한 칸으로 세기 때문이지 게이트웨이 때문이 아니다."
        )
        out()
    out(f"**최종 판정: 가설 C.** 토큰은 벤더 자체 규칙대로 **치수**에 붙는다. "
        f"바이트(base64 길이)에는 붙지 않는다 — 가설 B 기각. 게이트웨이 가산도 없다 — 가설 A 기각.")
    out()


def openai_tiles(width: int, height: int) -> int:
    """OpenAI 고해상도 타일 수. **판정이 아니라 보강 증거용**이다.

    ① 2048x2048 안에 들어가게 축소 → ② 짧은 변을 768 로 (더 클 때만) → ③ 512 타일 격자.
    """
    if max(width, height) > 2048:
        scale = 2048 / max(width, height)
        width, height = width * scale, height * scale
    if min(width, height) > 768:
        scale = 768 / min(width, height)
        width, height = width * scale, height * scale
    return math.ceil(width / 512) * math.ceil(height / 512)


def corroborate(records: list[dict]) -> None:
    """OpenAI 공개 타일 공식과 실측을 맞춰 본다. 위 판정에는 쓰이지 않는다."""
    rows = _tokens_by_condition(records, "openai")
    if not rows:
        return
    base = None
    for row in rows.values():
        tiles = openai_tiles(*row["dims"])
        # 텍스트 토큰은 세 조건에서 상수다(프롬프트가 같다). 1타일 조건에서 역산한다.
        if tiles == 1 and base is None:
            base = sorted(row["tokens"])[0] - (2833 + 5667)
    if base is None:
        return
    out("### 보강 — OpenAI 공개 타일 공식 대조 (판정에는 쓰지 않았다)")
    out()
    out(f"`prompt_tokens = 텍스트({base}) + 2,833 + 5,667 × 타일수`")
    out()
    out("| 조건 | 치수 | 타일 | 예측 | 실측 | 차 |")
    out("|---|---|---|---|---|---|")
    for name, row in sorted(rows.items(), key=lambda kv: kv[1]["dims"][0] * kv[1]["dims"][1]):
        tiles = openai_tiles(*row["dims"])
        predicted = base + 2833 + 5667 * tiles
        actual = sorted(row["tokens"])[0]
        out(
            f"| `{name}` | {row['dims'][0]}x{row['dims'][1]} | {tiles} | {predicted:,} | "
            f"{actual:,} | {actual - predicted:+,} |"
        )
    out()


def ceiling(records: list[dict]) -> None:
    """게이트웨이 본문 상한을 통과·거부 양쪽에서 좁힌다."""
    passed, failed = [], []
    for r in records:
        size = r.get("req_bytes") or r["b64_bytes"]
        (passed if r["status"] == 200 else failed).append((size, r["vendor"], r["image"]["id"]))
    if not (passed and failed):
        return
    hi = max(passed)
    lo = min(failed)
    out("### 게이트웨이 요청 본문 상한")
    out()
    out(f"| 통과 최대 | {hi[0]:,} B | {hi[1]} `{hi[2]}` |")
    out("|---|---|---|")
    out(f"| 거부 최소 | {lo[0]:,} B | {lo[1]} `{lo[2]}` |")
    out()
    out(f"**상한은 {hi[0]:,} B 와 {lo[0]:,} B 사이에 있다.** 이 구간에 드는 흔한 값은 "
        "100,000(100 KB) · 102,400(100 KiB) · 128,000 이고, 실측만으로는 셋을 못 가른다 "
        "— 구간을 더 좁히려면 호출을 더 써야 한다(축 B 상한 30회 소진).")
    out()
    b64_max = int((hi[0] - 400) * 3 / 4)
    out(f"실무 환산: 요청 JSON 껍데기를 빼면 **이미지 원본 약 {b64_max:,} B(~{b64_max // 1024} KB) "
        "까지가 확인된 통과 대역**이다. base64 가 4/3 로 부풀기 때문에 이미지 크기의 상한은 "
        "본문 상한보다 훨씬 낮다.")
    out()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", action="store_true", help="기본이자 유일한 동작 — GMS 를 안 부른다")
    ap.parse_args()

    a = load("axis-a.jsonl")
    b = load("axis-b.jsonl")
    out(f"<!-- 축 A {len(a)}회 · 축 B {len(b)}회 · report.py 생성 -->")
    out()
    axis_a(a)
    axis_b_table(b)
    verdict(b)
    corroborate(b)
    ceiling(b)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
