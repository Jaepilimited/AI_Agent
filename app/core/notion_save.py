# -*- coding: utf-8 -*-
"""채팅에서 "이 답변 노션에 넣어줘" 를 받는 관문.

⛔ **저장과 검색을 가른다.** `노션` 은 사내 문서 검색(`notion` 라우트)의 확신
   키워드이기도 하다 — 저장 동사가 함께 있을 때만 이 관문이 잡는다.
⛔ 이 관문은 라우터보다 **먼저** 돈다. 한쪽 경로에만 달면 답이 갈린다.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_NOTION_WORD = re.compile(r"노션|notion", re.IGNORECASE)
#: 저장을 시키는 **동사**. 이것이 없으면 검색이다.
_SAVE_VERB = re.compile(
    r"(넣어|넣자|넣어줘|저장|올려|올려줘|추가해|추가하|기록해|보내줘|옮겨)")
#: ⛔ `정리`·`찾아` 는 저장 동사가 아니다 — "노션 정리 잘 돼 있나" 를 가로챈다.
_SEARCH_VERB = re.compile(r"(찾아|검색|어디\s*있|뭐\s*있|알려줘|보여줘|조회)")

_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

_MARKER = re.compile(r"<!--\s*notion-save-v1:([A-Za-z0-9_-]{1,4000})\s*-->")


def notion_save_intent(query: str) -> bool:
    text = query or ""
    if not _NOTION_WORD.search(text):
        return False
    if not _SAVE_VERB.search(text):
        return False
    # ⚠️ "노션에서 찾아서 저장해줘" 처럼 둘 다 있으면 검색으로 둔다 — 저장은
    #    되돌리기 쉽지만, 검색을 가로채면 사내 문서가 통째로 안 나온다.
    if _SEARCH_VERB.search(text):
        return False
    return True


def extract_url(text: str) -> str:
    match = _URL.search(text or "")
    return match.group(0).rstrip(").,") if match else ""


def _encode(kind: str) -> str:
    raw = json.dumps({"v": 1, "kind": kind}, ensure_ascii=False,
                     separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode(token: str) -> dict | None:
    try:
        padded = token + "=" * (-len(token) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or data.get("v") != 1:
        return None
    return {"kind": str(data.get("kind") or "답변")}


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")
    parts = []
    for part in content:
        if isinstance(part, dict):
            value = part.get("text") or part.get("content")
            if value:
                parts.append(str(value))
        elif part:
            parts.append(str(part))
    return "\n".join(parts)


def build_prompt(kind: str = "답변") -> str:
    """어디에 넣을지 되묻는다.

    ⚠️ **연결 방법을 함께 적는다.** 실측상 가장 흔한 실패는 URL 오타가 아니라
       그 페이지에 인테그레이션을 안 붙인 것이다 (노션이 404 를 준다).
    """
    return (
        f"어느 노션 페이지에 넣을까요? **페이지 주소(URL)** 를 붙여넣어 주세요.\n\n"
        "처음이라면 그 페이지에서 한 번만 설정해 주세요 — "
        "`페이지 우상단 ⋯ > 연결 > 셀라 추가`. 이게 없으면 노션이 문을 열어주지 않습니다.\n\n"
        "데이터베이스 주소를 주시면 그 DB에 행으로 쌓고, 일반 페이지를 주시면 "
        "그 아래에 `셀라` DB를 한 번 만들어 거기 쌓습니다.\n\n"
        f"<!-- notion-save-v1:{_encode(kind)} -->"
    )


def pending(messages: list[dict] | None) -> dict | None:
    """현재 사용자 메시지 **바로 앞** assistant 가 되물었는지 본다."""
    if not messages:
        return None
    history = messages[:-1] if messages[-1].get("role") == "user" else messages
    for message in reversed(history):
        if message.get("role") not in ("assistant", "model"):
            continue
        match = _MARKER.search(_text_of(message.get("content", "")))
        return _decode(match.group(1)) if match else None
    return None


def target_answer(messages: list[dict] | None) -> str:
    """되묻기 **바로 앞** assistant 본문. 못 찾으면 빈 문자열이다.

    ⛔ 마커에 본문을 담지 않는다(길다). 대신 대화에서 되찾되, 없으면
       **아무것도 저장하지 않는다** — 엉뚱한 것을 저장하는 쪽이 나쁘다.
    """
    if not messages:
        return ""
    history = messages[:-1] if messages[-1].get("role") == "user" else list(messages)
    marker_at = -1
    for index in range(len(history) - 1, -1, -1):
        if history[index].get("role") not in ("assistant", "model"):
            continue
        if _MARKER.search(_text_of(history[index].get("content", ""))):
            marker_at = index
        break
    if marker_at < 0:
        return ""
    for index in range(marker_at - 1, -1, -1):
        message = history[index]
        if message.get("role") not in ("assistant", "model"):
            continue
        text = _text_of(message.get("content", "")).strip()
        if text:
            return text
    return ""
