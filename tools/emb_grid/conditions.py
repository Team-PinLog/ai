"""임베딩 4조건의 정본. 조건 정의가 여기 한 곳에만 있다.

`S15P11A705-174` 의 검색 실패 2건 중 8번(「밥 먹고 산책하면서 쉬어가는 공원」)의 원인이
**질의의 「공원」이 본문에 없고 장소명에만 있다**는 것이었다. 개선 축이 둘 —
임베딩 입력에 장소명을 넣는 것과 모델을 키우는 것 — 이고 어느 쪽이 듣는지 모르므로
둘을 교차시켜 넷을 잰다.

`profile` 문자열은 자유롭지 않다. `app/core/config.py` 의 `_profile_consistency` 가
`model` · `dimension` · `distance` 세 값을 **부분 문자열로** 요구하며, 어기면 기동 실패한다.
그 위에 `grid-{조건}` 접미사를 붙여 두 가지를 동시에 만족시킨다.

    측정용임이 드러난다      `grid` 가 들어간 profile 은 실배포 경로에서 만들어지지 않는다
    실데이터와 섞이지 않는다   실배포 profile 은 `-v1` 로 끝난다. 문자열이 겹치지 않으므로
                            `context_embedding.embedding_profile = $2` 로 거르는 검색이
                            측정 벡터를 실데이터 질의에 섞어 낼 수 없다

`include_place_name` 은 back 쪽 스위치다. **결합을 back 에서 하는 이유**는
`EmbeddingInputComposer` 주석에 있다 — 시딩 데이터의 `contextBody` 에 장소명을 박으면
화면 본문이 오염되고, 측정이 검증하는 코드 경로가 채택 후 배포할 경로와 달라진다.
"""
from __future__ import annotations

from dataclasses import dataclass

DISTANCE = "cosine"


@dataclass(frozen=True)
class Condition:
    key: str
    model: str
    dimension: int
    include_place_name: bool
    label: str

    @property
    def profile(self) -> str:
        return f"openai-{self.model}-{self.dimension}-{DISTANCE}-grid-{self.key.lower()}"

    def env(self) -> dict[str, str]:
        """이 조건으로 프로세스를 띄울 때 주입할 환경변수.

        FastAPI · `seed.py` · 이 스크립트가 **모두 같은 값**을 봐야 한다. `verify.py` 계열은
        자기 프로세스의 `get_settings()` 로 profile 을 읽어 검색 요청에 싣고, FastAPI 는
        자기 설정과 대조해 다르면 422 로 거절한다(`search_service.search`). 한쪽에만 주면
        측정이 시작되지 않는다.
        """
        return {
            "PINLOG_EMBEDDING_MODEL": self.model,
            "PINLOG_EMBEDDING_DIMENSION": str(self.dimension),
            "PINLOG_EMBEDDING_DISTANCE": DISTANCE,
            "PINLOG_EMBEDDING_PROFILE": self.profile,
            # back 쪽. 앞의 것은 런타임 대조용(BD-39), 뒤의 것이 조건 B·D를 만든다.
            "PINLOG_AI_EMBEDDING_PROFILE": self.profile,
            "PINLOG_AI_EMBEDDING_INCLUDE_PLACE_NAME": str(self.include_place_name).lower(),
        }


CONDITIONS: dict[str, Condition] = {
    c.key: c
    for c in (
        Condition("A", "text-embedding-3-small", 1536, False, "기준선 — 현행 동작"),
        Condition("B", "text-embedding-3-small", 1536, True, "장소명 결합"),
        Condition("C", "text-embedding-3-large", 3072, False, "모델 확대"),
        Condition("D", "text-embedding-3-large", 3072, True, "장소명 결합 + 모델 확대"),
    )
}


if __name__ == "__main__":
    # 셸에서 `eval "$(python tools/emb_grid/conditions.py A)"` 로 쓴다. 조건 표를 셸 쪽에
    # 다시 적으면 둘이 갈라지고, 갈라진 채 재면 어느 조건의 숫자인지 알 수 없게 된다.
    import sys

    key = sys.argv[1].upper() if len(sys.argv) > 1 else ""
    if key not in CONDITIONS:
        print(f"사용: python tools/emb_grid/conditions.py [{'|'.join(CONDITIONS)}]")
        raise SystemExit(2)
    for name, value in CONDITIONS[key].env().items():
        print(f"export {name}={value}")
    print(f"export PINLOG_TOKEN_LOG=.grid/tokens-{key}.jsonl")
