"""데모 시딩 공통 — 설정 로딩, back 인증 클라이언트, ai 스키마 접근.

`tools/e2e/_common.py`와 같은 규약을 따른다. `.env`를 자체 파서로 읽지 않고
`app.core.config.get_settings()`를 그대로 쓴다(T16·T22).

## 왜 JWT를 직접 서명하는가

back의 유일한 로그인 경로는 소셜 OAuth 리다이렉트다(`SocialLoginController`).
스크립트가 브라우저 없이 인증을 얻으려면 back이 검증할 수 있는 Access 토큰을
만드는 수밖에 없다. 그래서 **back과 같은 RSA 개인키를 공유**한다 —
`JWT_PRIVATE_KEY`로 back에 주입한 그 키다.

이것은 인증 **우회**가 아니라 키 **공급**이다. 서명·발급자·만료·용도 클레임을
back의 `JwtTokenProvider`가 그대로 검증한다. 키가 틀리면 401을 받는다.
`JwtKeyProvider` javadoc이 로컬 임시 키쌍에 대해 내린 판단과 같은 성격이다.

키는 `.demo/`(gitignore) 아래 두고 커밋하지 않는다.
"""
from __future__ import annotations

import base64
import functools
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]  # tools/demo_seed → 레포 루트

sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app.core.config import get_settings  # noqa: E402

SETTINGS = get_settings()
DATA_YAML = HERE / "demo_data.yaml"


