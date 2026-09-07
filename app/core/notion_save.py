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
