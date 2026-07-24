-- ai 스키마 테스트 전용 스냅샷 (운영 스키마 원본 아님).
--
-- 출처: Team-PinLog/back  src/main/resources/db/migration/
--   V1__create_schemas.sql   (ai 스키마 + vector 확장)
--   V100__ai_tables.sql      (ai 5테이블)
--   V101__ai_indexes.sql     (ai 조회 인덱스)
-- 기준 커밋: back main (2026-07-23 시점)
--
-- ⚠️ back이 ai 테이블(V100~V199 구간)을 변경하면 이 파일을 함께 갱신해야 한다.
--    갱신 누락 시, ai 코드가 새 컬럼을 아직 쓰지 않으면 테스트가 조용히 통과한다
--    (docs/troubleshooting에 알려진 한계로 기록). back PR 템플릿에 갱신 안내를 둔다.
--
-- core 스키마는 두지 않는다. ai 테이블은 core FK가 없고(비정규화), 테스트는 search_path=ai로 돈다.

CREATE SCHEMA ai;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE ai.keyword_preset (
    id                INT          PRIMARY KEY,
    code              VARCHAR(50)  NOT NULL UNIQUE,
    display_name      VARCHAR(50)  NOT NULL,
    category          VARCHAR(30)  NOT NULL,
    description       TEXT         NOT NULL,
    examples          TEXT[]       NOT NULL,
    embedding         VECTOR(1536) NOT NULL,
    embedding_profile VARCHAR(100) NOT NULL,
    visibility        VARCHAR(20)  NOT NULL DEFAULT 'PUBLIC',
    is_active         BOOLEAN      NOT NULL DEFAULT true,
    version           INT          NOT NULL DEFAULT 1,
    CONSTRAINT ck_keyword_preset_visibility
        CHECK (visibility IN ('PUBLIC', 'PRIVATE_ONLY', 'BLOCKED'))
);

CREATE TABLE ai.context_ai_state (
    context_id       BIGINT      PRIMARY KEY,
    embedding_status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    keyword_status   VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    retry_count      INT         NOT NULL DEFAULT 0,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_context_ai_state_embedding
        CHECK (embedding_status IN ('PENDING','PROCESSING','COMPLETED','FAILED','CANCELLED')),
    CONSTRAINT ck_context_ai_state_keyword
        CHECK (keyword_status   IN ('PENDING','PROCESSING','COMPLETED','FAILED','CANCELLED')),
    CONSTRAINT ck_context_ai_state_retry
        CHECK (retry_count BETWEEN 0 AND 3)
);

CREATE TABLE ai.context_embedding (
    context_id        BIGINT       PRIMARY KEY,
    user_id           BIGINT       NOT NULL,
    record_id         BIGINT       NOT NULL,
    embedding         VECTOR(1536) NOT NULL,
    embedding_profile VARCHAR(100) NOT NULL,
    is_deleted        BOOLEAN      NOT NULL DEFAULT false,
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE ai.context_keyword (
    context_id     BIGINT       NOT NULL,
    keyword_id     INT          NOT NULL REFERENCES ai.keyword_preset(id),
    confidence     NUMERIC(4,3),
    preset_version INT          NOT NULL,
    PRIMARY KEY (context_id, keyword_id),
    CONSTRAINT ck_context_keyword_confidence
        CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1)
);

CREATE TABLE ai.context_keyword_analysis (
    context_id         BIGINT       PRIMARY KEY,
    preset_version     INT          NOT NULL,
    unmatched_concepts JSONB        NOT NULL DEFAULT '[]'::jsonb,
    model_profile      VARCHAR(100) NOT NULL,
    updated_at         TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX idx_context_embedding_user_active
    ON ai.context_embedding (user_id, is_deleted);
CREATE INDEX idx_context_embedding_record
    ON ai.context_embedding (record_id);
CREATE INDEX idx_context_ai_state_embedding
    ON ai.context_ai_state (embedding_status, updated_at);
CREATE INDEX idx_context_ai_state_keyword
    ON ai.context_ai_state (keyword_status, updated_at);
CREATE INDEX idx_context_keyword_keyword
    ON ai.context_keyword (keyword_id);
