# -*- coding: utf-8 -*-
"""바깥 API 호출을 몇 번 다시 해 본다 — 배치가 하루를 통째로 건너뛰지 않게.

⛔ **왜 필요한가** (2026-08-27). 제품 전성분 적재가 이틀 연속 실패했다:

    08-26 04:00  HttpError 503  (sheets.googleapis.com)
    08-27 04:00  TimeoutError: read operation timed out

두 번 다 구글 쪽 일시 장애인데, 호출이 **한 번뿐**이라 그 순간에 걸리면 하루치가
통째로 날아간다. 다음 시도는 24시간 뒤다 — 그 사이 성분 데이터는 조용히 낡는다.
(적재가 "실패"로 남긴 하지만, 화면의 답변은 아무 말 없이 옛 데이터를 쓴다.)

⚠️ **모든 예외를 다시 시도하면 안 된다.** 권한 오류(403)·없는 시트(404)·잘못된
   범위는 몇 번을 더 해도 같다 — 기다리는 시간만 버리고 진짜 원인을 늦게 안다.
   여기서는 **일시적일 수 있는 것만** 다시 한다.
"""
from __future__ import annotations

import time
from typing import Callable, TypeVar

import structlog

logger = structlog.get_logger(__name__)

T = TypeVar("T")

#: 다시 걸어 볼 만한 HTTP 상태. 5xx 는 서버 쪽 일시 장애, 429 는 속도 제한이다.
#: ⛔ 4xx 는 넣지 마라 (429 만 예외) — 다시 해도 같고, 원인 파악만 늦어진다.
RETRIABLE_STATUS = frozenset({429, 500, 502, 503, 504})

#: 상태 코드가 없는 예외 중 다시 해 볼 것 — 이름으로 판정한다.
#: `googleapiclient` 는 소켓 오류를 그대로 올려보내므로 타입이 여러 가지다.
_RETRIABLE_NAMES = (
    "timeout", "timederror", "connectionerror", "connectionreset",
    "connectionaborted", "brokenpipe", "sslerror", "remotedisconnected",
    "incompleteread", "servernotfounderror",
)


def is_transient(exc: BaseException) -> bool:
    """다시 해 볼 만한 실패인가."""
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in RETRIABLE_STATUS
    name = type(exc).__name__.lower()
    return any(token in name for token in _RETRIABLE_NAMES)


def with_retry(
    fn: Callable[[], T],
    *,
    what: str,
    attempts: int = 3,
    first_delay: float = 3.0,
    factor: float = 3.0,
) -> T:
    """`fn` 을 최대 `attempts` 번 부른다 (3초 → 9초 → …).

    ⚠️ 마지막 시도까지 실패하면 **원래 예외를 그대로 올린다** — 재시도가 원인을
       가리면 안 된다. 몇 번 만에 됐는지는 로그에 남겨, 조용히 불안정해지는 것을
       나중에 볼 수 있게 한다.
    """
    delay = first_delay
    for attempt in range(1, attempts + 1):
        try:
            value = fn()
            if attempt > 1:
                logger.warning("retry_succeeded", what=what, attempt=attempt)
            return value
        except Exception as exc:
            if attempt >= attempts or not is_transient(exc):
                if attempt > 1:
                    logger.error("retry_exhausted", what=what, attempts=attempt,
                                 error=str(exc)[:200])
                raise
            logger.warning("retry_after_transient_error", what=what, attempt=attempt,
                           wait=delay, error=str(exc)[:200])
            time.sleep(delay)
            delay *= factor
    raise AssertionError("unreachable")   # pragma: no cover
