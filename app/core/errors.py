"""오류 분류 타입.

client는 외부 호출 실패를 분류해 service까지 올리고, 상태 반영 여부는 service가
결정한다(architecture.md §3, failure-recovery.md).
"""
from __future__ import annotations


class PermanentError(Exception):
    """재시도해도 성공하지 못하는 오류. 차원 불일치, Profile 불일치 등."""


class TransientError(Exception):
    """일시적 오류. 네트워크·타임아웃·5xx 등 재시도 여지가 있는 실패."""


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
