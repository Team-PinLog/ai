"""Embedding Profile 리터럴 대조기의 단위 검증.

CI 잡은 실제 두 레포 값을 비교하므로 **평소에는 항상 초록**이다. 그러면 "대조기가
불일치를 잡는가"는 검증되지 않은 채로 남는다 — 잡을 붙인 이유가 사라진 상태를
알아채지 못한다. 여기서 고정 입력으로 그 능력을 못박는다.

네트워크를 타지 않는다. `fetch_back_yaml` 을 부르지 않고 본문 문자열을 직접 넣는다.
"""
import pytest

from tools.check_embedding_profile_parity import (
    BACK_KEYS,
    BACK_REF,
    BACK_URL,
    ParityError,
    extract_literal_default,
    parse_back_profile,
    read_ai_profile,
)

PROFILE = "openai-text-embedding-3-small-1536-cosine-v1"


def back_yaml(profile_line: str) -> str:
    return f"""
spring:
  application:
    name: pinlog-back
pinlog:
  ai:
    base-url: ${{PINLOG_AI_BASE_URL:http://localhost:8000}}
    embedding-profile: {profile_line}
    process:
      connect-timeout: 1s
"""


def test_placeholder_default_and_plain_literal_both_resolve():
    assert extract_literal_default(f"${{PINLOG_AI_EMBEDDING_PROFILE:{PROFILE}}}") == PROFILE
    assert extract_literal_default(PROFILE) == PROFILE
    assert extract_literal_default(f"  {PROFILE}  ") == PROFILE


@pytest.mark.parametrize(
    "raw",
    [
        "${PINLOG_AI_EMBEDDING_PROFILE}",  # 기본값 없음 — (a)안 이탈
        "${PINLOG_AI_EMBEDDING_PROFILE:}",  # 기본값이 빈 문자열
        "",
        "   ",
        "prefix-${PINLOG_AI_EMBEDDING_PROFILE:x}-suffix",  # 해석 불가 형태
    ],
)
def test_literal_without_usable_default_is_an_error_not_a_pass(raw):
    with pytest.raises(ParityError):
        extract_literal_default(raw)


def test_back_yaml_is_parsed_at_the_documented_key_path():
    assert parse_back_profile(back_yaml(f"${{PINLOG_AI_EMBEDDING_PROFILE:{PROFILE}}}")) == PROFILE
    assert BACK_KEYS == ("pinlog", "ai", "embedding-profile")


def test_missing_key_path_fails_instead_of_defaulting_to_a_match():
    with pytest.raises(ParityError, match="pinlog.ai.embedding-profile"):
        parse_back_profile("spring:\n  application:\n    name: pinlog-back\n")


def test_unparsable_yaml_body_fails():
    with pytest.raises(ParityError):
        parse_back_profile("")


def test_multi_document_yaml_conflicting_values_fail():
    text = (
        back_yaml(f"${{PINLOG_AI_EMBEDDING_PROFILE:{PROFILE}}}")
        + "\n---\n"
        + back_yaml("${PINLOG_AI_EMBEDDING_PROFILE:some-other-profile-v9}")
    )
    with pytest.raises(ParityError, match="갈린다"):
        parse_back_profile(text)


def test_multi_document_yaml_agreeing_values_resolve():
    line = f"${{PINLOG_AI_EMBEDDING_PROFILE:{PROFILE}}}"
    assert parse_back_profile(back_yaml(line) + "\n---\n" + back_yaml(line)) == PROFILE


def test_ai_side_reads_the_declared_literal_not_the_environment(monkeypatch):
    monkeypatch.setenv("PINLOG_EMBEDDING_PROFILE", "env-override-must-not-win")
    assert read_ai_profile() == PROFILE


def test_mismatch_is_detected_between_the_two_sides():
    """이 잡이 존재하는 이유 — 한쪽만 바뀌면 반드시 갈라져야 한다."""
    skewed = parse_back_profile(
        back_yaml("${PINLOG_AI_EMBEDDING_PROFILE:openai-text-embedding-3-large-3072-cosine-v1}")
    )
    assert skewed != read_ai_profile()

    matching = parse_back_profile(back_yaml(f"${{PINLOG_AI_EMBEDDING_PROFILE:{PROFILE}}}"))
    assert matching == read_ai_profile()


def test_back_source_is_pinned_to_a_documented_public_ref():
    # 무인증으로 읽을 수 있는 raw endpoint 여야 한다 — 토큰을 쓰지 않는 것이 경계다.
    assert BACK_URL.startswith("https://raw.githubusercontent.com/Team-PinLog/back/")
    assert f"/{BACK_REF}/" in BACK_URL
    assert BACK_URL.endswith("/src/main/resources/application.yml")
