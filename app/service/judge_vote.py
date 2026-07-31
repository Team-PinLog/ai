"""판정 n회를 다수결로 접는다 (self-consistency).

`S15P11A705-223`. 앞선 두 티켓이 각각 다른 이유로 막혔고 **둘을 막은 것은 같다** —
판정 자체가 흔들린다. 같은 본문·같은 후보·같은 프롬프트로 재판정만 해도 Context
11/42(26%)에서 답이 갈리고(`-210` T39 · `-219` §2 가 다른 하네스로 재현), 오분류 30종
중 10/10 고정은 2종뿐이며 나머지 25종은 회차마다 얼굴이 바뀐다(`-219` §3.4).

**오분류가 「늘 붙는 것」이 아니라 「흔들릴 때 붙는 것」이라면 여러 번 묻고 공통된 것만
남기면 사라진다.** 정상 판정은 안정적이므로(흔들리는 fit 은 63종 중 6종뿐) 다수결을
거쳐도 살아남는다. τ 도 프롬프트도 「한 번의 판정」 안에서 고치려 했고 둘 다 실패했다.

## 규칙

    선택        votes * 2 > n          엄격 다수결. 분모는 **성공 수가 아니라 n** 이다
    confidence  찬성표들의 중앙값       평균은 이상치 하나에 끌린다
    동점        선택하지 않는다         `votes * 2 > n` 이 전순서라 동점 자체가 성립 안 함
    n=1         votes*2 > 1 → votes>=1  현행 동작과 **정확히** 같다

`n` 이 짝수면 설정이 기동을 막는다(`config.Settings._judge_vote_n_shape`). 짝수는
`votes*2 > n` 아래에서 바로 아래 홀수보다 항상 엄격한데(n=4 는 3표, n=3 은 2표) 호출은
더 든다 — 지배당하는 선택지를 열어 두면 「비용을 더 내고 규칙만 더 조인 상태」가 설정
오타 하나로 만들어진다.

## 분모를 n 으로 고정하는 이유

n회 중 일부가 실패했을 때 분모를 성공 수로 낮추면 규칙이 조용히 바뀐다 — n=3 에서 1회만
성공하면 그 1회가 곧 다수결이 되어 **다수결을 켠 채로 n=1 을 실행하는 상태**가 된다.
그래서 분모는 n 으로 두고, 대신 성공 수가 n 의 과반에 못 미치면 아예 판정하지 않는다
(`has_quorum`). 정족수를 넘긴 부분 실패에서는 분모가 n 이므로 판단이 보수적으로 기운다 —
못 받은 표는 기권이 아니라 반대로 세어지고, 그 방향의 오류는 「덜 붙는다」이지
「없는 근거로 붙는다」가 아니다.

`-219` 하네스는 회차 중 일부가 실패해도 평균에 그대로 넣었다(그때는 실패 0 이라
미발현). 다수결에서는 그 처리가 규칙을 바꾸므로 여기서 명시적으로 정한다.
"""
from __future__ import annotations

import statistics
from collections import Counter
from typing import Sequence

from app.schema.llm import JudgeResult, KeywordSelection


def has_quorum(successes: int, n: int) -> bool:
    """정족수 — 성공한 판정이 n 의 과반인가.

    못 넘기면 호출자가 판정을 버린다. 분모가 n 으로 고정돼 있으므로 정족수 미달 상태로
    다수결을 돌리면 **어떤 키워드도 과반을 못 받아 선택 0건**이 나온다. 그것은 판정
    결과가 아니라 판정 실패인데, 저장해 버리면 「맞는 키워드가 없다」와 구분되지 않는다.
    """
    return successes * 2 > n


def combine(results: Sequence[JudgeResult], n: int) -> JudgeResult:
    """성공한 판정들을 다수결로 접는다. **정족수 검사는 호출자 몫이다**(`has_quorum`).

    `n=1` 이고 결과가 하나면 그 결과를 그대로 재구성한 것과 같다 — `_map` 이 뒤에서
    중복을 접고 범위를 거르므로, 여기서 순서가 달라지는 것은 최종 저장에 영향이 없다.
    """
    if n < 1:
        raise ValueError(f"n 은 1 이상이어야 한다: {n}")

    votes: dict[int, list[float | None]] = {}
    for r in results:
        # 한 회차 안에서 같은 keyword_id 가 두 번 나오면 **표 하나**로 센다. 모델이
        # 같은 후보를 중복해 내면 그 회차가 여러 표를 갖게 되어 다수결이 무너진다.
        seen: dict[int, float | None] = {}
        for s in r.selected:
            if s.keyword_id not in seen or (
                s.confidence is not None
                and (seen[s.keyword_id] is None or s.confidence > seen[s.keyword_id])
            ):
                seen[s.keyword_id] = s.confidence
        for kid, conf in seen.items():
            votes.setdefault(kid, []).append(conf)

    selected: list[KeywordSelection] = []
    for kid, confs in sorted(votes.items()):
        if not has_quorum(len(confs), n):
            continue
        present = [c for c in confs if c is not None]
        selected.append(
            KeywordSelection(
                keyword_id=kid,
                # 찬성한 회차들의 중앙값. 전부 None 이면 None 을 그대로 물려준다 —
                # 0.0 으로 채우면 「확신 없음」이 「근거 0」으로 바뀐다.
                confidence=statistics.median(present) if present else None,
            )
        )

    # `unmatched_concepts` 에도 같은 규칙을 댄다. 합집합으로 두면 회차가 늘수록 자유
    # 서술이 쌓여 n 에 비례해 길어지고, 그것은 다수결이 아니라 누적이다. 자유 서술이라
    # 표기가 조금만 달라도 표가 갈리므로 n>1 에서 대부분 비게 되는데, 이 필드는
    # 진단용이고 저장 계약상 빈 배열이 정상값이다(keyword-preset.md §4.2).
    concept_votes = Counter(c for r in results for c in dict.fromkeys(r.unmatched_concepts))
    unmatched = [c for c, v in concept_votes.items() if has_quorum(v, n)]

    # 실제로 답한 모델. 회차마다 폴백이 다르게 걸릴 수 있으므로 **가장 많이 답한 것**을
    # 남긴다. 체인이 하나면 전 회차가 같은 값이라 n=1 과 구분되지 않는다.
    models = Counter(r.model for r in results if r.model is not None)
    model = models.most_common(1)[0][0] if models else None

    return JudgeResult(selected=selected, unmatched_concepts=unmatched, model=model)
