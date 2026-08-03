"""프리셋 `description`·`examples` 개정안의 **정본**. `S15P11A705-228`.

`data/keyword_preset.yaml` 을 읽어 조건별 오버라이드를 얹는다. 채택하기 전에는 이
파일만 바뀌고 시드 정본은 그대로다 — 채택하면 여기 값을 yaml 로 옮긴다.

## 조건

    base   현행 yaml 그대로. 대조군
    D      description 만 개정
    E      examples 만 개정
    DE     둘 다

**갈라 두는 이유.** 둘 다 `preset_embed_text` 에 들어가고(`embedding_client.py:36`)
`description` 만 판정 프롬프트에도 실린다(`keyword_service.py:98` 의 `cand_dicts`).
합쳐서 재면 어느 쪽이 움직였는지 모르고, 그러면 다음 사람이 무엇을 더 고쳐야 하는지도
모른다.

## 무엇을 고치는가 — 대상 선정

`-219` §3.3 과 `-223` §4 가 **같은 목록**을 남겼다. 판정을 30회 돌려도 100% 로 붙는
오분류 7행이고, 프리셋으로 접으면 5종이다.

    274·289 DRINK        WALK          같은 본문 한 쌍 + 265
    284 WITH_FAMILY      VIEW_GOOD     275·290 같은 본문 한 쌍
    266 TRENDY

## 무엇을 고치는가 — 수정 내용

**대상 선정은 라벨에서 왔지만 수정 내용은 라벨을 보지 않는다.** 이 구분이 이 티켓의
자기충족 방어이고, 근거는 `-219` 가 남긴 경고다 — *"42건으로 재면서 그 42건의 오답을
프롬프트 예시로 넣으면 측정이 자기충족이 된다"*.

수정은 **프리셋 텍스트 안에서만** 진단한다. 원칙 하나다.

    description·examples 에 **다른 프리셋의 의미 영역에 속하는 어휘**가 섞여 있으면
    그것이 연상 경로다. 그 프리셋 고유의 축으로 바꾼다.

`DRINK` 의 examples 에 「안주가 좋아서」가 있다 — 안주는 음식이고 음식 평가는 `MEAL`·
`DESSERT` 의 축이다. `WALK` 에 「밥 먹고 근처 한 바퀴」가 있다 — 「밥 먹고」는 `MEAL`
이다. 오답 본문이 무엇이었는지 몰라도 이 진단은 선다.

## yaml 머리말과의 긴장

시드 파일 머리말은 `description` 을 *"정의가 아니라 의미 범위. 동의어·인접 개념 포함"*
이라고 규정한다. **넓히라고 쓰인 필드를 좁히는 작업**이다.

그래서 좁히는 대상을 갈랐다.

    동의어           유지한다   `DRINK` 의 「한잔·반주」는 술의 다른 이름이다
    인접 개념 일반    유지한다
    다른 프리셋 소관  걷는다     `DRINK` 의 「모임」은 `GATHERING`, 「회식」은 `WITH_COLLEAGUES`

머리말이 말한 「인접 개념」은 **그 키워드의 인접**이지 다른 키워드의 영역이 아니다.
그렇게 읽으면 27개 프리셋이 서로의 영역을 침범하는 것이 정상이 되고, 그때 후보 선정과
판정이 무엇을 근거로 갈라야 하는지가 사라진다.

`examples` 머리말 규칙(*"실제 입력에 가까운 짧은 구어체. 키워드 단어가 없는 문장을
최소 하나 포함"*)은 개정안에서도 지킨다 — `_check_examples_rule` 이 강제한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "data" / "keyword_preset.yaml"

# ── description 개정 ────────────────────────────────────────────────────────
# 각 줄의 주석은 **무엇을 걷었고 그것이 누구 소관인가**다. 오답 본문을 근거로 적지
# 않는다 — 적는 순간 이 파일이 42건에 맞춘 것이 된다.
DESCRIPTION: dict[str, str] = {
    # 걷은 것: 「모임」(GATHERING) · 「회식」(WITH_COLLEAGUES).
    # 남긴 것: 「한잔·반주」 — 술의 동의어다. 「술을 마신」으로 축을 술 자체에 묶는다.
    "DRINK": "술을 마신 자리. 한잔·반주 등 주류가 실제로 있었던 경우",
    # 걷은 것: 「둘러보는」(SHOPPING·EXHIBITION 의 관람·구경 축).
    # 더한 것: 「바깥을」 — 실내에 앉아 있는 기록과 가르는 축이 원래 없었다.
    "WALK": "바깥을 걸어 다닌 활동. 마실·나들이·바람 쐬기를 포함",
    # 걷은 것: 「외출」 — 이동·나들이 축이라 WALK 와 겹친다.
    # 남긴 것: 「부모·형제」 — 가족의 동의어. 축을 「함께 있었다」에 묶는다.
    "WITH_FAMILY": "부모·형제 등 가족이 함께 있던 자리. 명절이나 주말에 가족과 보낸 시간",
    # 걷은 것: 「좋은 곳」 — 일반 호평 어휘라 어떤 만족 표현과도 가깝다.
    # 더한 것: 「보이는」 — 높은 곳에 있다는 사실이 아니라 눈에 들어오는 경치라는 축.
    "VIEW_GOOD": "창밖이나 바깥으로 보이는 경치. 야경·창가 뷰를 포함",
    # 걷은 것: 「분위기 있는」 — COZY·LIVELY·RETRO 와 전부 겹치는 최광의 어휘다.
    # 남긴 것: 「힙하고 트렌디한」 — 이 프리셋 고유의 축. 시각 축(사진·인테리어)에 묶는다.
    "TRENDY": "사진과 인테리어가 눈에 띄는 공간. 힙하고 트렌디한 비주얼",
}

# ── examples 개정 ──────────────────────────────────────────────────────────
# 같은 원칙. 세 문장 중 다른 프리셋 소관 어휘가 든 것만 갈아 끼우고 나머지는 그대로 둔다.
EXAMPLES: dict[str, list[str]] = {
    "DRINK": [
        "가볍게 맥주 한잔",
        # 대체: "안주가 좋아서 술이 술술" — 「안주」는 음식이고 음식 평가는 MEAL 축이다.
        "소주 한 병이 금방 비었다",
        "밤늦게까지 마시기 좋았음",
    ],
    "WALK": [
        # 대체: "밥 먹고 근처 한 바퀴" — 「밥 먹고」는 MEAL 이다.
        "강변 따라 한 바퀴 돌았다",
        "천천히 걷기 좋은 길",
        "바람 쐬러 나왔다가 들름",
    ],
    "WITH_FAMILY": [
        # 대체: "부모님 모시고 저녁 먹었다" — 「저녁 먹었다」는 MEAL 이다.
        "부모님 모시고 다녀왔다",
        "온 가족이 다 같이",
        # 대체: "어른들이랑 오기 편한 분위기" — 「분위기」는 ATMOSPHERE 축이다.
        "동생이랑 둘이 왔음",
    ],
    "VIEW_GOOD": [
        "창밖 경치가 끝내줬다",
        # 대체: "야경 보면서 한잔" — 「한잔」은 DRINK 다.
        "야경이 한눈에 들어온다",
        # 대체: "탁 트인 게 속이 시원함" — 「탁 트인」은 SPACIOUS 의 정의 문구와 겹친다.
        # 「맛이 있다」류의 관용구도 쓰지 않는다 — 표시명에 이미 「맛집」이 들어 있어
        # 음식 어휘가 겹으로 실린다(표시명은 접점이라 이 티켓에서 못 고친다).
        "창가 자리에서 내려다보이는 풍경이 좋았다",
    ],
    "TRENDY": [
        "어디를 찍어도 그림이 나옴",
        "인테리어가 요즘 스타일",
        # 대체: "감성 터지는 곳" — 세 어절뿐이라 의미가 얇고 어떤 카페 문장과도 가깝다.
        "소품 하나하나가 예뻐서 사진 많이 찍었다",
    ],
}

# ── 개정 **전** examples — 시드에 `E` 를 반영한 뒤 `base`·`D` 를 재현하기 위한 것 ──
#
# 이 티켓이 `E` 를 채택해 `data/keyword_preset.yaml` 에 반영했다. 그러면 시드를 그대로
# 읽는 `base` 가 더 이상 대조군이 아니게 되고 **이 리포트의 수치를 다시 못 낸다.**
# `-223` 이 앞선 티켓의 산출물을 못 찾아 GMS 1,092호출을 다시 뜬 것과 같은 종류의 손실이다.
#
# 그래서 개정 전 원본을 여기 고정한다. `base`·`D` 는 이 값을 쓴다.
EXAMPLES_PRE: dict[str, list[str]] = {
    "DRINK": ["가볍게 맥주 한잔", "안주가 좋아서 술이 술술", "밤늦게까지 마시기 좋았음"],
    "WALK": ["밥 먹고 근처 한 바퀴", "천천히 걷기 좋은 길", "바람 쐬러 나왔다가 들름"],
    "WITH_FAMILY": [
        "부모님 모시고 저녁 먹었다", "온 가족이 다 같이", "어른들이랑 오기 편한 분위기",
    ],
    "VIEW_GOOD": ["창밖 경치가 끝내줬다", "야경 보면서 한잔", "탁 트인 게 속이 시원함"],
    "TRENDY": ["어디를 찍어도 그림이 나옴", "인테리어가 요즘 스타일", "감성 터지는 곳"],
}

TARGETS = tuple(sorted(DESCRIPTION))

# `base2` 는 base 와 **글자 하나까지 같다.** 조건이 아니라 바닥이다 — 임베딩 API 가
# 결정적이지 않아서(T68) 같은 텍스트를 다시 떠도 벡터가 미세하게 갈리고, 그 흔들림이
# 후보 집합을 바꾸는지 재지 않으면 D·E·DE 의 후보 변화를 전부 개정의 몫으로 읽게 된다.
# `-219` 가 판정 비결정성을 대조군으로 잰 것과 같은 자리다.
CONDITIONS = ("base", "base2", "D", "E", "DE")
_ALIAS = {"base2": "base"}


def _load_seed() -> list[dict]:
    doc = yaml.safe_load(SEED.read_text(encoding="utf-8"))
    presets = doc.get("presets") or doc.get("keyword_presets")
    if not presets:
        raise SystemExit(f"{SEED} 에서 presets 를 못 읽었다.")
    return presets


def _check_examples_rule(code: str, display_name: str, examples: list[str]) -> None:
    """시드 머리말 규칙 — 「키워드 단어가 없는 문장을 최소 하나 포함」.

    개정안이 이 규칙을 깨면 조건 사이에 **개정 내용 말고 규칙 위반도 섞인다.** 그러면
    측정이 무엇의 효과인지 말할 수 없다. 조용히 넘기지 않고 여기서 멈춘다.
    """
    if not examples:
        raise SystemExit(f"{code}: examples 가 비었다")
    if not any(display_name not in ex for ex in examples):
        raise SystemExit(
            f"{code}: 표시명 「{display_name}」이 없는 examples 문장이 하나도 없다"
        )


def build(condition: str) -> list[dict]:
    """조건 하나에 해당하는 프리셋 목록. 시드의 순서·id·code 는 그대로다."""
    if condition not in CONDITIONS:
        raise SystemExit(f"알 수 없는 조건: {condition} (있는 것: {CONDITIONS})")
    condition = _ALIAS.get(condition, condition)

    use_desc = condition in ("D", "DE")
    use_ex = condition in ("E", "DE")

    out = []
    for p in _load_seed():
        q = dict(p)
        q.setdefault("visibility", "PUBLIC")
        q["examples"] = list(p.get("examples", []))
        code = q["code"]
        # 시드가 이미 `E` 를 반영했으므로 examples 는 **항상 명시적으로 정한다.**
        # 시드를 그대로 쓰면 `base`·`D` 가 조용히 `E`·`DE` 와 같아진다.
        if code in EXAMPLES:
            q["examples"] = list(EXAMPLES[code] if use_ex else EXAMPLES_PRE[code])
        if use_desc and code in DESCRIPTION:
            q["description"] = DESCRIPTION[code]
        _check_examples_rule(code, q["display_name"], q["examples"])
        out.append(q)

    missing = set(DESCRIPTION) - {p["code"] for p in out}
    if missing:
        # 시드에 없는 code 를 고치려 들면 그 조건은 base 와 같아지고, 그것을 모르면
        # 「효과가 없다」를 결론으로 낸다. 재기 전에 멈춘다.
        raise SystemExit(f"시드에 없는 code 를 개정 대상으로 두었다: {sorted(missing)}")
    return out


def diff_report() -> str:
    """개정 전후를 나란히 낸다. 리포트에 그대로 붙일 수 있게 텍스트로 돌려준다."""
    base = {p["code"]: p for p in build("base")}
    rev = {p["code"]: p for p in build("DE")}
    lines = []
    for code in TARGETS:
        b, r = base[code], rev[code]
        lines.append(f"## {code} {b['display_name']} ({b['category']})")
        lines.append(f"  description  - {b['description']}")
        lines.append(f"               + {r['description']}")
        for i, (x, y) in enumerate(zip(b["examples"], r["examples"])):
            mark = " " if x == y else "*"
            lines.append(f"  examples[{i}]{mark} - {x}")
            if x != y:
                lines.append(f"               + {y}")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    for cond in CONDITIONS:
        build(cond)
    print(f"조건 {CONDITIONS} 전부 규칙 검사 통과 · 대상 {len(TARGETS)}종 {TARGETS}\n")
    print(diff_report())
