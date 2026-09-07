# -*- coding: utf-8 -*-
"""노션 쓰기 — 저장 엔진.

브리핑 자동 배송과 채팅 저장이 **함께 쓰는 단 하나의 바닥**이다.

⛔ 헤더는 `2025-09-03` 이다. 옛 버전(`2022-06-28`)의 `parent: {database_id}` 는
   **단일 소스 DB 에서만** 행 생성이 되므로, 사용자가 그 DB 에 데이터 소스를
   하나 더 붙이는 순간 저장이 실패한다. 읽기 경로는 옛 버전 그대로 둔다 —
   버전은 요청마다 붙는 값이라 섞여도 된다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx
import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

NOTION_API = "https://api.notion.com"
NOTION_VERSION = "2025-09-03"
_TIMEOUT = 20.0

#: 노션이 한 요청에 받아 주는 블록 수. 넘기면 400 이 난다.
MAX_BLOCKS_PER_REQUEST = 100
#: rich_text 한 조각의 상한.
MAX_TEXT_CHARS = 2000


class NotionError(Exception):
    """사용자에게 **원인을 갈라서** 말하기 위한 예외.

    "저장하지 못했습니다" 한 줄이면 연결을 안 붙인 것인지 URL 이 틀린 것인지
    알 수 없다. 실측상 `not_connected` 가 압도적으로 흔할 것이다.
    """

    def __init__(self, kind: str, message: str = "", status: int = 0):
        super().__init__(message or kind)
        self.kind = kind
        self.status = status


def is_enabled() -> bool:
    return bool((settings.notion_write_token or "").strip())


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {(settings.notion_write_token or '').strip()}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _classify(status: int, payload: dict) -> NotionError:
    message = str(payload.get("message", ""))[:300]
    if status == 404:
        return NotionError("not_connected", message, status)
    if status in (401, 403):
        return NotionError("forbidden", message, status)
    if status == 400:
        # ⚠️ 속성 이름이 어긋나면 노션은 400 을 준다. 우리 버그이므로 갈라 둔다.
        if "is not a property that exists" in message or "property" in message.lower():
            return NotionError("bad_property", message, status)
        return NotionError("bad_request", message, status)
    return NotionError("unavailable", message, status)


def _request(method: str, path: str, body: dict | None = None) -> dict:
    if not is_enabled():
        raise NotionError("disabled", "NOTION_WRITE_TOKEN 미설정")
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            response = client.request(
                method, f"{NOTION_API}{path}", headers=_headers(), json=body
            )
    except httpx.HTTPError as exc:
        raise NotionError("unavailable", str(exc)[:200]) from exc

    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if response.status_code >= 400:
        error = _classify(response.status_code, payload)
        logger.warning(
            "notion_write_failed",
            kind=error.kind, status=response.status_code,
            path=path, message=str(payload.get("message", ""))[:200],
        )
        raise error
    return payload
