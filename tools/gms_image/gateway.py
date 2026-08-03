"""GMS 호출부 — 자격 증명 로드 · 마스킹 · 호출 상한 · **한 건씩 즉시 기록**.

두 프로브(`probe_gen.py` 축 A, `probe_vision.py` 축 B)가 공유한다. 재는 것은 다르지만
**지켜야 할 것이 같다** — 키를 흘리지 않기, 정해진 횟수를 넘기지 않기, 그리고 결과를
잃지 않기.

## 왜 한 건씩 append 하나

**GMS 호출은 다시 뜨면 쿼터를 또 쓴다.** 회차를 다 돌고 마지막에 파일을 쓰면, 중간에
세션이 죽었을 때 이미 쓴 쿼터가 통째로 사라진다(`claude-code#63023`). 그래서 응답이
오는 즉시 JSONL 한 줄을 붙이고 flush 한다. 파일 형식이 JSON 배열이 아니라 JSONL 인 이유가
이것 하나다 — 배열은 닫는 괄호가 있어야 유효하고, 죽은 세션은 그것을 못 쓴다.

## 왜 호출 상한이 코드에 있나

칩이 준 상한(축 A 20 · 축 B 30)을 사람이 세지 않는다. 세다 틀리면 공용 게이트웨이를
남의 몫까지 태운다. `Budget` 이 넘는 순간 예외로 멈추고, **그때까지 기록된 것은 이미
파일에 있다.**

## 마스킹

`-205`(게이트웨이 오류 본문 마스킹)와 `-227`(비전 재고)이 쓴 절차 그대로다. 키 문자열
치환 + 긴 영숫자 덩어리 마스킹. 여기서는 **요청 본문을 애초에 기록하지 않는 것**이
더 큰 방어다 — 축 B 의 요청에는 base64 이미지가 12 MB 들어 있고, 그것을 기록하면 마스킹
이전에 파일이 못 쓰게 된다. 남기는 것은 이미지 **지문**뿐이다(`synth.Image.fingerprint`).

모델명·엔드포인트 경로는 남긴다 — 공개 설정이고(P45), 어느 경로를 쟀는지가 없으면
수치가 다음 사람에게 상수로 읽힌다(T27: 쿼터는 시점·경로별로 다르다).
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
OUTDIR = ROOT / ".gms_image"

KST = timezone(timedelta(hours=9), "KST")

# Windows 콘솔 기본 코드페이지(cp949)에서 한국어 로그가 깨진다. `tools/judge_vote/run_live.py`
# 와 같은 처리 — 실패해도 측정은 계속한다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# 비밀처럼 보이는 긴 덩어리. base64 이미지 에코·토큰류가 여기 걸린다. 32자 미만은 안 건다 —
# 모델명(`claude-haiku-4-5-20251001`)까지 가려지면 기록에서 경로를 읽을 수 없다.
_SECRETISH = re.compile(r"[A-Za-z0-9+/=_\-]{32,}")

# 응답 본문을 남기는 상한. 오류 본문의 형태(게이트웨이 고정 문구 vs 벤더 원문)를 가르는
# 데 필요한 만큼만. 200 본문은 어차피 usage 와 짧은 텍스트라 이 안에 들어온다.
_EXCERPT = 2000

# 파싱된 응답에서 이 길이를 넘는 문자열은 자리표시자로 바꾼다. 축 A 의 생성 응답은
# base64 이미지가 1.5 MB 라 그대로 남기면 기록 파일이 커밋할 수 없는 크기가 되고,
# **재현에 쓸모도 없다** — 이미지는 매번 다르게 생성되므로 바이트를 보관해도 대조군이
# 되지 못한다. 길이는 남긴다(응답 크기가 곧 측정값이다).
_BLOB = 256


def strip_blobs(value, limit: int = _BLOB):
    """응답 트리를 훑어 긴 문자열을 `<blob:N>` 으로 바꾼다. 구조는 그대로 둔다.

    `usage`·`finishReason` 같은 짧은 값은 손대지 않으므로 판정에 필요한 것은 전부 남는다.
    """
    if isinstance(value, str):
        return value if len(value) <= limit else f"<blob:{len(value)}>"
    if isinstance(value, list):
        return [strip_blobs(v, limit) for v in value]
    if isinstance(value, dict):
        return {k: strip_blobs(v, limit) for k, v in value.items()}
    return value


class BudgetExceeded(RuntimeError):
    """정해진 호출 수를 넘겼다. 무한 재시도로 쿼터를 태우지 않기 위한 하드 스톱."""


class Budget:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def take(self) -> int:
        if self.used >= self.limit:
            raise BudgetExceeded(f"호출 상한 {self.limit}회 소진 — 멈춘다. 중앙에 물어라")
        self.used += 1
        return self.used

    @property
    def left(self) -> int:
        return self.limit - self.used


def load_creds(env_path: Path | None = None) -> tuple[str, str]:
    """`(root, api_key)` 를 돌려준다. root 는 `.../gmsapi` 까지다.

    worktree 에는 `.env` 가 없다(주 레포에만 있고 gitignore 대상). 그래서 worktree 루트 →
    주 레포 루트 순으로 찾는다. 값은 메모리로만 읽고 어디에도 쓰지 않는다.
    """
    candidates = [env_path] if env_path else [ROOT / ".env", ROOT.parents[2] / ".env"]
    values: dict[str, str] = {}
    for path in candidates:
        if path and path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                key, sep, val = line.partition("=")
                if sep and not key.lstrip().startswith("#"):
                    values.setdefault(key.strip(), val.strip())
            break
    key = values.get("GMS_API_KEY", "")
    base = values.get("GMS_BASE_URL", "")
    if not key or not base:
        raise SystemExit(
            f".env 에서 GMS_API_KEY·GMS_BASE_URL 을 못 읽었다 — 찾은 곳: "
            f"{', '.join(str(c) for c in candidates if c)}"
        )
    # `llm_client._root` 와 같은 파생 규칙. 벤더 네이티브 경로를 이 root 뒤에 붙인다.
    return base.split("/gmsapi/")[0] + "/gmsapi", key


def mask(text: str, key: str, allow: tuple[str, ...] = ()) -> str:
    """키를 지우고 남은 긴 덩어리를 가린다. `allow` 는 공개 값(모델명 등)의 예외."""
    out = text.replace(key, "<GMS_API_KEY>") if key else text
    return _SECRETISH.sub(
        lambda m: m.group(0) if m.group(0) in allow else f"<masked:{len(m.group(0))}>", out
    )


def now_kst() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


class Recorder:
    """JSONL 한 줄씩 append. 열어 두고 매 호출마다 flush 한다."""

    def __init__(self, name: str) -> None:
        OUTDIR.mkdir(parents=True, exist_ok=True)
        self.path = OUTDIR / name
        self._fh = self.path.open("a", encoding="utf-8")

    def write(self, record: dict) -> None:
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


def call(
    client: httpx.Client,
    *,
    method: str,
    url: str,
    headers: dict,
    body: dict | None,
    key: str,
    allow: tuple[str, ...] = (),
) -> dict:
    """한 번 부르고 **기록용 dict** 를 돌려준다. 예외를 밖으로 내지 않는다.

    타임아웃·연결 실패도 측정값이다 — 12 MB 업로드가 게이트웨이에서 끊기는 것과 벤더가
    거부하는 것은 다른 답이고, 예외로 죽으면 그 구분이 기록에 안 남는다.
    """
    started = time.monotonic()
    try:
        resp = client.request(method, url, headers=headers, json=body)
        elapsed = time.monotonic() - started
        raw = resp.text
        try:
            payload = resp.json()
        except ValueError:
            payload = None
        return {
            "status": resp.status_code,
            "elapsed_ms": round(elapsed * 1000),
            "payload": strip_blobs(payload),
            "body_excerpt": mask(raw[:_EXCERPT], key, allow),
            "body_len": len(raw),
        }
    except Exception as exc:  # noqa: BLE001 — 실패 자체가 측정값이다
        return {
            "status": type(exc).__name__,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "payload": None,
            "body_excerpt": mask(str(exc)[:_EXCERPT], key, allow),
            "body_len": 0,
        }


def log(msg: str = "") -> None:
    print(msg, flush=True)
