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


class SettingsError(RuntimeError):
    """설정 형식 오류 — 기동을 중단시킨다.

    pydantic의 `ValueError` 경로를 쓰지 않는다. `ValidationError`로 감싸이면 pydantic이
    `input_value`(원시 입력 dict)를 메시지에 실어 `GMS_API_KEY`·`DATABASE_URL`의 앞부분이
    기동 로그와 traceback에 남는다(pydantic 2.13.4에서 실측). pydantic은 `ValueError`와
    `AssertionError`만 가로채므로, 이 예외는 메시지를 여기서 완전히 통제할 수 있다.
    """


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", populate_by_name=True
    )

    # DB — ai 스키마 전용 롤
    database_url: str = Field(alias="DATABASE_URL")

    # GMS 게이트웨이
    gms_api_key: str = Field(alias="GMS_API_KEY")
    gms_base_url: str = Field(alias="GMS_BASE_URL")

    # Embedding Profile — 기본값 없음(누락 시 기동 실패)
    embedding_model: str = Field(alias="PINLOG_EMBEDDING_MODEL")
    embedding_dimension: int = Field(alias="PINLOG_EMBEDDING_DIMENSION")
    embedding_distance: str = Field(alias="PINLOG_EMBEDDING_DISTANCE")
    embedding_profile: str = Field(alias="PINLOG_EMBEDDING_PROFILE")

    # LLM 판정 (E2)
    judge_model: str = Field("gemini-2.5-flash", alias="PINLOG_JUDGE_MODEL")

    # 후보 검색
    keyword_candidate_top_k: int = Field(10, alias="KEYWORD_CANDIDATE_TOP_K")
    similarity_floor: float = Field(0.30, alias="SIMILARITY_FLOOR")

    # PROCESSING 재선점 만료 — Spring 재스캔 만료와 동일 값
    processing_expiry_sec: int = Field(600, alias="PROCESSING_EXPIRY_SEC")

    # 서비스 간 인증
    internal_shared_secret: str = Field(alias="INTERNAL_SHARED_SECRET")

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
