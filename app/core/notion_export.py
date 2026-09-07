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


#: 32자 이상 이어지는 hex 덩어리. 슬러그(`회의록-24f1…`)에 붙어 있어도 잡힌다.
_HEX_RUN = re.compile(r"[0-9a-fA-F]{32,}")


# Task 3: 본문 정제
_DETAILS = re.compile(r"<details\b.*?</details\s*>", re.IGNORECASE | re.DOTALL)
_CHART_FENCE = re.compile(r"```chart\b.*?```", re.IGNORECASE | re.DOTALL)
_FOLLOWUP = re.compile(r"<!--\s*followup:.*?-->", re.IGNORECASE | re.DOTALL)
_ORPHAN_VIZ = re.compile(r"^#{1,6}\s*시각화\s*$\n?", re.MULTILINE)


def clean_for_notion(text: str) -> str:
    """노션에 실을 수 없거나 실으면 안 되는 것을 걷는다.

    ⛔ `<details>실행된 쿼리</details>` 는 앱에서는 접힌 근거지만 노션에서는
       **평문으로 펼쳐져 내부 테이블 경로가 페이지에 남는다.** 노션 페이지는
       공유가 쉽다 — 잔디 규칙과 같은 이유다.
    ⚠️ 차트를 뺐으면 홀로 남는 `시각화` 제목도 함께 걷는다.
    """
    out = _DETAILS.sub("", text or "")
    out = _CHART_FENCE.sub("", out)
    out = _FOLLOWUP.sub("", out)
    out = _ORPHAN_VIZ.sub("", out)
    # ⚠️ 빈 줄만 걷는다. `.strip()` 은 첫 줄의 들여쓰기까지 걷어 표 정렬이 깨진다
    #    (잔디 글자 막대에서 실제로 겪었다).
    return re.sub(r"\n{3,}", "\n\n", out).strip("\n")


# Task 4: 마크다운 → 노션 블록
#: 브리핑 평문의 절 머리말. 이 글자로 시작하는 줄은 소제목으로 올린다.
_SECTION_EMOJI = ("📅", "✉️", "✅", "⏰", "📊", "💱", "📌")
_SUN = "☀️"


# Task 5: 마크다운 표 → 노션 표 블록
_SEPARATOR = re.compile(r"^\|?[\s:\-|]+\|?$")


def _cells(line: str) -> list[str]:
    """표 줄을 파싱하여 셀 목록으로 돌린다."""
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_table_start(lines: list[str], index: int) -> bool:
    """머리행 **다음 줄이 구분선**일 때만 표로 본다."""
    if index + 1 >= len(lines):
        return False
    head, sep = lines[index].strip(), lines[index + 1].strip()
    if not head.startswith("|") or not sep.startswith("|"):
        return False
    return bool(_SEPARATOR.match(sep)) and "-" in sep


def _table_block(lines: list[str], index: int) -> tuple[dict, int]:
    """마크다운 표를 파싱하여 노션 표 블록으로 만든다."""
    header = _cells(lines[index])
    width = len(header)
    rows = [header]
    cursor = index + 2                       # 머리행 + 구분선을 건너뛴다
    while cursor < len(lines) and lines[cursor].strip().startswith("|"):
        if _SEPARATOR.match(lines[cursor].strip()):
            cursor += 1
            continue
        rows.append(_cells(lines[cursor]))
        cursor += 1

    children = []
    for row in rows[:MAX_BLOCKS_PER_REQUEST]:
        # ⚠️ 셀 수가 table_width 와 다르면 400 이 난다.
        cells = (row + [""] * width)[:width]
        children.append({
            "object": "block", "type": "table_row",
            "table_row": {"cells": [rich_text(cell) or [] for cell in cells]},
        })
    block = {
        "object": "block", "type": "table",
        "table": {"table_width": width, "has_column_header": True,
                  "has_row_header": False, "children": children},
    }
    return block, cursor


def rich_text(text: str) -> list[dict]:
    """⚠️ 한 조각이 2,000자를 넘으면 400 이 난다 — 넘기지 말고 쪼갠다."""
    body = text or ""
    if not body:
        return []
    return [
        {"type": "text", "text": {"content": body[i:i + MAX_TEXT_CHARS]}}
        for i in range(0, len(body), MAX_TEXT_CHARS)
    ]


