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
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

NOTION_API = "https://api.notion.com"
NOTION_VERSION = "2025-09-03"
_TIMEOUT = 20.0

#: ⚠️ 서버 로컬 시간으로 날짜를 찍지 마라 — 호스트 TZ 가 바뀌면 하루가 어긋난다
#:    (이 저장소는 DB `time_zone` 이 SYSTEM 이라 같은 사고를 이미 겪었다).
KST = ZoneInfo("Asia/Seoul")

#: 노션이 한 요청에 받아 주는 블록 수. 넘기면 400 이 난다.
MAX_BLOCKS_PER_REQUEST = 100
#: rich_text 한 조각의 상한.
MAX_TEXT_CHARS = 2000


#: 32자 이상 이어지는 hex 덩어리. 슬러그(`회의록-24f1…`)에 붙어 있어도 잡힌다.
_HEX_RUN = re.compile(r"[0-9a-fA-F]{32,}")


# Task 3: 본문 정제
_DETAILS = re.compile(r"<details\b.*?</details\s*>", re.IGNORECASE | re.DOTALL)
_CHART_FENCE = re.compile(r"```chart\b.*?```", re.IGNORECASE | re.DOTALL)
#: 실제 후속 제안 칩 형식 (`work_briefing.py` 가 잔디 본문에서 걷어내는 것과 같다).
#: ⛔ 예전 정규식(`<!-- followup: -->`)은 앱이 내지 않는 형식이라 칩이 그대로 노션에 실렸다.
_FOLLOWUP = re.compile(r"(?m)^>.*이런 것도 물어보세요.*(?:\n>.*)*")
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


def _table_block(lines: list[str], index: int) -> tuple[dict, int, int]:
    """마크다운 표를 파싱하여 노션 표 블록으로. 세 번째 반환값은 **버린 행 수**다.

    ⛔ 노션은 한 요청에 블록 100개까지만 받는다. 넘치는 행은 자를 수밖에 없지만
       **조용히 자르지 않는다** — 부르는 쪽이 그 사실을 본문에 적는다.
    """
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

    shown = rows[:MAX_BLOCKS_PER_REQUEST]
    dropped = len(rows) - len(shown)

    children = []
    for row in shown:
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
    return block, cursor, dropped


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
            table, index, dropped = _table_block(lines, index)
            blocks.append(table)
            if dropped:
                # ⛔ 조용히 자르지 않는다 — 사람은 표를 보지 각주를 안 본다.
                blocks.append(_block(
                    "paragraph",
                    f"표가 길어 앞의 {MAX_BLOCKS_PER_REQUEST}행만 실었습니다 "
                    f"(머리행 포함 전체 {MAX_BLOCKS_PER_REQUEST + dropped}행)."))
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


def _request(method: str, path: str, body: dict | None = None,
             probe: bool = False) -> dict:
    """`probe=True` 는 **답이 예/아니오인 조회**다 (이 주소가 DB 인가?).

    ⛔ 그 400 을 WARNING 으로 남기지 마라 — 페이지 주소로 저장할 때마다
       **성공 경로에서** 뜬다. 매일 뜨는 경고는 곧 아무도 안 읽고,
       그러면 진짜 실패까지 함께 묻힌다. 예상 밖의 오류(403 등)는 그대로 남긴다.
    """
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
        expected = probe and _looks_like_a_page(error)
        (logger.debug if expected else logger.warning)(
            "notion_write_failed",
            kind=error.kind, status=response.status_code,
            path=path, message=str(payload.get("message", ""))[:200],
        )
        raise error
    return payload


# Task 6: 목적지 해석 — DB 인가 페이지인가
DB_TITLE = "셀라"

#: 우리가 만드는 DB 의 속성. ⛔ 사용자가 만든 DB 에는 이것을 강요하지 않는다.
DB_PROPERTIES = {
    "제목": {"title": {}},
    "날짜": {"date": {}},
    "종류": {"select": {"options": [{"name": "브리핑"}, {"name": "답변"}]}},
    "셀라 링크": {"url": {}},
}

