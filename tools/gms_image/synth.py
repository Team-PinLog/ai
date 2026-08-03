"""합성 이미지 생성 — **픽셀 치수와 바이트 크기를 따로 움직인다.**

측정에 실제 이미지를 쓰지 않는다. 대화 캡처·장소 사진은 개인정보이고, 시연 DB 의
기록은 팀원 본인의 것이다(`ai#94`). 그리고 실제 사진으로는 애초에 이 측정을 할 수 없다 —
사진은 치수가 커지면 바이트도 같이 커져서 **둘 중 무엇이 토큰을 움직였는지 가를 수 없다.**

`S15P11A705-253` 이 답해야 하는 것이 정확히 그 갈림이다.

    가설 A  게이트웨이 가산      토큰이 치수·바이트 어느 쪽에도 안 붙고 상수로 얹힌다
    가설 B  base64 그대로        토큰이 **바이트**에 비례한다 (게이트웨이가 이미지를
                                텍스트로 세고 있다는 뜻)
    가설 C  정상                 토큰이 **치수**에 반응하고 바이트에는 반응하지 않는다
                                (벤더의 타일 규칙 그대로)

그래서 이 모듈이 만드는 것은 예쁜 이미지가 아니라 **대조쌍**이다.

    px512-solid   512×512  단색   ~1.5 KB
    px512-noise   512×512  노이즈  ~790 KB

둘은 치수가 같고 바이트가 500배 차이 난다. 여기서 토큰이 같으면 B 는 죽고, 토큰이
바이트를 따라가면 C 가 죽는다. **한 쌍이 세 가설 중 둘을 떨어뜨린다.**

## 왜 Pillow 를 안 쓰나

레포 의존성에 없다. 측정 하네스 하나 때문에 런타임 의존성을 늘리면 그 결정이 `app/`
컨테이너 이미지까지 따라간다. PNG 는 zlib 위의 얇은 컨테이너라 표준 라이브러리로 충분하다
(IHDR·IDAT·IEND 세 청크). 필터는 0(None)만 쓴다 — 압축률을 짜낼 이유가 없고, 노이즈는
어차피 안 줄어든다.

## 왜 JPEG 은 안 만드나

**형식이 아니라 바이트 수가 변수이기 때문이다.** JPEG 을 더해도 「같은 치수 · 다른
바이트」라는 대조는 위 한 쌍이 이미 500배로 만들어 준다. 벤더의 이미지 토큰 규칙은
디코드된 픽셀에 붙지 인코딩 형식에 붙지 않으므로, JPEG 인코더를 손으로 짜서 얻는 정보가
없다. 실사용 이미지가 JPEG 이라는 사실은 **바이트 크기 축에서 이미 대표된다.**

## 결정성

`random.Random(seed)` 로 픽셀을 만든다. 같은 `(id, seed)` 는 항상 같은 바이트를 낸다 —
`--replay` 로 판정만 다시 할 때 이미지 지문(sha256)이 기록과 맞는지 확인할 수 있다.
"""
from __future__ import annotations

import hashlib
import random
import struct
import zlib
from dataclasses import dataclass

# PNG 시그니처(고정 8바이트).
_MAGIC = b"\x89PNG\r\n\x1a\n"

# 단색 채움 색. 값 자체에 의미는 없다 — 벤더가 "무엇이 보이는가"에 한 단어로 답할 수 있는
# 무채색이 아닌 색이면 된다. 응답이 이미지를 실제로 읽었는지 눈으로 확인하는 용도다.
_SOLID_RGB = (0, 102, 204)

# zlib 압축 레벨. 상수로 못 박는다 — 레벨이 바뀌면 같은 픽셀이 다른 바이트 수를 내고,
# 그러면 기록된 sha256 과 재현이 어긋난다.
_ZLIB_LEVEL = 6


def _chunk(tag: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(tag + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


def png(width: int, height: int, *, noise_rows: int, seed: int = 0) -> bytes:
    """8bit RGB PNG. 위에서 `noise_rows` 줄만 난수이고 나머지는 단색이다.

    노이즈는 zlib 이 못 줄이므로 난수 줄 하나가 `width*3` 바이트를 그대로 더한다. 단색
    줄은 거의 0 이다. **줄 수가 곧 바이트 손잡이**이고, 치수는 그대로다 — 512×512 를
    유지한 채 2 KB 부터 787 KB 까지 훑을 수 있다.

    처음엔 `noise: bool` 이었다. 그것으로는 게이트웨이 본문 상한(2 KB 통과 · 787 KB 거부)
    사이를 못 훑어서 **가설 B 를 기각할 표본이 상한 위에만 있었다** — 400 은 토큰을
    안 준다. 줄 수로 바꾸면서 상한 아래에 바이트 사다리가 생겼다.
    """
    rng = random.Random(seed)
    solid = b"\x00" + bytes(_SOLID_RGB) * width
    rows = [
        (b"\x00" + rng.randbytes(width * 3)) if y < noise_rows else solid
        for y in range(height)
    ]
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        _MAGIC
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(b"".join(rows), _ZLIB_LEVEL))
        + _chunk(b"IEND", b"")
    )