def _block(kind: str, text: str) -> dict:
    return {"object": "block", "type": kind, kind: {"rich_text": rich_text(text)}}


def markdown_to_blocks(text: str) -> list[dict]:
    """마크다운(과 브리핑 평문)을 노션 블록 목록으로 만든다.

    ⚠️ 표는 `_table_block()` 가 맡는다 (Task 5).
    """
    blocks: list[dict] = []
    lines = (text or "").split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if not stripped:
            index += 1
            continue

        if stripped.startswith("```"):
            language = stripped[3:].strip() or "plain text"
            body: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                body.append(lines[index])
                index += 1
            index += 1
            blocks.append({
                "object": "block", "type": "code",
                "code": {"language": _notion_language(language),
                         "rich_text": rich_text("\n".join(body))},
            })
            continue

        if stripped.startswith("|") and _is_table_start(lines, index):
            table, index = _table_block(lines, index)
            blocks.append(table)
            continue

        index += 1
        if stripped.startswith("### "):
            blocks.append(_block("heading_3", stripped[4:].strip()))
        elif stripped.startswith("## "):
            blocks.append(_block("heading_2", stripped[3:].strip()))
        elif stripped.startswith("# "):
            blocks.append(_block("heading_1", stripped[2:].strip()))
        elif stripped.startswith("> "):
            blocks.append(_block("quote", stripped[2:].strip()))
        elif stripped[:2] in ("- ", "* "):
            blocks.append(_block("bulleted_list_item", stripped[2:].strip()))
        elif re.match(r"^\d+\.\s", stripped):
            blocks.append(_block("numbered_list_item",
                                 re.sub(r"^\d+\.\s*", "", stripped)))
        elif stripped.startswith(_SUN):
            blocks.append(_block("heading_2", stripped))
        elif stripped.startswith(_SECTION_EMOJI):
            # 브리핑 평문의 절 머리말 — 소제목으로 올려야 하루가 위에서 아래로 읽힌다
            blocks.append(_block("heading_3", stripped))
        elif line.startswith("  "):
            # 브리핑 평문은 들여쓰기로 항목을 나타낸다
            blocks.append(_block("bulleted_list_item", stripped))
        else:
            blocks.append(_block("paragraph", stripped))
    return blocks


#: 노션 code 블록이 받는 언어 이름은 정해져 있다. 모르는 것은 통째로 거절당한다.
_LANGUAGES = {"python", "sql", "javascript", "typescript", "json", "bash",
              "shell", "html", "css", "markdown", "yaml", "java", "go"}


def _notion_language(name: str) -> str:
    lowered = (name or "").strip().lower()
    return lowered if lowered in _LANGUAGES else "plain text"


def chunk_blocks(blocks: list[dict],
                 size: int = MAX_BLOCKS_PER_REQUEST) -> list[list[dict]]:
    return [blocks[i:i + size] for i in range(0, len(blocks), size)]


def parse_page_url(url: str | None) -> str | None:
    """노션 URL(또는 맨 id)에서 32자 id 를 뽑아 UUID 형식으로 돌려준다.

    ⛔ **추측하지 않는다.** 못 뽑으면 None 을 주고 부르는 쪽이 되묻는다 —
       엉뚱한 페이지에 쓰는 것이 못 쓰는 것보다 나쁘다.
    ⛔ `*.notion.site` 는 외부 공개 사이트라 인테그레이션으로 쓸 수 없다.
       그대로 두면 404 만 반복한다 (2026-09-04 학습 쪽에서 이미 겪었다).
    """
    raw = (url or "").strip()
    if not raw or "notion.site" in raw.lower():
        return None

    # ⚠️ 쿼리(`?v=` 뷰 id)와 앵커를 먼저 버린다 — 거기에도 32자 hex 가 있다.
    path = raw.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    tail = path.rsplit("/", 1)[-1].replace("-", "")
    runs = _HEX_RUN.findall(tail)
    if not runs:
        return None
    compact = runs[-1][-32:]
    return (f"{compact[:8]}-{compact[8:12]}-{compact[12:16]}"
            f"-{compact[16:20]}-{compact[20:]}")


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
