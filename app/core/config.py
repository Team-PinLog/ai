"""단일 설정 진입점.

Embedding Profile은 이 한 곳에서만 읽는다(model-profile.md §2.1). 다른 모듈은 설정
객체를 통해서만 접근하며, Profile 문자열 리터럴을 코드 어디에도 두지 않는다.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Preset 캐시
    preset_cache_ttl_sec: int = Field(600, alias="PRESET_CACHE_TTL_SEC")

    # 서비스 간 인증
    internal_shared_secret: str = Field(alias="INTERNAL_SHARED_SECRET")

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