def png_dims(raw: bytes) -> tuple[int, int] | None:
    """PNG 바이트에서 `(width, height)`. PNG 가 아니면 `None`.

    **생성된 이미지의 치수는 측정값이다** — 요청한 `size`·비율이 실제로 먹었는지가 그것
    하나로 갈린다. 그래서 응답의 base64 를 `<blob:N>` 으로 접기 **전에** 여기를 통과시킨다.
    IHDR 은 시그니처 바로 뒤 고정 위치라 청크를 순회할 필요가 없다.
    """
    if len(raw) < 24 or raw[:8] != _MAGIC:
        return None
    return struct.unpack(">II", raw[16:24])


@dataclass(frozen=True)
class Image:
    """측정 조건 한 칸. `id` 가 리포트 표의 행 이름이 된다."""

    id: str
    width: int
    height: int
    noise_rows: int
    data: bytes

    @property
    def noise(self) -> bool:
        return self.noise_rows > 0

    @property
    def nbytes(self) -> int:
        return len(self.data)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()[:16]

    def fingerprint(self) -> dict:
        """기록에 남기는 것. **이미지 자체는 남기지 않는다** — 재현은 `id` 로 다시 만든다."""
        return {
            "id": self.id,
            "w": self.width,
            "h": self.height,
            "noise": self.noise,
            "noise_rows": self.noise_rows,
            "bytes": self.nbytes,
            "sha256": self.sha256,
        }


# 측정 조건. `(id, w, h, noise_rows)`.
#
# **첫 회차(1~20)는 위 여섯 줄만 썼고, 787 KB 이상이 전부 400 이었다.** 게이트웨이가
# 본문을 못 읽어 "Model not found in request" 를 낸다 — 400 에는 usage 가 없으므로
# 바이트를 키운 표본이 토큰을 하나도 주지 못했다. 아래 두 묶음이 그 구멍을 메운다.
#
#   px1-solid       `-227` 이 쓴 1×1. 그 8,524 토큰의 출발점이라 반드시 재현해야 한다
#   px64-noise      작은 이미지
#   px512-solid     512×512 의 바닥(2 KB)
#   px512-noise     512×512 의 천장(787 KB) — 상한을 넘는다
#   px1024-noise    수 MB
#   px2048-noise    12.6 MB
#
#   ── 바이트 사다리 (치수 고정 512×512, 상한 아래) ────────────────────────────
#   px512-n64       ~98 KB    단색 대비 49배인데 치수는 같다
#   px512-n128      ~197 KB
#   px512-n256      ~393 KB   상한이 이 위 어딘가에 있다
#
#   ── 치수 사다리 (바이트 고정, 단색이라 거의 안 는다) ─────────────────────────
#   px1024-solid    1024×1024 인데 ~4 KB   OpenAI 타일이 1장에서 4장으로 는다
#   px2048-solid    2048×2048 인데 ~13 KB
#
# **두 사다리가 서로의 대조군이다.** 바이트만 200배 늘렸을 때 토큰이 안 움직이고,
# 치수만 늘렸을 때 움직이면 B 가 죽고 C 가 산다. 치수를 늘려도 안 움직이면 A 다.
_SPEC: tuple[tuple[str, int, int, int], ...] = (
    ("px1-solid", 1, 1, 0),
    ("px64-noise", 64, 64, 64),
    ("px512-solid", 512, 512, 0),
    ("px512-noise", 512, 512, 512),
    ("px1024-noise", 1024, 1024, 1024),
    ("px2048-noise", 2048, 2048, 2048),
    ("px512-n64", 512, 512, 64),
    ("px512-n128", 512, 512, 128),
    ("px512-n256", 512, 512, 256),
    ("px1024-solid", 1024, 1024, 0),
    ("px2048-solid", 2048, 2048, 0),
)

_CACHE: dict[str, Image] = {}


def catalog() -> tuple[str, ...]:
    return tuple(name for name, _, _, _ in _SPEC)


def build(image_id: str) -> Image:
    """조건 이름으로 이미지를 만든다(같은 프로세스 안에서는 캐시한다).

    치수·채움을 호출부가 직접 넘기게 두지 않는다. 조건이 코드 한 곳에만 있어야 기록의
    `id` 와 리포트의 행이 어긋나지 않는다.
    """
    if image_id in _CACHE:
        return _CACHE[image_id]
    for name, w, h, rows in _SPEC:
        if name == image_id:
            img = Image(
                id=name, width=w, height=h, noise_rows=rows, data=png(w, h, noise_rows=rows)
            )
            _CACHE[name] = img
            return img
    raise KeyError(f"모르는 조건 '{image_id}' — 있는 것: {', '.join(catalog())}")


if __name__ == "__main__":  # 조건별 실제 바이트 수 확인용. GMS 를 부르지 않는다.
    for name in catalog():
        im = build(name)
        print(f"{name:16} {im.width:>5}x{im.height:<5} {im.nbytes:>10,} B  sha={im.sha256}")
