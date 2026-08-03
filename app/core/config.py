"""단일 설정 진입점.

Embedding Profile은 이 한 곳에서만 읽는다(model-profile.md §2.1). 다른 모듈은 설정
객체를 통해서만 접근하며, Profile 문자열 리터럴을 코드 어디에도 두지 않는다.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# GMS_BASE_URL이 반드시 포함해야 하는 경로 세그먼트.
# `llm_client._root`가 root를 파생할 때 쓰는 구분자와 같은 값이다(현재는 각자 리터럴).
GMS_PATH_SEGMENT = "/gmsapi/"

# 판정 LLM 폴백 체인의 기본값 — 우선순위 순서다. 2026-07-30 실측 근거는
# `docs/implements/2026-07-30-judge-vendor-fallback.md`에 있다. 요약하면
#
#   1  openai:gpt-4o-mini                 성공률 100% · 0.91s (가장 빠르다)
#   2  gemini:gemini-2.5-flash            현행. 결과 기준선이므로 남긴다
#   3  anthropic:claude-haiku-4-5-...     프로바이더가 셋째라 동시 장애 가능성이 가장 낮다
#
# 벤더 이름은 `app.client.vendors.ADAPTERS`의 키다. 이 문자열은 공개 설정이므로 정본을
# 코드에 둔다(P45) — 주입은 실험·롤백용 덮어쓰기이며, 항목 하나만 남기면 폴백 이전 동작으로
# 되돌아간다.
JUDGE_CHAIN_SEPARATOR = ","
JUDGE_VENDOR_SEPARATOR = ":"
DEFAULT_JUDGE_CHAIN = (
    "openai:gpt-4o-mini"
    ",gemini:gemini-2.5-flash"
    ",anthropic:claude-haiku-4-5-20251001"
)


class SettingsError(RuntimeError):
    """설정 형식 오류 — 기동을 중단시킨다.

    pydantic의 `ValueError` 경로를 쓰지 않는다. `ValidationError`로 감싸이면 pydantic이
    `input_value`(원시 입력 dict)를 메시지에 실어 `GMS_API_KEY`·`DATABASE_URL`의 앞부분이
    기동 로그와 traceback에 남는다(pydantic 2.13.4에서 실측). pydantic은 `ValueError`와
    `AssertionError`만 가로채므로, 이 예외는 메시지를 여기서 완전히 통제할 수 있다.
    """


def parse_judge_chain(spec: str) -> tuple[tuple[str, str], ...]:
    """`"vendor:model,vendor:model"` → `(("vendor", "model"), ...)`.

    **형식만 본다.** 그 벤더를 지원하는지는 어댑터 레지스트리만 알 수 있으므로
    `app.client.vendors.resolve_chain`이 판정한다(client가 core를 알고 core는 client를
    모른다 — architecture.md §4). 둘 다 기동 시점에 터지므로 잘못된 체인을 들고 뜨는
    상태는 성립하지 않는다.

    오류 메시지에 어긋난 항목을 넣는다. `GMS_BASE_URL`·키와 달리 모델명은 공개 값이고
    (P45), 형식 오류는 무엇이 잘못됐는지 보여야 고칠 수 있다.
    """
    entries: list[tuple[str, str]] = []
    for raw in spec.split(JUDGE_CHAIN_SEPARATOR):
        item = raw.strip()
        if not item:
            continue
        vendor, separator, model = item.partition(JUDGE_VENDOR_SEPARATOR)
        if not separator or not vendor.strip() or not model.strip():
            raise SettingsError(
                f"PINLOG_JUDGE_CHAIN 형식 오류 — '{item}' 은 "
                f"'vendor{JUDGE_VENDOR_SEPARATOR}model' 이 아닙니다. 항목을 "
                f"'{JUDGE_CHAIN_SEPARATOR}' 로 잇습니다 (예: {DEFAULT_JUDGE_CHAIN}). 기동 중단."
            )
        entries.append((vendor.strip(), model.strip()))
    if not entries:
        raise SettingsError(
            "PINLOG_JUDGE_CHAIN 이 비어 있습니다 — 판정 벤더가 하나도 없으면 "
            "Keyword 생성 경로 전체가 죽습니다. 기동 중단."
        )
    return tuple(entries)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", populate_by_name=True
    )

    # DB — ai 스키마 전용 롤
    database_url: str = Field(alias="DATABASE_URL")

    # GMS 게이트웨이
    gms_api_key: str = Field(alias="GMS_API_KEY")
    gms_base_url: str = Field(alias="GMS_BASE_URL")

    # Embedding Profile — 공개 값이며 이 네 줄이 정본이다(P45, model-profile.md §2.1).
    #
    # 값을 배포 설정에만 두면 교체가 git 이력·리뷰를 남기지 않는다. Profile 변경은 기존
    # 임베딩을 전부 조회 대상에서 빼는 결정인데(model-profile.md §3.2), 그것이 콘솔 편집
    # 한 번으로 가능해진다. 공개 값을 비밀처럼 다루면 보안은 늘지 않고 감시만 줄어든다.
    #
    # 주입은 필수가 아니라 **덮어쓰기**다 — 실험·롤백 때만 환경변수를 준다. 그래서
    # "배포 설정 누락"이라는 상태가 성립하지 않는다. 어긋난 조합은 아래 _profile_consistency
    # 가 기동 시, 요청과의 불일치는 model-profile.md §3.1 이 런타임에 잡는다.
    embedding_model: str = Field("text-embedding-3-small", alias="PINLOG_EMBEDDING_MODEL")
    embedding_dimension: int = Field(1536, alias="PINLOG_EMBEDDING_DIMENSION")
    embedding_distance: str = Field("cosine", alias="PINLOG_EMBEDDING_DISTANCE")
    embedding_profile: str = Field(
        "openai-text-embedding-3-small-1536-cosine-v1", alias="PINLOG_EMBEDDING_PROFILE"
    )

    # LLM 판정 (E2) — 벤더 폴백 체인. 우선순위 순서의 "vendor:model" 목록이다.
    #
    # `PINLOG_JUDGE_MODEL`을 대체했다(S15P11A705-175). 모델 하나만으로는 폴백 순서를
    # 표현할 수 없고, 벤더 이름 없이는 어느 경로·어느 인증 헤더로 부를지 알 수 없다.
    judge_chain: str = Field(DEFAULT_JUDGE_CHAIN, alias="PINLOG_JUDGE_CHAIN")

    # 판정 다수결 회수 (S15P11A705-223). 같은 입력을 이 횟수만큼 판정해 **엄격 다수결**
    # (`votes * 2 > n`)로 접는다. 규칙과 근거는 `app.service.judge_vote`.
    #
    # 기본값 1 은 현행 동작과 **정확히** 같다 — 호출도 1회고 `votes >= 1` 이 곧 선택이다.
    # 되돌리기가 설정 한 줄이어야 한다는 요구가 이 기본값의 이유다. 값을 올리면 판정
    # 호출이 그만큼 늘어난다(Context 1건당 n회). 채택 판단은
    # `docs/implements/2026-07-31-judge-vote.md` 를 보라.
    judge_vote_n: int = Field(1, alias="PINLOG_JUDGE_VOTE_N")

    # 후보 검색
    keyword_candidate_top_k: int = Field(10, alias="KEYWORD_CANDIDATE_TOP_K")
    similarity_floor: float = Field(0.30, alias="SIMILARITY_FLOOR")

    # 개인 검색 결과 컷 (personal-search.md §6.1). **위 `SIMILARITY_FLOOR` 와 다른 값이다** —
    # 저쪽은 Keyword 후보 선정에 걸리고 이쪽은 검색 결과에 걸린다. 값이 우연히 같지만
    # 서로 다른 측정(-210 · -213)이 각각 정했으므로 한쪽을 옮기면 다른 쪽이 따라오면 안 된다.
    #
    # 0 이면 그 컷을 끈다. 두 컷은 서로 다른 실패 모드를 막으므로 하나로 대체되지 않는다
    # (-213 실측: r 은 무관 질의를 15건 중 0건도 침묵시키지 못하고, τ_abs 는 관련 질의의
    # 꼬리 제거가 같은 안전 마진에서 r 보다 약하다).
    search_similarity_floor: float = Field(0.30, alias="SEARCH_SIMILARITY_FLOOR")
    search_top_ratio: float = Field(0.60, alias="SEARCH_TOP_RATIO")

    # 단어형 질의의 절대 하한 (S15P11A705-266 실측). **문장형과 대역이 겹치지 않아서**
    # 값을 가른다 — 문장형 정답 하한은 0.3642 인데 단어형 정답(컷 전 top-3) 하한은
    # 0.2438 이다. 단일값으로 두면 둘 중 하나가 반드시 손해를 본다.
    #
    #   0.30 단일   단어형 컷 전 1위 정답 5건이 0건이 된다(`비건`→플랜트가 1위인데 0건)
    #   0.24 단일   그 5건이 살아나지만 **문장형** 무관 질의 침묵이 11/15 → 5/15 로 무너진다
    #   갈랐을 때   단어형 회복 71/71 · 1위 손실 0 이면서 문장형은 완전 불변
    #
    # 0.24 는 「컷 전 1위인 정답을 하나도 잃지 않는 가장 높은 값」이다(0.25 부터 깨진다).
    # 마진이 얇다 — 최저 정답이 `스팟` 0.2438 이라 0.0038 뿐이다.
    #
    # **이 값 하나로는 컷이 꺼지지 않는다.** 위 두 키와 성격이 다르다.
    #
    #   SEARCH_SIMILARITY_FLOOR · SEARCH_TOP_RATIO   비상 스위치를 겸한다 — 둘 다 0 이면
    #                                                컷 전체가 꺼진다(단어형 포함)
    #   SEARCH_SIMILARITY_FLOOR_WORD                 튜닝 값이다. 끄는 용도가 아니다 —
    #                                                0 으로 둬도 `r` 이 남아 계속 자른다
    search_similarity_floor_word: float = Field(
        0.24, alias="SEARCH_SIMILARITY_FLOOR_WORD"
    )
    # 단어형의 경계. **이 값은 측정이 정하지 못했다** — 측정한 단어형은 전부 공백 없는
    # 2~5자이고 문장형은 전부 공백 포함 6자↑라, 「글자 수」와 「어절 수」 두 정의가 같은
    # 답을 냈다. 둘이 갈리는 질의(`신한 부트캠프` 7자 2어절)가 행렬에 없다.
    # 두 조건을 **함께** 요구해 안전한 쪽(문장형 취급 = 더 세게 자름)으로 기운다.
    search_word_query_max_chars: int = Field(
        5, alias="SEARCH_WORD_QUERY_MAX_CHARS"
    )

    # PROCESSING 재선점 만료 — Spring 재스캔 만료와 동일 값
    processing_expiry_sec: int = Field(600, alias="PROCESSING_EXPIRY_SEC")

    # 서비스 간 인증
    internal_shared_secret: str = Field(alias="INTERNAL_SHARED_SECRET")

    @property
    def judge_vendors(self) -> tuple[tuple[str, str], ...]:
        """폴백 체인을 `(vendor, model)` 순서열로. 형식 오류면 `SettingsError`."""
        return parse_judge_chain(self.judge_chain)

    @property
    def judge_model(self) -> str:
        """체인 1순위 모델.

        폴백이 발동하지 않았을 때 실제로 판정한 모델이며, 진단 도구
        (`tools/keyword_eval/probe_quota.py`)와 `model_profile` 기본값이 이 값을 읽는다.
        **실제로 답한 모델은 `JudgeResult.model`이다** — 폴백이 걸리면 둘이 갈라지므로,
        저장에는 이 값이 아니라 그쪽을 우선한다(keyword_service._persist).
        """
        return self.judge_vendors[0][1]

    @model_validator(mode="after")
    def _judge_chain_shape(self) -> "Settings":
        """체인 형식을 기동 시 검증한다.

        판정 경로는 첫 Context 요청이 올 때까지 한 번도 실행되지 않는다. 형식 오류를
        그때까지 미루면 서버는 정상으로 보이는데 Keyword만 통째로 생성되지 않는다 —
        `GMS_BASE_URL` 세그먼트 누락과 같은 종류의 비대칭 장애다.
        """
        parse_judge_chain(self.judge_chain)
        return self

    @model_validator(mode="after")
    def _judge_vote_n_shape(self) -> "Settings":
        """`PINLOG_JUDGE_VOTE_N` 은 1 이상의 **홀수**여야 한다.

        짝수를 막는 이유는 동점이 아니라 **지배당하기 때문**이다. 엄격 다수결
        (`votes * 2 > n`)은 짝수에서도 동점을 남기지 않는다 — n=4 는 3표를 요구한다.
        그런데 n=3 도 2표를 요구하므로 n=4 는 호출을 33% 더 쓰면서 규칙만 더 조인 것이고,
        그런 조합을 원했다면 그것은 n 이 아니라 문턱으로 표현해야 한다. 열어 두면
        설정 오타 하나로 비용만 더 내는 상태가 만들어진다.

        기동 시에 끊는다. 판정 경로는 첫 Context 요청까지 실행되지 않으므로 미루면
        서버는 정상으로 보이는데 Keyword 만 통째로 어긋난다 — `judge_chain` 과 같은
        종류의 비대칭 장애다.
        """
        if self.judge_vote_n < 1 or self.judge_vote_n % 2 == 0:
            raise SettingsError(
                f"PINLOG_JUDGE_VOTE_N 은 1 이상의 홀수여야 합니다 — 받은 값 "
                f"{self.judge_vote_n}. 짝수는 바로 아래 홀수보다 호출만 더 쓰고 판정은 "
                "더 엄격해집니다(n=4 는 3표, n=3 은 2표). 기동 중단."
            )
        return self

    @model_validator(mode="after")
    def _gms_base_url_shape(self) -> "Settings":
        """`GMS_BASE_URL`에 `/gmsapi/` 세그먼트가 없으면 기동 실패.

        한 변수를 두 클라이언트가 다르게 소비한다 — 임베딩은 `{URL}/embeddings`를 그대로
        붙이고(`embedding_client.py`), 판정은 `/gmsapi/` 앞을 잘라 Gemini 네이티브 root를
        파생한다(`llm_client.py`). 세그먼트가 빠지면 **임베딩은 정상 동작하고 judge만
        조용히 실패**하는 비대칭 장애가 되며, 첫 실사용 요청까지 드러나지 않는다.

        기동 시 GMS로 요청을 보내지 않으므로 형식 검사가 유일한 사전 방어다. 다만 형식이
        맞아도 인증 오류·네트워크 도달 실패·모델 미존재는 걸러지지 않는다 — 그쪽은
        `python -m app.smoke.gms_roundtrip`이 실호출로 덮는다(dev 배포 계약 ai#32 §2·§3).

        오류 메시지에 값을 넣지 않는다. 기동 로그는 배포 파이프라인에 남는다.
        """
        if GMS_PATH_SEGMENT not in self.gms_base_url:
            raise SettingsError(
                f"GMS_BASE_URL 형식 오류 — '{GMS_PATH_SEGMENT}' 세그먼트가 없습니다. "
                "판정 클라이언트가 이 세그먼트로 Gemini root를 파생하므로, 없으면 "
                "임베딩만 동작하고 judge가 조용히 실패합니다. 기동 중단."
            )
        return self

    @model_validator(mode="after")
    def _profile_consistency(self) -> "Settings":
        """Profile 문자열과 model·dimension·distance가 어긋나면 기동 실패.

        두 개의 진실(설정값 vs Profile 문자열)이 조용히 갈라지는 것을 막는다.
        """
        for token in (
            self.embedding_model,
            str(self.embedding_dimension),
            self.embedding_distance,
        ):
            if token not in self.embedding_profile:
                raise ValueError(
                    f"embedding_profile '{self.embedding_profile}' 와 "
                    f"'{token}' 불일치 — 설정 누락/오타로 인한 Profile 분기 방지"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
