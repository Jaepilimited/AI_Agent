"""정적 자산의 캐시 지문 — 손으로 세던 `?v=` 번호를 대신한다.

**왜 바꿨나 (2026-08-31 실측):**

    정적 파일은 2026-03-17 부터 `Cache-Control: no-store, no-cache, must-revalidate`
    로 나가고 있었다. `?v=` 규칙이 문서에 들어온 것은 그보다 **뒤인 2026-04-08** 이다.
    브라우저 실측: 새로고침 한 번에 자기 자산 8개가 **전부 네트워크로 다시** 왔다
    (디스크 캐시 0건, 776KB). nginx 에도 `expires`·`proxy_cache` 가 없다.

    ⟶ 번호를 올리든 말든 아무것도 달라지지 않았다. 5개월간 손으로 세던 규칙이
      막고 있던 사고는 **일어날 수 없는 사고**였고, 대신 모두가 페이지를 열 때마다
      776KB 를 다시 받고 있었다.

**그래서 방향을 뒤집는다.** 번호를 없애는 것이 아니라 **자동으로 만들고**,
그 대가로 **진짜 캐시를 켠다**:

    HTML 을 내보낼 때  /frontend/chat.js  →  /frontend/chat.js?v=<내용 해시>
    그 URL 로 들어오면 `immutable, max-age=1년`

⛔ **가장 나쁜 실패는 "영영 낡은 파일에 갇히는 것"** 이다. 그래서 `immutable` 은
   **지금 파일의 해시와 일치하는 요청에만** 준다 (`matches`). 해시가 안 맞으면
   예전처럼 `no-store` 로 내보낸다 — 낡은 URL 은 캐시되지 않고, 다음 HTML 이
   새 해시를 알려 준다. 잘못 굳는 경로가 **구조적으로 없다.**

⚠️ 해시를 못 구하면 **기동 토큰**을 붙인다. 절대 버전 없이 내보내지 않는다 —
   그때만 조용히 낡을 수 있기 때문이다. 기동 토큰은 배포(재기동)마다 바뀐다.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path

import structlog

logger = structlog.get_logger()

ROOT = Path(__file__).resolve().parents[2]

#: URL 접두 → 실제 디렉토리. 여기 없는 경로는 건드리지 않는다.
_MOUNTS = {
    "/frontend/": ROOT / "app" / "frontend",
    "/static/": ROOT / "app" / "static",
}

#: HTML 안의 자산 참조. 기존 `?v=193` 이 붙어 있어도 통째로 갈아 끼운다.
_REF = re.compile(
    r"(?P<url>/(?:frontend|static)/[A-Za-z0-9_./-]+\.(?:js|css))"
    r"(?:\?v=[^\"'\s>]*)?"
)

#: 해시를 못 구할 때 쓸 값. 배포하면 재기동되므로 배포마다 바뀐다.
_BOOT = hashlib.sha1(str(time.time()).encode()).hexdigest()[:8]

#: 경로 → (mtime, size, 해시). 파일이 그대로면 다시 읽지 않는다.
_CACHE: dict[str, tuple[float, int, str]] = {}


def _resolve(url_path: str) -> Path | None:
    """URL 경로를 실제 파일로. ⛔ 마운트 밖으로 새 나가면 None (경로 탈출 방지)."""
    for prefix, base in _MOUNTS.items():
        if url_path.startswith(prefix):
            target = (base / url_path[len(prefix):]).resolve()
            try:
                target.relative_to(base.resolve())
            except ValueError:
                return None
            return target
    return None


def stamp(url_path: str) -> str:
    """자산의 캐시 지문. 파일을 못 읽으면 기동 토큰 (버전 없음은 없다)."""
    target = _resolve(url_path)
    if target is None:
        return _BOOT
    try:
        info = target.stat()
    except OSError:
        return _BOOT
    key = str(target)
    hit = _CACHE.get(key)
    if hit and hit[0] == info.st_mtime and hit[1] == info.st_size:
        return hit[2]
    try:
        digest = hashlib.sha1(target.read_bytes()).hexdigest()[:8]
    except OSError as exc:
        logger.warning("asset_stamp_unreadable", path=url_path,
                       error_type=type(exc).__name__)
        return _BOOT
    _CACHE[key] = (info.st_mtime, info.st_size, digest)
    return digest


def matches(url_path: str, version: str) -> bool:
    """요청에 붙은 `?v=` 가 **지금 파일의** 지문인가.

    ⛔ 이 판정이 곧 안전장치다. 참일 때만 1년 캐시를 준다 — 낡은 URL 은 절대
       굳지 않으므로 "영영 낡은 파일" 이 생길 수 없다.
    """
    return bool(version) and version == stamp(url_path)


def rewrite_html(html: str) -> str:
    """HTML 안의 자산 참조에 지문을 붙인다."""
    return _REF.sub(lambda m: f"{m.group('url')}?v={stamp(m.group('url'))}", html)


def boot_token() -> str:
    return _BOOT


def normalize_path(path: str, root_path: str = "") -> str:
    """ASGI scope 의 경로를 `/frontend/...` 형태로 맞춘다.

    ⚠️ Starlette 버전마다 다르다 — 1.x 는 `path` 가 **이미 전체 경로**이고
       `root_path` 에도 마운트 접두가 들어 있다. 그대로 이어 붙이면
       `/frontend/frontend/chat.js` 가 되고, 그때 나는 것은 에러가 아니라
       **캐시가 조용히 안 켜지는 것**이다 (2026-08-31 실측으로 잡았다).
    """
    if any(path.startswith(prefix) for prefix in _MOUNTS):
        return path
    return f"{root_path}{path}"


def parse_version(query_string: str) -> str:
    """`v=abc&x=1` 에서 v 만. 의존성 없이 가볍게 뽑는다."""
    for part in query_string.split("&"):
        if part.startswith("v="):
            return part[2:]
    return ""
