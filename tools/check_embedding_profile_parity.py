"""두 레포의 Embedding Profile 리터럴이 같은지 대조한다.

BD-39 는 Profile 정본을 `back` 의 `application.yml` 리터럴로 두는 (a) 안을 택했고,
그 근거로 *"두 값이 같다"* 를 지켜야 할 명제로 명시했다. 그런데 그 명제를 지키는
장치가 사람의 눈뿐이라는 지적이 `back#98` 리뷰에서 나왔다. 이 스크립트가 그
대조를 CI 로 옮긴다.

대조 대상은 **선언된 리터럴 기본값** 둘이다.

    back  src/main/resources/application.yml   pinlog.ai.embedding-profile
    ai    app/core/config.py                   Settings.embedding_profile

양쪽 모두 환경변수 덮어쓰기를 허용하지만(`${VAR:default}` / `Field(alias=...)`),
덮어쓰기는 실험·롤백용이고 정본은 리터럴이다. 그래서 **런타임 값이 아니라 선언된
기본값을 비교한다** — 이 프로세스의 환경변수가 결과를 바꾸면 CI 가 무엇을 검증하는지
알 수 없게 된다.

`back` 은 public 저장소라 raw endpoint 를 무인증으로 읽는다. 토큰을 쓰지 않는 것은
편의가 아니라 경계다 — 이 잡에 credential 을 주면 `ai` CI 가 타 레포 쓰기 권한을
들고 다니게 된다.

불일치·조회 실패 모두 exit 1 이다. "확인하지 못했다"를 통과로 처리하면 사람의 눈을
CI 로 옮긴 의미가 없다.
"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

BACK_REPO = "Team-PinLog/back"
# back 의 기본 브랜치이자 통합 브랜치. 배포 대상인 main 이 아니라 dev 를 읽는 것은
# 드리프트를 가장 이르게 잡기 위함이다 — main 까지 올라간 뒤 알면 이미 늦다.
BACK_REF = "dev"
BACK_PATH = "src/main/resources/application.yml"
BACK_URL = f"https://raw.githubusercontent.com/{BACK_REPO}/{BACK_REF}/{BACK_PATH}"

# pinlog.ai.embedding-profile 의 YAML 경로
BACK_KEYS = ("pinlog", "ai", "embedding-profile")

# ${VAR:default} — default 안에 } 가 없다는 전제(Profile 은 영숫자·하이픈뿐)
PLACEHOLDER = re.compile(r"^\$\{(?P<var>[A-Za-z_][A-Za-z0-9_]*)(?::(?P<default>[^}]*))?\}$")

TIMEOUT_SEC = 15


class ParityError(Exception):
    """대조를 수행할 수 없거나 값이 어긋났다."""


def extract_literal_default(raw: str) -> str:
    """`${VAR:default}` 에서 default 를, 평문이면 그대로 돌려준다.

    `${VAR}` 처럼 기본값이 없는 형태는 오류다 — 그것 자체가 BD-39 의 (a)안(리터럴을
    파일에 둔다)에서 벗어난 상태이므로 조용히 넘기지 않는다.
    """
    value = raw.strip()
    if not value:
        raise ParityError("back 의 embedding-profile 이 빈 값이다")

    match = PLACEHOLDER.match(value)
    if match is None:
        # 평문 리터럴. `${` 가 섞인 미지의 형태는 걸러 낸다.
        if "${" in value:
            raise ParityError(f"back 의 embedding-profile 형태를 해석할 수 없다: {value!r}")
        return value

    default = match.group("default")
    if default is None:
        raise ParityError(
            f"back 의 embedding-profile 에 리터럴 기본값이 없다: {value!r} — "
            "BD-39 는 정본을 파일 리터럴로 두기로 했다"
        )
    if not default:
        raise ParityError(
            f"back 의 embedding-profile 기본값이 빈 문자열이다: {value!r}"
        )
    return default


def parse_back_profile(yaml_text: str) -> str:
    """back 의 application.yml 본문에서 Profile 리터럴을 뽑는다."""
    # application.yml 은 --- 로 나뉜 다중 문서일 수 있다(profile 별 설정).
    documents = [doc for doc in yaml.safe_load_all(yaml_text) if isinstance(doc, dict)]
    if not documents:
        raise ParityError("back 의 application.yml 을 매핑으로 파싱하지 못했다")

    found: list[str] = []
    for document in documents:
        node: object = document
        for key in BACK_KEYS:
            if not isinstance(node, dict) or key not in node:
                node = None
                break
            node = node[key]
        if node is not None:
            found.append(str(node))

    if not found:
        raise ParityError(
            f"back 의 application.yml 에 {'.'.join(BACK_KEYS)} 가 없다 — "
            "경로가 바뀌었으면 이 스크립트를 함께 고쳐야 한다"
        )
    if len({extract_literal_default(value) for value in found}) > 1:
        raise ParityError(
            f"back 의 application.yml 안에서 {'.'.join(BACK_KEYS)} 가 여러 값으로 갈린다: {found}"
        )
    return extract_literal_default(found[0])


def read_ai_profile() -> str:
    """ai 의 선언된 기본값을 읽는다.

    `Settings()` 를 만들지 않는다 — 인스턴스화는 환경변수를 읽고 profile 정합 검증까지
    돌리므로, 이 프로세스의 환경이 비교 결과에 섞여 든다. 클래스의 필드 선언만 본다.
    """
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from app.core.config import Settings

    field = Settings.model_fields.get("embedding_profile")
    if field is None:
        raise ParityError(
            "app/core/config.py 의 Settings 에 embedding_profile 필드가 없다 — "
            "이름이 바뀌었으면 이 스크립트를 함께 고쳐야 한다"
        )
    default = field.default
    if not isinstance(default, str) or not default:
        raise ParityError(
            f"ai 의 embedding_profile 기본값이 리터럴 문자열이 아니다: {default!r}"
        )
    return default


def fetch_back_yaml(url: str = BACK_URL) -> str:
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SEC) as response:  # noqa: S310
            return response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ParityError(f"back 의 application.yml 조회 실패 ({url}): {error}") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--back-yaml",
        help="back 의 application.yml 경로. 생략하면 raw endpoint 에서 받는다(무인증).",
    )
    arguments = parser.parse_args()

    try:
        if arguments.back_yaml:
            source = arguments.back_yaml
            yaml_text = Path(source).read_text(encoding="utf-8")
        else:
            source = BACK_URL
            yaml_text = fetch_back_yaml()

        back_profile = parse_back_profile(yaml_text)
        ai_profile = read_ai_profile()
    except ParityError as error:
        print(f"::error::Embedding Profile 대조 불가 — {error}")
        return 1

    print(f"back ({source})")
    print(f"  {'.'.join(BACK_KEYS)} = {back_profile}")
    print("ai  (app/core/config.py)")
    print(f"  Settings.embedding_profile = {ai_profile}")

    if back_profile != ai_profile:
        print(
            "::error::Embedding Profile 리터럴 불일치 — "
            f"back={back_profile!r} / ai={ai_profile!r}. "
            "검색 요청이 런타임 대조에서 거절된다(back 은 422, 사용자에게는 503). "
            "두 레포 중 어느 쪽이 정본인지 합의한 뒤 양쪽을 같은 값으로 맞춰라 — "
            "공용 계약 05 §7.1 표도 함께 갱신 대상이다."
        )
        return 1

    print("일치")
    return 0


if __name__ == "__main__":
    sys.exit(main())