@functools.lru_cache(maxsize=1)
def shared_root() -> Path:
    """worktree 안에서 실행해도 **메인 워킹트리**를 가리키는 경로.

    `ROOT`(`_client.py` 위치 기준)를 쓰면 worktree마다 다른 곳을 가리킨다.
    코드는 그래야 맞지만 **키는 그러면 안 된다** — `.demo/`는 gitignore라
    worktree에 존재하지 않고, `ensure_key()`가 거기서 새 키를 만들면 back에
    주입된 키와 갈라진다. 그 결과가 전 요청 401이며 back 로그에는 아무것도
    남지 않는다(`S15P11A705-198` 결함 3).

    `--git-common-dir`은 worktree에서도 메인 레포의 `.git`을 준다. 그 부모가
    메인 워킹트리다. git이 없거나 형식이 예상과 다르면 `ROOT`로 물러선다 —
    이 함수 때문에 시딩이 못 도는 일은 없어야 한다.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(HERE), "rev-parse", "--path-format=absolute",
             "--git-common-dir"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ROOT
    common = Path(out) if out else None
    return common.parent if common is not None and common.name == ".git" else ROOT


DEMO_DIR = shared_root() / ".demo"
KEY_PATH = Path(os.environ.get("PINLOG_DEMO_JWT_KEY", DEMO_DIR / "demo-jwt-key.pem"))

DEFAULT_BACK_BASE = "http://localhost:8080/api/core"
DEFAULT_AI_BASE = "http://localhost:8000"

# back `application.yml`의 `pinlog.auth.jwt.issuer`. 값이 다르면 검증이 조용히 실패한다.
JWT_ISSUER = "pinlog"
ACCESS_COOKIE = "access_token"
CSRF_COOKIE = "XSRF-TOKEN"
CSRF_HEADER = "X-XSRF-TOKEN"

# 데모 사용자 표식. `core.social_account`에 이 provider로 남겨 두면
# "이 member는 시딩이 만든 것"을 SQL 한 줄로 판별할 수 있다. 실제 공급자
# (google·kakao·naver)와 겹치지 않는 값이어야 한다.
DEMO_PROVIDER = "demo-seed"


def arg(argv: list[str], flag: str, default: str) -> str:
    return argv[argv.index(flag) + 1].rstrip("/") if flag in argv else default


def back_base(argv: list[str]) -> str:
    return arg(argv, "--back", DEFAULT_BACK_BASE)


def ai_base(argv: list[str]) -> str:
    return arg(argv, "--ai", DEFAULT_AI_BASE)


def load_data() -> dict:
    import yaml

    return yaml.safe_load(DATA_YAML.read_text(encoding="utf-8"))


# ── JWT ────────────────────────────────────────────────────────────────────


def ensure_key() -> bytes:
    """서명 키를 읽고, 없으면 만든다. back에 주입한 키와 같은 파일이어야 한다.

    **키를 새로 만드는 것 자체가 신호다.** 이미 back이 떠 있는 환경에서 이 경로가
    비어 있다는 것은 대개 환경이 갈라졌다는 뜻이고, 그대로 두면 401만 보게 된다.
    조용히 만들지 않고 stderr로 말한다.
    """
    if KEY_PATH.exists():
        return KEY_PATH.read_bytes()

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    print(
        f"[demo_seed] 서명 키가 없어 새로 만든다: {KEY_PATH}\n"
        f"            back에 이 키를 JWT_PRIVATE_KEY로 주입하지 않았다면 "
        f"모든 요청이 401이 된다.",
        file=sys.stderr,
        flush=True,
    )
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    KEY_PATH.write_bytes(pem)
    return pem


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def mint_access_token(member_id: int, pem: bytes, ttl_sec: int = 3600) -> str:
    """back의 `JwtTokenProvider.issueAccessToken`과 같은 클레임 구성으로 RS256 서명.

    `kid` 헤더를 넣지 않는다. back은 공개키 thumbprint에서 `kid`를 뽑는데,
    다른 값을 넣으면 `JWSVerificationKeySelector`의 매처가 키를 못 찾아 검증이
    실패한다. 헤더에 `kid`가 없으면 매처가 그 조건을 걸지 않는다.
    """
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key = serialization.load_pem_private_key(pem, password=None)
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "sub": str(member_id),
        "iss": JWT_ISSUER,
        "iat": now,
        "exp": now + ttl_sec,
        "jti": str(uuid.uuid4()),
        "token_use": "access",  # JwtTokenProvider.TOKEN_USE — 없으면 필수 클레임 검증 실패
    }
    signing_input = (
        f"{_b64(json.dumps(header, separators=(',', ':')).encode())}."
        f"{_b64(json.dumps(claims, separators=(',', ':')).encode())}"
    ).encode()
    sig = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input.decode()}.{_b64(sig)}"


# ── back API 클라이언트 ─────────────────────────────────────────────────────


class BackClient:
    """한 member로 인증된 back API 호출자.

    CSRF를 실제로 통과한다 — 쿠키 인증이라 상태 변경 요청에 `X-XSRF-TOKEN`이
    필요하다(`SecurityConfig`). 안전한 요청 한 번으로 토큰 쿠키를 받아 온 뒤
    헤더에 실어 보낸다. 우회하지 않는 이유는 우회할 수단이 없기 때문이고,
    통과시키는 편이 프론트가 겪을 경로를 그대로 밟는다는 뜻이기도 하다.
    """

    def __init__(self, base: str, member_id: int, pem: bytes) -> None:
        import httpx

        self.base = base.rstrip("/")
        self.member_id = member_id
        self._client = httpx.Client(timeout=30.0, follow_redirects=False)
        # 쿠키를 httpx의 쿠키 저장소에 맡기지 않고 직접 들고 다닌다.
        # back은 인증·CSRF 쿠키에 `Secure`를 고정한다(AuthCookies·SecurityConfig).
        # http://localhost 로 붙는 클라이언트에서는 표준 쿠키 정책이 그 쿠키를
        # **저장은 하되 되돌려 보내지 않으므로**, 서버가 XSRF-TOKEN을 매 요청 새로
        # 발급하고 헤더로 보낸 값과 어긋나 403이 된다. 브라우저가 https로 겪지 않는
        # 문제라 back 쪽 설정이 잘못된 것이 아니다 — 이쪽이 맞춘다.
        self._cookies: dict[str, str] = {
            ACCESS_COOKIE: mint_access_token(member_id, pem)
        }

    def _absorb(self, resp: object) -> None:
        """Set-Cookie를 직접 읽어 보관한다(Secure 속성 무시)."""
        from http.cookies import SimpleCookie

        for raw in resp.headers.get_list("set-cookie"):  # type: ignore[attr-defined]
            jar = SimpleCookie()
            jar.load(raw)
            for name, morsel in jar.items():
                if morsel.value:
                    self._cookies[name] = morsel.value

    def _headers(self, csrf: bool) -> dict[str, str]:
        token = self._csrf_token() if csrf else None
        h = {"Cookie": "; ".join(f"{k}={v}" for k, v in self._cookies.items())}
        if token is not None:
            h[CSRF_HEADER] = token
        return h

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "BackClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _csrf_token(self) -> str:
        """헤더에 실을 CSRF 토큰. **항상 현재 쿠키 값을 쓴다.**

        캐시해 두면 안 된다 — 서버는 응답마다 `XSRF-TOKEN`을 다시 내려보낼 수
        있고(`CsrfCookieFilter`가 매 요청 토큰 해석을 강제한다), 그때 쿠키만
        갱신되고 헤더가 옛 값이면 둘이 어긋나 403이 된다. 실제로 Record 생성
        직후 Collection 생성이 그렇게 실패했다.
        """
        token = self._cookies.get(CSRF_COOKIE)
        if token:
            return token
        # 안전한 요청으로 CsrfCookieFilter가 쿠키를 내려보내게 한다.
        resp = self._client.get(
            f"{self.base}/v1/collections?size=1",
            headers={"Cookie": "; ".join(f"{k}={v}" for k, v in self._cookies.items())},
        )
        self._absorb(resp)
        token = self._cookies.get(CSRF_COOKIE)
        if not token:
            raise RuntimeError(
                f"CSRF 토큰 쿠키({CSRF_COOKIE})를 받지 못했다 — "
                f"인증 실패일 가능성이 크다(HTTP {resp.status_code}). "
                "back에 주입한 JWT_PRIVATE_KEY와 .demo/demo-jwt-key.pem이 같은지 확인하라."
            )
        return token

    def get(self, path: str) -> dict:
        resp = self._client.get(f"{self.base}{path}", headers=self._headers(csrf=False))
        self._absorb(resp)
        _raise(resp, "GET", path)
        return _unwrap(resp)

    def post(self, path: str, body: dict) -> dict:
        resp = self._client.post(
            f"{self.base}{path}", json=body, headers=self._headers(csrf=True)
        )
        self._absorb(resp)
        _raise(resp, "POST", path)
        return _unwrap(resp)

    def delete(self, path: str) -> None:
        resp = self._client.delete(
            f"{self.base}{path}", headers=self._headers(csrf=True)
        )
        self._absorb(resp)
        _raise(resp, "DELETE", path)


def _unwrap(resp: object) -> dict:
    """`ApiResponse` 공통 envelope에서 `data`를 꺼낸다.

    back은 모든 성공 응답을 `{"success": true, "data": ...}`로 감싼다
    (`ApiResponseBodyAdvice`). 호출자가 매번 `["data"]`를 적으면 envelope가
    바뀔 때 고칠 곳이 흩어지므로 여기 한 곳에 둔다.
    """
    if not resp.content:  # type: ignore[attr-defined]
        return {}
    payload = resp.json()  # type: ignore[attr-defined]
    if isinstance(payload, dict) and "success" in payload:
        return payload.get("data") or {}
    return payload


def _raise(resp: object, method: str, path: str) -> None:
    status = resp.status_code  # type: ignore[attr-defined]
    if status >= 400:
        body = resp.text[:300]  # type: ignore[attr-defined]
        raise RuntimeError(f"{method} {path} → HTTP {status} {body}")
