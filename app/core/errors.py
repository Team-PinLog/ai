"""오류 분류 타입.

client는 외부 호출 실패를 분류해 service까지 올리고, 상태 반영 여부는 service가
결정한다(architecture.md §4, failure-recovery.md).
"""
from __future__ import annotations


class PermanentError(Exception):
    """재시도해도 성공하지 못하는 오류. 차원 불일치, Profile 불일치 등."""


class TransientError(Exception):
    """일시적 오류. 네트워크·타임아웃·5xx 등 재시도 여지가 있는 실패."""


class SchemaViolationError(TransientError):
    """LLM 구조화 출력이 스키마를 위반했다.

    `TransientError`의 하위 타입인 것이 핵심이다 — 같은 요청에 대한 LLM 출력은
    결정론적이지 않으므로 재요청이 성공할 여지가 있고(§3.1 짧은 재시도의 대상),
    **재시도 후에도 위반이면 영구 오류**다(§2.2). 그 승격은 `llm_client.judge`가
    하며, 이 타입은 client 밖으로 나가지 않는다. 따라서 service가 보는 분류는
    여전히 `TransientError`/`PermanentError` 둘뿐이고 §2의 두 분류 체계가 유지된다.

    Embedding 응답 형식 위반은 이 타입을 쓰지 않는다. 프로바이더가 같은 요청에
    같은 형식으로 답하므로 재시도가 무의미하고, §2.2가 곧바로 영구 오류로 둔다.
    """


class PersistDiscarded(Exception):
    """저장 TX에서 FOR UPDATE 재검사가 실패해 결과를 폐기하고 롤백할 때 사용.

    삭제(CANCELLED)나 경합으로 상태가 어긋난 경우이며, 오류가 아니라 설계된 폐기다
    (context-processing.md §4.4·§4.7 저장 불변식). 트랜잭션 밖에서 잡아 정상 종료한다.
    """


class ProfileMismatchError(Exception):
    """검색 요청 Profile ≠ 서버 설정 Profile. 422로 거부(model-profile.md §3.1)."""

    def __init__(self, request_profile: str, server_profile: str) -> None:
        self.request_profile = request_profile
        self.server_profile = server_profile
        super().__init__(
            f"embeddingProfile mismatch: request={request_profile} "
            f"server={server_profile}"
        )


def classify_http_status(status_code: int, detail: str) -> TransientError | PermanentError:
    """외부 API의 비-200 상태 코드를 두 분류 중 하나로 매핑한다.

    `failure-recovery.md` §2.1·§2.2의 표를 코드로 옮긴 **유일한 지점**이다. 두
    클라이언트가 각자 매핑을 들고 있어 정반대로 갈라진 것이 S15P11A705-121의 결함
    2(429를 영구 오류로)·3(401을 일시 오류로)이었다.

    - `429` → Transient. Rate limit이므로 잠시 후 같은 요청이 성공한다(§2.1).
      **`5xx`보다 먼저 판정해야 한다** — `>= 500`만 보면 429가 4xx로 떨어진다.
    - `5xx` → Transient. 제공자 장애(§2.1).
    - 그 밖의 `4xx` → Permanent. 모델명·입력 형식 오류(400 계열)와 인증 실패
      (`401`/`403`)는 키·설정을 고치기 전까지 같은 답이 온다(§2.2).
    - `3xx` → Permanent. 리다이렉트를 따르지 않으므로 base URL 설정 오류다.

    예외를 던지지 않고 **반환**한다 — 호출부가 `raise`하며, 분류와 발생을 분리해야
    분류 자체를 HTTP 없이 단위 테스트할 수 있다.
    """
    if status_code == 429 or status_code >= 500:
        return TransientError(detail)
    return PermanentError(detail)
