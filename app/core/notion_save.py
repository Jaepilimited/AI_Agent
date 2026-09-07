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
#: ⛔ "노션 페이지 링크 좀 보내줘" 는 **달라는 요청**이지 저장이 아니다.
#:    `보내`·`줘` 는 양쪽 뜻을 다 가지므로, 무엇을 달라는지(링크·주소·URL)가
#:    함께 있으면 저장으로 보지 않는다.
_GIVE_ME = re.compile(r"(링크|주소|url|경로)\s*(좀\s*)?(보내|알려|줘|공유)", re.IGNORECASE)

_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

_MARKER = re.compile(r"<!--\s*notion-save-v1:([A-Za-z0-9_-]{1,4000})\s*-->")


def notion_save_intent(query: str) -> bool:
    text = query or ""
    if not _NOTION_WORD.search(text):
        return False
    if not _SAVE_VERB.search(text):
        return False
    if _GIVE_ME.search(text):
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
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        # ⛔ 조용히 삼키지 않는다 — 마커가 깨지면 되묻기가 이어지지 않는데,
        #    흔적이 없으면 "왜 URL 을 줬는데 저장이 안 되지" 가 영영 안 잡힌다.
        logger.warning("notion_save_marker_undecodable",
                       error_type=type(exc).__name__, token=token[:40])
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


_ERROR_MESSAGE = {
    "not_connected": (
        "그 페이지를 열지 못했습니다. 둘 중 하나입니다 — 주소가 다르거나, "
        "그 페이지에 **연결**이 없습니다.\n\n"
        "노션에서 `페이지 우상단 ⋯ > 연결 > 셀라 추가` 를 한 번 해주신 뒤 "
        "다시 말씀해 주세요."),
    "forbidden": (
        "노션이 접근을 거부했습니다. 워크스페이스 설정에서 `셀라` 연결이 "
        "허용되어 있는지 관리자에게 확인해 주세요."),
    "bad_property": (
        "그 데이터베이스의 속성과 맞지 않아 저장하지 못했습니다. "
        "제목 속성이 있는 데이터베이스인지 확인해 주세요."),
    "bad_request": (
        "노션 주소를 읽지 못했습니다. 페이지나 데이터베이스 주소를 "
        "그대로 붙여넣어 주세요."),
    "unavailable": "노션에 연결하지 못했습니다. 잠시 뒤 다시 시도해 주세요.",
    "disabled": (
        "노션 저장이 아직 켜져 있지 않습니다. 관리자에게 노션 연동 설정을 "
        "요청해 주세요."),
}


def _title_from(text: str) -> str:
    for line in (text or "").split("\n"):
        cleaned = re.sub(r"^[#>\-\*\s]+", "", line).strip()
        if cleaned:
            return cleaned[:60]
    return "셀라 저장"


def _do_save(user_id: int | None, url: str, body: str, kind: str) -> str:
    from app.core import notion_export as nx

    if not nx.is_enabled():
        return _ERROR_MESSAGE["disabled"]
    if not body.strip():
        return ("저장할 내용을 찾지 못했습니다. 저장하고 싶은 답변 바로 다음에 "
                "다시 말씀해 주세요.")
    try:
        target = nx.resolve_target(int(user_id or 0), url)
        result = nx.save(target, _title_from(body), body, kind=kind,
                         link=_sella_link())
    except nx.NotionError as exc:
        logger.info("notion_save_failed", kind=exc.kind, user_id=user_id)
        return _ERROR_MESSAGE.get(exc.kind, _ERROR_MESSAGE["unavailable"])

    lines = [f"노션에 저장했습니다 → {result.url}"]
    if result.created_database:
        lines.append("그 페이지 아래에 `셀라` 데이터베이스를 새로 만들었습니다. "
                     "다음부터는 여기에 쌓입니다.")
    if result.skipped:
        lines.append("이 데이터베이스에 " + "·".join(result.skipped) +
                     " 속성이 없어 본문에만 담았습니다.")
    return "\n\n".join(lines)


def _sella_link() -> str:
    try:
        from app.core.jandi_notify import base_url

        return base_url() or ""
    except Exception:
        return ""


def handle(query: str, messages: list[dict] | None,
          user_id: int | None) -> str | None:
    """관문. 해당 없으면 None 을 돌려 평소 라우팅으로 흘려보낸다.

    ⚠️ 네트워크를 탄다 — 부르는 쪽은 `asyncio.to_thread` 로 감싼다.
    """
    from app.core import notion_export as nx

    waiting = pending(messages)
    if waiting:
        url = extract_url(query)
        if not url:
            # ⚠️ 마음이 바뀐 것일 수 있다 — 저장 요청이 아니면 놓아준다.
            if not notion_save_intent(query):
                return None
            # ⛔ 여기서도 꺼져 있으면 URL 을 또 물을 이유가 없다 (기능부터 밝힌다).
            if not nx.is_enabled():
                return _ERROR_MESSAGE["disabled"]
            return build_prompt(waiting.get("kind", "답변"))
        return _do_save(user_id, url, target_answer(messages),
                        waiting.get("kind", "답변"))

    if not notion_save_intent(query):
        return None

    # ⛔ **URL 을 묻기 전에 기능이 켜져 있는지부터 본다.** 브리프 원안은 이 확인 없이
    #    바로 `build_prompt()` 로 갔다 — 꺼진 상태에서도 URL 을 물어보고, 사용자가
    #    URL 을 주고 나서야(=_do_save 안에서) "꺼져 있다" 는 것을 알게 된다.
    #    한 턴 앞당겨 알려준다 (`test_handle_tells_the_user_when_the_feature_is_off`).
    if not nx.is_enabled():
        return _ERROR_MESSAGE["disabled"]

    url = extract_url(query)
    if not url:
        return build_prompt()
    return _do_save(user_id, url, _previous_assistant(messages), "답변")


def _previous_assistant(messages: list[dict] | None) -> str:
    history = (messages or [])[:-1]
    for message in reversed(history):
        if message.get("role") not in ("assistant", "model"):
            continue
        text = _text_of(message.get("content", "")).strip()
        if text:
            return text
    return ""


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