_DDL = """
CREATE TABLE IF NOT EXISTS user_notion_databases (
    user_id INT NOT NULL,
    parent_id VARCHAR(40) NOT NULL,
    database_id VARCHAR(40) NOT NULL,
    data_source_id VARCHAR(40) NOT NULL DEFAULT '',
    title VARCHAR(120) NOT NULL DEFAULT '',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, parent_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def ensure_tables() -> None:
    from app.db.mariadb import execute

    try:
        execute(_DDL)
    except Exception as exc:                      # 기동을 막지 않는다
        logger.warning("notion_tables_error", error=str(exc)[:160])


@dataclass
class Target:
    database_id: str
    data_source_id: str
    properties: dict = field(default_factory=dict)   # {이름: 타입}
    created: bool = False


def _cached_database(user_id: int, parent_id: str) -> dict | None:
    ensure_tables()
    from app.db.mariadb import fetch_one

    try:
        return fetch_one(
            "SELECT database_id, data_source_id FROM user_notion_databases "
            "WHERE user_id=%s AND parent_id=%s", (user_id, parent_id))
    except Exception as exc:
        logger.warning("notion_cache_read_failed", error=str(exc)[:160])
        return None


def _remember_database(user_id: int, parent_id: str, target: Target) -> None:
    ensure_tables()
    from app.db.mariadb import execute

    try:
        execute(
            "INSERT INTO user_notion_databases "
            "(user_id, parent_id, database_id, data_source_id, title) "
            "VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE "
            "database_id=VALUES(database_id), data_source_id=VALUES(data_source_id)",
            (user_id, parent_id, target.database_id, target.data_source_id, DB_TITLE))
    except Exception as exc:
        logger.warning("notion_cache_write_failed", error=str(exc)[:160])


def _schema(payload: dict) -> dict:
    return {name: (value or {}).get("type", "")
            for name, value in (payload.get("properties") or {}).items()}


def _first_data_source(payload: dict) -> str:
    sources = payload.get("data_sources") or []
    return str(sources[0].get("id", "")) if sources else ""


def _fetch_schema(data_source_id: str, fallback_payload: dict) -> dict:
    """데이터 소스에서 스키마를 읽는다. 실패하거나 비면 DB payload 의 것으로 물러선다.

    ⛔ **스키마는 데이터베이스가 아니라 데이터 소스에 있다** (`2025-09-03`).
       DB 객체의 `properties` 를 믿으면 빈 스키마를 받아 저장이 "제목 속성이 없는 DB"
       로 죽는다 — 기존 DB 에 대한 저장이 **전부** 실패한다.
    ⚠️ 폴백을 남긴다: 데이터 소스 조회가 실패하거나 비면 DB payload 의 properties 를 쓴다.
    """
    if not data_source_id:
        return _schema(fallback_payload)
    schema = {}
    try:
        schema = _schema(_request("GET", f"/v1/data_sources/{data_source_id}"))
    except NotionError as exc:
        logger.warning("notion_data_source_read_failed",
                       kind=exc.kind, data_source_id=data_source_id)
    return schema or _schema(fallback_payload)


def _load_database(database_id: str, created: bool = False,
                   probe: bool = False) -> Target:
    """DB 를 읽어 Target 을 만든다. 스키마는 `_fetch_schema()` 가 데이터 소스에서 얻는다."""
    payload = _request("GET", f"/v1/databases/{database_id}", probe=probe)
    data_source_id = _first_data_source(payload)
    return Target(database_id=str(payload.get("id", database_id)),
                  data_source_id=data_source_id,
                  properties=_fetch_schema(data_source_id, payload), created=created)


def _find_child_database(parent_id: str) -> str:
    """부모의 자식 블록에서 제목이 `셀라` 인 DB 를 찾는다.

    ⛔ 캐시가 없다고 곧바로 만들지 마라 — 사용자 페이지에 DB 가 여러 개 생긴다.
    """
    cursor, guard = None, 0
    while guard < 10:
        guard += 1
        path = f"/v1/blocks/{parent_id}/children?page_size=100"
        if cursor:
            path += f"&start_cursor={cursor}"
        payload = _request("GET", path)
        for block in payload.get("results") or []:
            if block.get("type") != "child_database":
                continue
            title = (block.get("child_database") or {}).get("title", "")
            if title.strip() == DB_TITLE:
                return str(block.get("id", ""))
        if not payload.get("has_more"):
            return ""                     # 끝까지 봤는데 없다 — 정상이다
        cursor = payload.get("next_cursor")

    # ⛔ 여기 닿았다는 것은 1,000블록을 다 보고도 못 찾았다는 뜻이다. 그대로 "" 를
    #    주면 부르는 쪽이 DB 를 새로 만들어 **중복이 생긴다** — 흔적을 반드시 남긴다.
    #    (이 저장소는 프로덕션에서 INFO 를 버리므로 WARNING 이어야 한다)
    logger.warning("notion_child_scan_truncated", parent_id=parent_id, pages=guard)
    return ""


#: `GET /v1/databases/{id}` 가 "이건 페이지다" 라고 답하는 두 가지 방식.
#: ⚠️ 404 는 "연결이 안 붙었다" 일 수도 있는데, 그때도 아래 페이지 경로가
#:    `GET /v1/pages/{id}` 로 다시 확인하므로 여기서 넘어가도 안전하다.
def _looks_like_a_page(exc: NotionError) -> bool:
    if exc.kind == "not_connected":
        return True
    message = str(exc).lower()
    return exc.kind in ("bad_request", "bad_property") and (
        "is a page" in message or "not a database" in message
    )


def resolve_target(user_id: int, url: str) -> Target:
    """준 URL 이 DB 면 그대로, 페이지면 그 아래 `셀라` DB 를 한 번 만든다.

    ⛔ **URL 문자열만으로는 DB 와 페이지를 구분할 수 없다** — 둘 다 32자 id 하나다.
       추측하지 말고 물어서 확인한다.
    """
    page_id = parse_page_url(url)
    if not page_id:
        raise NotionError("bad_request", "노션 주소를 읽지 못했다")

    try:
        return _load_database(page_id, probe=True)
    except NotionError as exc:
        # ⛔ 노션은 "이건 DB 가 아니라 페이지다" 를 **404 가 아니라 400** 으로 답한다
        #    (2026-09-08 실측: "Provided database_id … is a page, not a database").
        #    404 만 보고 넘어가면 **페이지 주소가 통째로 죽는다** — 실제로 그랬다.
        if not _looks_like_a_page(exc):
            raise                                  # 403 등 진짜 오류는 그대로 올린다

    cached = _cached_database(user_id, page_id)
    if cached and cached.get("database_id"):
        try:
            # ⛔ 캐시에는 **스키마가 없다.** 스키마 없이 돌려주면 저장 단계가
            #    "제목 속성이 없는 DB" 로 죽는다 — 첫 저장만 되고 그 뒤로 전부 실패한다.
            #    이 함수는 어차피 위에서 한 번 API 를 타므로 한 번 더 읽어도 된다.
            #    캐시가 막는 것은 호출 수가 아니라 **DB 중복 생성과 자식 전수 스캔**이다.
            return _load_database(str(cached["database_id"]))
        except NotionError:
            pass                                   # 지워졌다 — 아래에서 다시 찾거나 만든다

    _request("GET", f"/v1/pages/{page_id}")        # 페이지가 맞는지·닿는지 확인
    found = _find_child_database(page_id)
    if found:
        target = _load_database(found)
        _remember_database(user_id, page_id, target)
        return target

    payload = _request("POST", "/v1/databases", {
        "parent": {"type": "page_id", "page_id": page_id},
        "title": [{"type": "text", "text": {"content": DB_TITLE}}],
        "initial_data_source": {"properties": DB_PROPERTIES},
    })
    # ⚠️ 생성 응답에도 스키마는 데이터 소스에 있다 — `_load_database` 와 같은 경로로
    #    읽는다. 데이터 소스 조회가 비거나 실패하면 우리가 요청한 `DB_PROPERTIES` 로
    #    물러선다 (방금 만든 DB 이므로 이 폴백은 실제 스키마와 같다).
    data_source_id = _first_data_source(payload)
    schema = _fetch_schema(data_source_id, payload) or {
        name: list(spec)[0] for name, spec in DB_PROPERTIES.items()}
    target = Target(database_id=str(payload.get("id", "")),
                    data_source_id=data_source_id,
                    properties=schema,
                    created=True)
    _remember_database(user_id, page_id, target)
    return target


# Task 7: 저장 — 행 생성과 속성 채우기
@dataclass
class SaveResult:
    url: str
    created_database: bool = False
    skipped: list = field(default_factory=list)


def _title_property_name(properties: dict) -> str:
    """⛔ 이름이 무엇이든 `type == "title"` 인 속성을 찾는다.

    사용자가 만든 DB 는 `Name`·`이름`·`문서명` 무엇이든 될 수 있다.
    """
    for name, kind in (properties or {}).items():
        if kind == "title":
            return name
    return ""


def _properties_for(target: Target, title: str, kind: str,
                    link: str) -> tuple[dict, list]:
    """있는 것만 채운다. 없는 속성은 **보내지 않고** 이름만 돌려준다."""
    schema = target.properties or {}
    title_name = _title_property_name(schema)
    if not title_name:
        # ⛔ `bad_request` 로 나가면 "노션 주소를 읽지 못했습니다" 문구가 붙어
        #    사용자는 멀쩡한 URL 을 영원히 다시 붙여넣는다. 원인이 다르므로 문구도 갈랐다.
        raise NotionError("bad_property", "제목 속성이 없는 DB 다")

    props = {title_name: {"title": [{"type": "text",
                                     "text": {"content": (title or "제목 없음")[:200]}}]}}
    wanted = {
        "날짜": ("date", {"date": {"start": datetime.now(KST).date().isoformat()}}),
        "종류": ("select", {"select": {"name": kind}}),
        "셀라 링크": ("url", {"url": link or None}),
    }
    skipped = []
    for name, (want_type, value) in wanted.items():
        if schema.get(name) == want_type:
            props[name] = value
        else:
            skipped.append(name)
    return props, skipped


def save(target: Target, title: str, text: str, kind: str = "답변",
         link: str = "") -> SaveResult:
    """DB 에 행 하나를 만들고 본문 블록을 넣는다."""
    if not target.data_source_id:
        # ⛔ 빈 값으로 POST 하면 400 이 나고, 그 문구가 "제목 속성이 없는 DB" 처럼
        #    오해하기 좋게 나간다 — API 를 부르기 전에 막는다.
        raise NotionError("bad_property", "데이터 소스를 찾지 못했다")
    blocks = markdown_to_blocks(clean_for_notion(text))
    props, skipped = _properties_for(target, title, kind, link)

    row = _request("POST", "/v1/pages", {
        "parent": {"type": "data_source_id", "data_source_id": target.data_source_id},
        "properties": props,
        "children": blocks[:MAX_BLOCKS_PER_REQUEST],
    })
    row_id = str(row.get("id", ""))
    for chunk in chunk_blocks(blocks[MAX_BLOCKS_PER_REQUEST:]):
        _request("PATCH", f"/v1/blocks/{row_id}/children", {"children": chunk})

    return SaveResult(url=str(row.get("url", "")),
                      created_database=target.created, skipped=skipped)
