# 노션 저장 1단계 — 저장 엔진 + 채팅 저장 경로 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 채팅에서 "이 답변 노션에 넣어줘" 라고 하면, 되묻고 받은 노션 URL의 DB에 그 답변을 행으로 저장한다.

**Architecture:** `app/core/notion_export.py` 가 노션 쓰기 전부를 맡는다(URL 파싱 → 목적지 해석 → 마크다운을 블록으로 → 행 생성). `app/core/notion_save.py` 는 채팅 판정과 되묻기만 맡고 저장은 엔진에 넘긴다. 오케스트레이터는 라우터보다 **먼저** 이 관문을 부르며, 배선은 일반·스트리밍 **두 경로 모두**에 건다.

**Tech Stack:** Python 3 · httpx(동기) · MariaDB(`app.db.mariadb`) · pytest · Notion API `2025-09-03`

**Spec:** `docs/superpowers/specs/2026-09-07-notion-export-design.md`

## Global Constraints

- 쓰기 요청 헤더는 **`Notion-Version: 2025-09-03`**. 읽기 경로(`app/core/product_info.py`, `app/agents/notion_agent.py`, `scripts/notion_qdrant_pipeline.py`)의 `2022-06-28` 은 **건드리지 않는다**.
- DB 행의 부모는 **`{"type": "data_source_id", "data_source_id": ...}`**. ⛔ `database_id` 부모는 쓰지 않는다 — 옛 버전 전용이고 단일 소스 DB에서만 동작한다.
- 토큰은 `settings.notion_write_token`(`.env` 의 `NOTION_WRITE_TOKEN`). **비어 있으면 기능이 꺼진 상태로 동작한다** — 안내 문구를 돌려주고 예외를 올리지 않는다.
- ⛔ 본문에서 `<details>실행된 쿼리</details>` 를 반드시 제거한다 (내부 테이블 경로 노출 금지).
- ⛔ **사용자가 만든 DB의 스키마를 고치지 않는다.** `type == "title"` 인 속성만 이름과 무관하게 찾아 채우고, 나머지는 이름·타입이 맞을 때만 채운다.
- 노션 제약: 한 요청에 블록 **100개**, `rich_text` 하나에 **2,000자**.
- `*.notion.site` 주소는 거절한다 (인테그레이션으로 쓸 수 없다).
- ⚠️ **글자 막대는 노션에 넣지 않는다.** 잔디가 `█`/`░` 를 쓰는 이유는 평문 매체라
  표를 못 그리기 때문이다 — 노션은 표를 표로 그리므로(Task 5) 그림이 필요 없다.
  차트 **설정 JSON** 만 걷어낸다(Task 3).
- ⛔ **이 작업트리를 다른 세션이 함께 쓴다.** 커밋은 반드시 `git add <경로>` 로 지목한다. `git add -A`·`git add .`·`commit -a` 금지.

---

## File Structure

| 파일 | 책임 |
|---|---|
| `app/config.py` (수정) | `notion_write_token` 설정 한 줄 |
| `app/core/notion_export.py` (신규) | 노션 쓰기 전부 — HTTP·URL 파싱·블록 변환·목적지 해석·행 생성 |
| `app/core/notion_save.py` (신규) | 채팅 판정·되묻기 마커·핸들러. 저장은 엔진에 위임 |
| `app/agents/orchestrator.py` (수정 2곳) | 관문 배선 (1126행·1411행 직후) |
| `scripts/notion_write_probe.py` (신규) | 실 API 관통 확인 도구 (수동 실행) |
| `tests/test_notion_export.py` (신규) | 엔진 회귀 |
| `tests/test_notion_save.py` (신규) | 관문·되묻기·배선 회귀 |

---

### Task 1: 설정 + HTTP 계층 + 실 API 관통 확인

**Files:**
- Modify: `app/config.py:79` (`notion_mcp_token` 바로 아래)
- Create: `app/core/notion_export.py`
- Create: `scripts/notion_write_probe.py`
- Test: `tests/test_notion_export.py`

**Interfaces:**
- Consumes: `app.config.settings`
- Produces:
  - `NOTION_VERSION = "2025-09-03"`
  - `class NotionError(Exception)` — `.kind` 가 `"disabled"|"not_connected"|"forbidden"|"bad_property"|"bad_request"|"unavailable"`, `.status: int`
  - `is_enabled() -> bool`
  - `_headers() -> dict`
  - `_request(method: str, path: str, body: dict | None = None) -> dict`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_notion_export.py` 를 만든다:

```python
# -*- coding: utf-8 -*-
"""노션 쓰기 엔진 회귀.

⛔ 이 테스트는 네트워크를 타지 않는다. `_request` 를 monkeypatch 해서
   우리가 **보내는 것**과 **받은 것을 해석하는 방식**만 검사한다.
   실 API 관통은 `scripts/notion_write_probe.py` 가 수동으로 한다.
"""
import pytest

from app.core import notion_export as nx


def test_write_path_pins_2025_version():
    """⛔ 옛 버전으로 DB 행을 만들면 단일 소스 DB 에서만 동작한다."""
    assert nx.NOTION_VERSION == "2025-09-03"


def test_headers_carry_token_and_version(monkeypatch):
    monkeypatch.setattr(nx.settings, "notion_write_token", "secret-token")
    headers = nx._headers()
    assert headers["Authorization"] == "Bearer secret-token"
    assert headers["Notion-Version"] == "2025-09-03"


def test_disabled_when_token_missing(monkeypatch):
    monkeypatch.setattr(nx.settings, "notion_write_token", "")
    assert nx.is_enabled() is False


def test_read_paths_keep_old_version():
    """읽기 경로는 그대로 둔다 — 버전은 요청마다 붙는 값이라 섞여도 된다."""
    from app.core import product_info

    assert product_info.NOTION_VERSION == "2022-06-28"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_notion_export.py -v`
Expected: FAIL — `ModuleNotFoundError: app.core.notion_export`

- [ ] **Step 3: 설정을 추가한다**

`app/config.py` 의 `notion_mcp_token: str = ""` 바로 아래:

```python
    # 노션 쓰기 전용 인테그레이션 (읽기용 notion_mcp_token 과 분리한다 —
    # 크롤러 토큰이 개인 페이지 쓰기 권한을 갖는 구조를 만들지 않는다)
    notion_write_token: str = ""
```

- [ ] **Step 4: 엔진의 HTTP 계층을 만든다**

`app/core/notion_export.py`:

```python
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

from app.config import settings

logger = structlog.get_logger(__name__)

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
```

- [ ] **Step 5: 테스트 통과를 확인한다**

Run: `python -m pytest tests/test_notion_export.py -v`
Expected: PASS (4건)

- [ ] **Step 6: 관통 확인 도구를 만든다**

`scripts/notion_write_probe.py`:

```python
# -*- coding: utf-8 -*-
"""노션 쓰기 관통 확인 — 수동 실행.

이 프로젝트는 `2025-09-03` 을 써 본 적이 없다(전부 `2022-06-28` 읽기 전용).
DB 생성 → data_source_id 획득 → 행 생성까지 한 번 관통시켜 본 뒤 나머지를 짓는다.

    python scripts/notion_write_probe.py <노션_페이지_URL>

⚠️ 콘솔이 cp949 다 — 아래 래핑이 없으면 한글 출력에서 죽는다.
"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from app.core import notion_export as nx  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("사용법: python scripts/notion_write_probe.py <노션_페이지_URL>")
        return 2
    if not nx.is_enabled():
        print("NOTION_WRITE_TOKEN 이 비어 있다. .env 에 넣고 다시 실행할 것.")
        return 2

    page_id = nx.parse_page_url(sys.argv[1])
    print(f"page_id = {page_id}")
    if not page_id:
        return 2

    created = nx._request("POST", "/v1/databases", {
        "parent": {"type": "page_id", "page_id": page_id},
        "title": [{"type": "text", "text": {"content": "셀라 (probe)"}}],
        "initial_data_source": {"properties": nx.DB_PROPERTIES},
    })
    print(f"database_id      = {created.get('id')}")
    sources = created.get("data_sources") or []
    print(f"data_sources     = {sources}")
    if not sources:
        print("⛔ data_sources 가 비었다 — 응답 형태를 확인할 것")
        return 1

    row = nx._request("POST", "/v1/pages", {
        "parent": {"type": "data_source_id", "data_source_id": sources[0]["id"]},
        "properties": {"제목": {"title": [{"type": "text",
                                          "text": {"content": "관통 확인"}}]}},
        "children": [{"object": "block", "type": "paragraph",
                      "paragraph": {"rich_text": [{"type": "text",
                                                   "text": {"content": "성공"}}]}}],
    })
    print(f"row url          = {row.get('url')}")
    print("✅ 관통 성공 — 만들어진 probe DB 는 노션에서 지워도 된다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

⚠️ 이 도구는 `parse_page_url` 과 `DB_PROPERTIES` 를 쓴다. **Task 2·6을 마친 뒤에 실행한다** (Task 11에서 실행한다). 지금은 파일만 만든다.

- [ ] **Step 7: 커밋**

```bash
git add app/config.py app/core/notion_export.py scripts/notion_write_probe.py tests/test_notion_export.py
git commit -m "feat(notion): 쓰기 전용 토큰과 2025-09-03 HTTP 계층"
```

---

### Task 2: 노션 URL → page id

**Files:**
- Modify: `app/core/notion_export.py`
- Test: `tests/test_notion_export.py`

**Interfaces:**
- Produces: `parse_page_url(url: str) -> str | None` — 대시 넣은 UUID 문자열 또는 `None`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_notion_export.py` 끝에 붙인다:

```python
_ID = "24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b"
_UUID = "24f1a2b3-c4d5-4e6f-8a9b-0c1d2e3f4a5b"


@pytest.mark.parametrize("url", [
    f"https://www.notion.so/{_ID}",
    f"https://www.notion.so/회의록-{_ID}",
    f"https://www.notion.so/workspace/My-Notes-{_ID}?v=99991111222233334444555566667777",
    f"https://www.notion.so/{_ID}?pvs=4#abc",
    f"https://www.notion.so/{_UUID}",
    _ID,
])
def test_parse_page_url_accepts_real_shapes(url):
    assert nx.parse_page_url(url) == _UUID


@pytest.mark.parametrize("url", [
    "",
    None,
    "https://example.com/nothing",
    "그냥 아무 말",
    f"https://skin1004.notion.site/{_ID}",   # ⛔ 외부 공개 사이트는 쓸 수 없다
])
def test_parse_page_url_rejects(url):
    assert nx.parse_page_url(url) is None


def test_parse_page_url_takes_the_id_not_the_view():
    """⚠️ `?v=` 뒤에도 32자 hex 가 있다 — 뷰 id 를 페이지로 읽으면 404 가 난다."""
    url = f"https://www.notion.so/{_ID}?v=99991111222233334444555566667777"
    assert nx.parse_page_url(url) == _UUID
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_notion_export.py -k parse -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'parse_page_url'`

- [ ] **Step 3: 구현한다**

`app/core/notion_export.py` 의 `is_enabled()` 앞에 넣는다:

```python
#: 32자 이상 이어지는 hex 덩어리. 슬러그(`회의록-24f1…`)에 붙어 있어도 잡힌다.
_HEX_RUN = re.compile(r"[0-9a-fA-F]{32,}")


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
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_notion_export.py -k parse -v`
Expected: PASS (11건)

- [ ] **Step 5: 커밋**

```bash
git add app/core/notion_export.py tests/test_notion_export.py
git commit -m "feat(notion): 노션 URL 에서 page id 를 뽑는다"
```

---

### Task 3: 본문 정제 — 노션에 실으면 안 되는 것을 걷는다

**Files:**
- Modify: `app/core/notion_export.py`
- Test: `tests/test_notion_export.py`

**Interfaces:**
- Produces: `clean_for_notion(text: str) -> str`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_clean_drops_the_query_details_block():
    """⛔ 노션에서는 평문으로 펼쳐져 내부 테이블 경로가 페이지에 남는다."""
    text = (
        "2026년 매출은 1,234억원입니다.\n\n"
        "<details><summary>실행된 쿼리</summary>\n\n"
        "```sql\nSELECT SUM(Sales1_R) FROM `skin1004-319714.SALES_ALL_Backup.x`\n```\n\n"
        "</details>\n"
    )
    cleaned = nx.clean_for_notion(text)
    assert "1,234억원" in cleaned
    assert "skin1004-319714" not in cleaned
    assert "실행된 쿼리" not in cleaned


def test_clean_drops_chart_config_fence_and_orphan_heading():
    text = (
        "## 시각화\n"
        "```chart\n{\"type\": \"bar\", \"labels\": [1, 2]}\n```\n"
        "## 요약\n본문\n"
    )
    cleaned = nx.clean_for_notion(text)
    assert "chart" not in cleaned
    assert "시각화" not in cleaned   # 차트를 뺐으면 홀로 남는 제목도 걷는다
    assert "## 요약" in cleaned


def test_clean_keeps_sql_fence_that_is_not_a_query_block():
    """코드 자체를 물어본 답변까지 지우면 안 된다."""
    text = "이렇게 쓰세요:\n```python\nprint(1)\n```\n"
    assert "print(1)" in nx.clean_for_notion(text)


def test_clean_drops_followup_chips():
    text = "본문입니다.\n\n<!-- followup: [\"다음 질문\"] -->\n"
    assert "followup" not in nx.clean_for_notion(text)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_notion_export.py -k clean -v`
Expected: FAIL — `has no attribute 'clean_for_notion'`

- [ ] **Step 3: 구현한다**

```python
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
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_notion_export.py -k clean -v`
Expected: PASS (4건)

- [ ] **Step 5: 커밋**

```bash
git add app/core/notion_export.py tests/test_notion_export.py
git commit -m "feat(notion): 본문에서 쿼리 블록과 차트 설정을 걷는다"
```

---

### Task 4: 마크다운 → 노션 블록 (표 제외)

**Files:**
- Modify: `app/core/notion_export.py`
- Test: `tests/test_notion_export.py`

**Interfaces:**
- Produces:
  - `rich_text(text: str) -> list[dict]` — 2,000자마다 쪼갠 조각
  - `markdown_to_blocks(text: str) -> list[dict]`
  - `chunk_blocks(blocks: list[dict], size: int = 100) -> list[list[dict]]`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def _types(blocks):
    return [b["type"] for b in blocks]


def _plain(block):
    key = block["type"]
    return "".join(r["text"]["content"] for r in block[key]["rich_text"])


def test_markdown_maps_headings_lists_and_quote():
    blocks = nx.markdown_to_blocks(
        "# 제목\n## 소제목\n본문 문단\n- 첫째\n- 둘째\n1. 하나\n> 인용\n"
    )
    assert _types(blocks) == [
        "heading_1", "heading_2", "paragraph",
        "bulleted_list_item", "bulleted_list_item",
        "numbered_list_item", "quote",
    ]
    assert _plain(blocks[0]) == "제목"
    assert _plain(blocks[3]) == "첫째"


def test_markdown_keeps_code_fence_as_code_block():
    blocks = nx.markdown_to_blocks("```python\nprint(1)\nprint(2)\n```\n")
    assert _types(blocks) == ["code"]
    assert "print(1)\nprint(2)" == _plain(blocks[0])


def test_briefing_plaintext_becomes_headings_and_bullets():
    """`render_markdown()` 산출물은 이름과 달리 **평문**이다.

    ⛔ 노션용 렌더러를 따로 만들면 사본이 갈린다 — 변환기가 흡수한다.
    """
    blocks = nx.markdown_to_blocks(
        "☀️ 오늘의 출근 브리핑 (2026-09-07 월요일)\n"
        "\n"
        "📅 오늘의 일정 · 2건\n"
        "  10:00 | 주간 회의\n"
        "      장소 3층\n"
    )
    assert _types(blocks) == [
        "heading_2", "heading_3", "bulleted_list_item", "bulleted_list_item",
    ]
    assert _plain(blocks[1]) == "📅 오늘의 일정 · 2건"


def test_rich_text_splits_at_2000_chars():
    parts = nx.rich_text("가" * 4500)
    assert [len(p["text"]["content"]) for p in parts] == [2000, 2000, 500]


def test_long_paragraph_stays_one_block_with_split_rich_text():
    blocks = nx.markdown_to_blocks("나" * 3000)
    assert len(blocks) == 1
    assert len(blocks[0]["paragraph"]["rich_text"]) == 2


def test_chunk_blocks_respects_the_100_limit():
    blocks = nx.markdown_to_blocks("\n".join(f"- 줄 {i}" for i in range(250)))
    chunks = nx.chunk_blocks(blocks)
    assert [len(c) for c in chunks] == [100, 100, 50]


def test_blank_lines_do_not_become_blocks():
    assert nx.markdown_to_blocks("첫 줄\n\n\n둘째 줄\n") == nx.markdown_to_blocks(
        "첫 줄\n둘째 줄\n")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_notion_export.py -k "markdown or rich_text or chunk or blank or briefing_plaintext" -v`
Expected: FAIL — `has no attribute 'rich_text'`

- [ ] **Step 3: 구현한다**

```python
#: 브리핑 평문의 절 머리말. 이 글자로 시작하는 줄은 소제목으로 올린다.
_SECTION_EMOJI = ("📅", "✉️", "✅", "⏰", "📊", "💱", "📌")
_SUN = "☀️"


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

    ⚠️ 표는 `_table_blocks()` 가 맡는다 (Task 5).
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
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_notion_export.py -v`
Expected: PASS (전부)

- [ ] **Step 5: 커밋**

```bash
git add app/core/notion_export.py tests/test_notion_export.py
git commit -m "feat(notion): 마크다운과 브리핑 평문을 노션 블록으로 바꾼다"
```

---

### Task 5: 표를 노션 표 블록으로

**Files:**
- Modify: `app/core/notion_export.py`
- Test: `tests/test_notion_export.py`

**Interfaces:**
- Produces: `markdown_to_blocks()` 가 표를 `table` 블록으로 낸다 (시그니처 변화 없음)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
_TABLE_MD = (
    "| 국가 | 매출 |\n"
    "|---|---|\n"
    "| 일본 | 55.1억 |\n"
    "| 미국 | 32.0억 |\n"
)


def test_table_becomes_a_notion_table_block():
    """⛔ 표가 코드블록으로 들어가면 노션에서 정렬도 복사도 안 되는 글자 더미다."""
    blocks = nx.markdown_to_blocks(_TABLE_MD)
    assert _types(blocks) == ["table"]
    table = blocks[0]["table"]
    assert table["table_width"] == 2
    assert table["has_column_header"] is True
    rows = table["children"]
    assert len(rows) == 3                      # 머리행 + 본문 2행
    assert rows[0]["table_row"]["cells"][0][0]["text"]["content"] == "국가"
    assert rows[2]["table_row"]["cells"][1][0]["text"]["content"] == "32.0억"


def test_table_rows_are_padded_to_the_declared_width():
    """⚠️ 셀 수가 table_width 와 다르면 400 이 난다 — 채우거나 자른다."""
    blocks = nx.markdown_to_blocks(
        "| a | b | c |\n|---|---|---|\n| 1 |\n| 1 | 2 | 3 | 4 |\n")
    rows = blocks[0]["table"]["children"]
    assert all(len(r["table_row"]["cells"]) == 3 for r in rows)


def test_text_around_a_table_is_kept():
    blocks = nx.markdown_to_blocks("앞 문단\n" + _TABLE_MD + "뒤 문단\n")
    assert _types(blocks) == ["paragraph", "table", "paragraph"]


def test_separator_line_alone_is_not_a_table():
    """구분선처럼 생긴 줄 하나로 표를 만들지 않는다."""
    assert _types(nx.markdown_to_blocks("|---|\n")) == ["paragraph"]
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_notion_export.py -k table -v`
Expected: FAIL — `assert ['paragraph', 'paragraph', ...] == ['table']`

- [ ] **Step 3: 구현한다**

`markdown_to_blocks()` 의 코드펜스 분기 **바로 아래**에 넣는다:

```python
        if stripped.startswith("|") and _is_table_start(lines, index):
            table, index = _table_block(lines, index)
            blocks.append(table)
            continue
```

그리고 모듈에 함수를 더한다:

```python
_SEPARATOR = re.compile(r"^\|?[\s:\-|]+\|?$")


def _cells(line: str) -> list[str]:
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
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_notion_export.py -v`
Expected: PASS (전부)

- [ ] **Step 5: 커밋**

```bash
git add app/core/notion_export.py tests/test_notion_export.py
git commit -m "feat(notion): 마크다운 표를 노션 표 블록으로 만든다"
```

---

### Task 6: 목적지 해석 — DB인가 페이지인가

**Files:**
- Modify: `app/core/notion_export.py`
- Test: `tests/test_notion_export.py`

**Interfaces:**
- Produces:
  - `DB_PROPERTIES: dict` — 우리가 만드는 DB의 속성 정의
  - `DB_TITLE = "셀라"`
  - `@dataclass Target(database_id: str, data_source_id: str, properties: dict[str, str], created: bool = False)` — `properties` 는 `{이름: 타입}`
  - `resolve_target(user_id: int, url: str) -> Target`
  - `_cached_database(user_id: int, parent_id: str) -> dict | None`
  - `_remember_database(user_id: int, parent_id: str, target: Target) -> None`
  - `ensure_tables() -> None`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def _stub_requests(monkeypatch, handler):
    calls = []

    def fake(method, path, body=None):
        calls.append((method, path, body))
        return handler(method, path, body)

    monkeypatch.setattr(nx, "_request", fake)
    return calls


@pytest.fixture
def no_cache(monkeypatch):
    """DB 는 테스트에서 타지 않는다 — 캐시 함수만 갈아 끼운다."""
    store = {}
    monkeypatch.setattr(nx, "_cached_database",
                        lambda uid, pid: store.get((uid, pid)))
    monkeypatch.setattr(
        nx, "_remember_database",
        lambda uid, pid, target: store.__setitem__(
            (uid, pid), {"database_id": target.database_id,
                         "data_source_id": target.data_source_id}))
    return store


def test_db_url_is_used_as_is(monkeypatch, no_cache):
    def handler(method, path, body=None):
        if path == f"/v1/databases/{_UUID}":
            return {"id": _UUID, "data_sources": [{"id": "ds-1", "name": "셀라"}],
                    "properties": {"이름": {"type": "title"}}}
        raise AssertionError(f"불필요한 호출: {method} {path}")

    _stub_requests(monkeypatch, handler)
    target = nx.resolve_target(7, f"https://www.notion.so/{_ID}")
    assert (target.database_id, target.data_source_id) == (_UUID, "ds-1")
    assert target.created is False
    assert target.properties == {"이름": "title"}


def test_page_url_creates_the_database_once(monkeypatch, no_cache):
    created = {"count": 0}

    def handler(method, path, body=None):
        if path == f"/v1/databases/{_UUID}":
            raise nx.NotionError("not_connected", "not a database", 404)
        if path == f"/v1/pages/{_UUID}":
            return {"id": _UUID, "object": "page"}
        if path == f"/v1/blocks/{_UUID}/children":
            return {"results": []}
        if method == "POST" and path == "/v1/databases":
            created["count"] += 1
            # ⚠️ 노션 응답의 속성 형태는 `{이름: {"type": "title", ...}}` 다.
            #    `DB_PROPERTIES` 의 요청 형태(`{"title": {}}`)와 다르다.
            return {"id": "db-new", "data_sources": [{"id": "ds-new"}],
                    "properties": {name: {"type": list(spec)[0]}
                                   for name, spec in nx.DB_PROPERTIES.items()}}
        raise AssertionError(f"불필요한 호출: {method} {path}")

    _stub_requests(monkeypatch, handler)
    first = nx.resolve_target(7, f"https://www.notion.so/{_ID}")
    assert first.created is True
    assert created["count"] == 1

    # ⛔ 두 번째 저장에서 DB 가 또 생기면 안 된다 — 가장 흔할 실수다.
    second = nx.resolve_target(7, f"https://www.notion.so/{_ID}")
    assert created["count"] == 1
    assert second.database_id == "db-new"
    assert second.created is False


def test_existing_child_database_is_reused_without_cache(monkeypatch, no_cache):
    """캐시가 비어도 부모에서 찾는다 — 사용자가 DB 를 옮겨도 회복한다."""
    def handler(method, path, body=None):
        if path == f"/v1/databases/{_UUID}":
            raise nx.NotionError("not_connected", "", 404)
        if path == f"/v1/pages/{_UUID}":
            return {"id": _UUID}
        if path == f"/v1/blocks/{_UUID}/children":
            return {"results": [
                {"type": "child_page", "child_page": {"title": "셀라"}},
                {"id": "db-old", "type": "child_database",
                 "child_database": {"title": "셀라"}},
            ]}
        if path == "/v1/databases/db-old":
            return {"id": "db-old", "data_sources": [{"id": "ds-old"}],
                    "properties": {"제목": {"type": "title"}}}
        raise AssertionError(f"불필요한 호출: {method} {path}")

    _stub_requests(monkeypatch, handler)
    target = nx.resolve_target(7, f"https://www.notion.so/{_ID}")
    assert (target.database_id, target.created) == ("db-old", False)


def test_bad_url_raises_before_any_call(monkeypatch, no_cache):
    _stub_requests(monkeypatch, lambda *a, **k: pytest.fail("호출하면 안 된다"))
    with pytest.raises(nx.NotionError) as exc:
        nx.resolve_target(7, "https://example.com/x")
    assert exc.value.kind == "bad_request"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_notion_export.py -k "target or database" -v`
Expected: FAIL — `has no attribute 'DB_PROPERTIES'`

- [ ] **Step 3: 구현한다**

```python
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
    from app.db.mariadb import fetch_one

    try:
        return fetch_one(
            "SELECT database_id, data_source_id FROM user_notion_databases "
            "WHERE user_id=%s AND parent_id=%s", (user_id, parent_id))
    except Exception as exc:
        logger.warning("notion_cache_read_failed", error=str(exc)[:160])
        return None


def _remember_database(user_id: int, parent_id: str, target: Target) -> None:
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


def _load_database(database_id: str, created: bool = False) -> Target:
    payload = _request("GET", f"/v1/databases/{database_id}")
    return Target(database_id=str(payload.get("id", database_id)),
                  data_source_id=_first_data_source(payload),
                  properties=_schema(payload), created=created)


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
            break
        cursor = payload.get("next_cursor")
    return ""


def resolve_target(user_id: int, url: str) -> Target:
    """준 URL 이 DB 면 그대로, 페이지면 그 아래 `셀라` DB 를 한 번 만든다.

    ⛔ **URL 문자열만으로는 DB 와 페이지를 구분할 수 없다** — 둘 다 32자 id 하나다.
       추측하지 말고 물어서 확인한다.
    """
    page_id = parse_page_url(url)
    if not page_id:
        raise NotionError("bad_request", "노션 주소를 읽지 못했다")

    try:
        return _load_database(page_id)
    except NotionError as exc:
        if exc.kind != "not_connected":
            raise                                  # 403 은 그대로 올린다

    cached = _cached_database(user_id, page_id)
    if cached and cached.get("database_id"):
        try:
            return _load_database(str(cached["database_id"]))
        except NotionError:
            pass                                   # 지워졌다 — 아래에서 다시 찾는다

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
    target = Target(database_id=str(payload.get("id", "")),
                    data_source_id=_first_data_source(payload),
                    properties=_schema(payload) or {
                        name: list(spec)[0] for name, spec in DB_PROPERTIES.items()},
                    created=True)
    _remember_database(user_id, page_id, target)
    return target
```

`app/main.py` 의 기동 테이블 생성부(다른 `ensure_*` 호출 옆)에 `notion_export.ensure_tables()` 를 더한다.

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_notion_export.py -v`
Expected: PASS (전부)

- [ ] **Step 5: 커밋**

```bash
git add app/core/notion_export.py app/main.py tests/test_notion_export.py
git commit -m "feat(notion): 목적지를 해석하고 셀라 DB 를 한 번만 만든다"
```

---

### Task 7: 저장 — 행 생성과 속성 채우기

**Files:**
- Modify: `app/core/notion_export.py`
- Test: `tests/test_notion_export.py`

**Interfaces:**
- Produces:
  - `@dataclass SaveResult(url: str, created_database: bool, skipped: list[str])`
  - `save(target: Target, title: str, text: str, kind: str = "답변", link: str = "") -> SaveResult`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
@pytest.fixture
def our_target():
    return nx.Target(database_id="db-1", data_source_id="ds-1",
                     properties={"제목": "title", "날짜": "date",
                                 "종류": "select", "셀라 링크": "url"})


@pytest.fixture
def user_made_target():
    """사용자가 직접 만든 DB — 이름이 다르고 속성이 적다."""
    return nx.Target(database_id="db-2", data_source_id="ds-2",
                     properties={"Name": "title", "Tags": "multi_select"})


def test_save_creates_a_row_under_the_data_source(monkeypatch, our_target):
    def handler(method, path, body=None):
        if path == "/v1/pages":
            assert body["parent"] == {"type": "data_source_id",
                                      "data_source_id": "ds-1"}
            return {"id": "row-1", "url": "https://notion.so/row-1"}
        raise AssertionError(path)

    _stub_requests(monkeypatch, handler)
    result = nx.save(our_target, "2026년 일본 매출", "본문입니다", kind="답변",
                     link="http://ai.example/chat")
    assert result.url == "https://notion.so/row-1"
    assert result.skipped == []


def test_save_fills_our_properties(monkeypatch, our_target):
    seen = {}

    def handler(method, path, body=None):
        seen.update(body["properties"])
        return {"id": "row-1", "url": "u"}

    _stub_requests(monkeypatch, handler)
    nx.save(our_target, "제목입니다", "본문", kind="브리핑", link="http://x")
    assert seen["제목"]["title"][0]["text"]["content"] == "제목입니다"
    assert seen["종류"]["select"]["name"] == "브리핑"
    assert seen["셀라 링크"]["url"] == "http://x"
    assert "date" in seen["날짜"]


def test_user_made_db_gets_only_its_title_property(monkeypatch, user_made_target):
    """⛔ 남의 DB 스키마를 고치지 않는다. 모르는 속성을 보내면 400 이 난다."""
    seen = {}

    def handler(method, path, body=None):
        seen.update(body["properties"])
        return {"id": "row-1", "url": "u"}

    _stub_requests(monkeypatch, handler)
    result = nx.save(user_made_target, "제목입니다", "본문", kind="답변")
    assert list(seen) == ["Name"]                       # 이름이 달라도 찾는다
    assert seen["Name"]["title"][0]["text"]["content"] == "제목입니다"
    assert set(result.skipped) == {"날짜", "종류", "셀라 링크"}


def test_save_appends_blocks_beyond_the_first_hundred(monkeypatch, our_target):
    appended = []

    def handler(method, path, body=None):
        if path == "/v1/pages":
            assert len(body["children"]) == 100
            return {"id": "row-1", "url": "u"}
        if path == "/v1/blocks/row-1/children":
            appended.append(len(body["children"]))
            return {}
        raise AssertionError(path)

    _stub_requests(monkeypatch, handler)
    nx.save(our_target, "긴 답변", "\n".join(f"- 줄 {i}" for i in range(250)))
    assert appended == [100, 50]


def test_save_cleans_the_body_before_writing(monkeypatch, our_target):
    """⛔ 내부 테이블 경로가 노션 페이지에 남으면 안 된다."""
    captured = {}

    def handler(method, path, body=None):
        captured["children"] = body.get("children", [])
        return {"id": "row-1", "url": "u"}

    _stub_requests(monkeypatch, handler)
    nx.save(our_target, "t",
            "값 1,234억\n\n<details><summary>실행된 쿼리</summary>\n"
            "`skin1004-319714.x.y`\n</details>\n")
    dumped = str(captured["children"])
    assert "1,234억" in dumped
    assert "skin1004-319714" not in dumped
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_notion_export.py -k save -v`
Expected: FAIL — `has no attribute 'save'`

- [ ] **Step 3: 구현한다**

파일 상단 import 에 `from datetime import date as _date` 를 더하고, 아래를 모듈에 넣는다:

```python
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
        raise NotionError("bad_request", "제목 속성이 없는 DB 다")

    props = {title_name: {"title": [{"type": "text",
                                     "text": {"content": (title or "제목 없음")[:200]}}]}}
    wanted = {
        "날짜": ("date", {"date": {"start": _date.today().isoformat()}}),
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
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_notion_export.py -v`
Expected: PASS (전부)

- [ ] **Step 5: 커밋**

```bash
git add app/core/notion_export.py tests/test_notion_export.py
git commit -m "feat(notion): DB 행으로 저장하고 있는 속성만 채운다"
```

---

### Task 8: 채팅 판정 — 저장인가 검색인가

**Files:**
- Create: `app/core/notion_save.py`
- Test: `tests/test_notion_save.py`

**Interfaces:**
- Produces:
  - `notion_save_intent(query: str) -> bool`
  - `extract_url(text: str) -> str`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_notion_save.py`:

```python
# -*- coding: utf-8 -*-
"""노션 저장 관문 회귀 — 저장과 검색을 **양방향**으로 지킨다."""
import pytest

from app.core import notion_save as ns


@pytest.mark.parametrize("query", [
    "이 답변 노션에 넣어줘",
    "방금 그거 노션에 저장해줘",
    "노션에 올려줘",
    "위 내용 노션 페이지에 추가해줘",
    "브리핑 매일 노션에 넣어줘",
])
def test_save_intent_true(query):
    assert ns.notion_save_intent(query) is True


@pytest.mark.parametrize("query", [
    "노션에서 휴가 규정 찾아줘",
    "노션에 뭐 있어?",
    "노션 문서 어디 있어",
    "노션 정리 잘 돼 있나?",
    "2026년 일본 매출 알려줘",
    "잔디로 보내줘",
])
def test_save_intent_false(query):
    """⛔ 검색을 가로채면 사내 문서 검색이 통째로 죽는다."""
    assert ns.notion_save_intent(query) is False


def test_extract_url_finds_the_notion_link():
    text = "https://www.notion.so/내-페이지-24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b 여기에"
    assert ns.extract_url(text).startswith("https://www.notion.so/")


def test_extract_url_returns_empty_without_a_link():
    assert ns.extract_url("그냥 아무 말") == ""
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_notion_save.py -v`
Expected: FAIL — `ModuleNotFoundError: app.core.notion_save`

- [ ] **Step 3: 구현한다**

`app/core/notion_save.py`:

```python
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
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_notion_save.py -v`
Expected: PASS (11건)

- [ ] **Step 5: 커밋**

```bash
git add app/core/notion_save.py tests/test_notion_save.py
git commit -m "feat(notion): 저장 요청과 문서 검색을 가르는 관문"
```

---

### Task 9: 되묻기와 이어받기

**Files:**
- Modify: `app/core/notion_save.py`
- Test: `tests/test_notion_save.py`

**Interfaces:**
- Consumes: `notion_save_intent`, `extract_url`
- Produces:
  - `build_prompt(kind: str = "답변") -> str` — 되묻기 문구 + 숨은 표식
  - `pending(messages: list[dict] | None) -> dict | None`
  - `target_answer(messages: list[dict] | None) -> str` — 되묻기 **바로 앞** assistant 본문

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def _msgs(*pairs):
    return [{"role": role, "content": text} for role, text in pairs]


def test_prompt_asks_for_url_and_explains_the_connection():
    prompt = ns.build_prompt()
    assert "URL" in prompt or "주소" in prompt
    assert "연결" in prompt          # ⛔ 404 의 실제 원인을 함께 알려준다
    assert ns._MARKER.search(prompt)


def test_pending_reads_the_marker_from_the_previous_assistant():
    messages = _msgs(
        ("user", "이 답변 노션에 넣어줘"),
        ("assistant", ns.build_prompt()),
        ("user", "https://www.notion.so/x"),
    )
    assert ns.pending(messages)["kind"] == "답변"


def test_pending_is_none_when_the_last_assistant_has_no_marker():
    """⛔ 오래된 요청이 나중의 일반 대화를 가로채면 안 된다."""
    messages = _msgs(
        ("user", "이 답변 노션에 넣어줘"),
        ("assistant", ns.build_prompt()),
        ("user", "아니 됐고 매출 알려줘"),
        ("assistant", "2026년 매출은 …"),
        ("user", "고마워"),
    )
    assert ns.pending(messages) is None


def test_target_answer_is_the_message_before_the_question():
    messages = _msgs(
        ("user", "2026년 일본 매출은?"),
        ("assistant", "일본 매출은 55.1억원입니다."),
        ("user", "이거 노션에 넣어줘"),
        ("assistant", ns.build_prompt()),
        ("user", "https://www.notion.so/x"),
    )
    assert ns.target_answer(messages) == "일본 매출은 55.1억원입니다."


def test_target_answer_empty_when_history_is_trimmed():
    """⛔ 못 찾으면 빈 문자열이다 — 엉뚱한 것을 저장하지 않는다."""
    messages = _msgs(
        ("user", "이거 노션에 넣어줘"),
        ("assistant", ns.build_prompt()),
        ("user", "https://www.notion.so/x"),
    )
    assert ns.target_answer(messages) == ""
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_notion_save.py -k "prompt or pending or target" -v`
Expected: FAIL — `has no attribute 'build_prompt'`

- [ ] **Step 3: 구현한다**

```python
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
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_notion_save.py -v`
Expected: PASS (전부)

- [ ] **Step 5: 커밋**

```bash
git add app/core/notion_save.py tests/test_notion_save.py
git commit -m "feat(notion): URL 을 되묻고 직전 답변을 대상으로 이어받는다"
```

---

### Task 10: 핸들러 — 판정부터 저장까지 한 함수로

**Files:**
- Modify: `app/core/notion_save.py`
- Test: `tests/test_notion_save.py`

**Interfaces:**
- Consumes: `notion_export.resolve_target`, `notion_export.save`, `notion_export.is_enabled`
- Produces: `handle(query: str, messages: list[dict] | None, user_id: int | None) -> str | None` — 관문이 답할 문자열, 해당 없으면 `None`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
from app.core import notion_export as nx


@pytest.fixture
def fake_engine(monkeypatch):
    state = {"saved": None, "target": nx.Target("db-1", "ds-1", {"제목": "title"})}

    def resolve(user_id, url):
        if "boom" in url:
            raise nx.NotionError("not_connected", "", 404)
        return state["target"]

    def save(target, title, text, kind="답변", link=""):
        state["saved"] = {"title": title, "text": text, "kind": kind}
        return nx.SaveResult(url="https://notion.so/row-1",
                             created_database=target.created, skipped=[])

    monkeypatch.setattr(nx, "is_enabled", lambda: True)
    monkeypatch.setattr(nx, "resolve_target", resolve)
    monkeypatch.setattr(nx, "save", save)
    return state


def test_handle_ignores_unrelated_questions(fake_engine):
    assert ns.handle("2026년 일본 매출 알려줘", [], 7) is None


def test_handle_asks_back_when_no_url(fake_engine):
    answer = ns.handle("이 답변 노션에 넣어줘", _msgs(
        ("user", "매출은?"), ("assistant", "55.1억원입니다."),
        ("user", "이 답변 노션에 넣어줘")), 7)
    assert ns._MARKER.search(answer)


def test_handle_saves_when_the_url_arrives(fake_engine):
    messages = _msgs(
        ("user", "매출은?"),
        ("assistant", "일본 매출은 55.1억원입니다."),
        ("user", "이 답변 노션에 넣어줘"),
        ("assistant", ns.build_prompt()),
        ("user", "https://www.notion.so/24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b"),
    )
    answer = ns.handle(messages[-1]["content"], messages, 7)
    assert "https://notion.so/row-1" in answer
    assert fake_engine["saved"]["text"] == "일본 매출은 55.1억원입니다."


def test_handle_saves_immediately_when_url_is_in_the_request(fake_engine):
    messages = _msgs(
        ("user", "매출은?"), ("assistant", "55.1억원입니다."),
        ("user", "이거 노션에 넣어줘 https://www.notion.so/"
                 "24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b"),
    )
    answer = ns.handle(messages[-1]["content"], messages, 7)
    assert "https://notion.so/row-1" in answer
    assert fake_engine["saved"]["text"] == "55.1억원입니다."


def test_handle_explains_a_404_instead_of_saying_it_just_failed(fake_engine):
    messages = _msgs(
        ("user", "매출은?"), ("assistant", "55.1억원입니다."),
        ("user", "이거 노션에 넣어줘"),
        ("assistant", ns.build_prompt()),
        ("user", "https://www.notion.so/boom1a2b3c4d54e6f8a9b0c1d2e3f4a5b"),
    )
    answer = ns.handle(messages[-1]["content"], messages, 7)
    assert "연결" in answer          # ⛔ 원인을 갈라서 말한다


def test_handle_says_nothing_to_save_when_history_is_gone(fake_engine):
    messages = _msgs(
        ("user", "이거 노션에 넣어줘"),
        ("assistant", ns.build_prompt()),
        ("user", "https://www.notion.so/24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b"),
    )
    answer = ns.handle(messages[-1]["content"], messages, 7)
    assert "저장할" in answer
    assert fake_engine["saved"] is None


def test_handle_tells_the_user_when_the_feature_is_off(monkeypatch):
    monkeypatch.setattr(nx, "is_enabled", lambda: False)
    answer = ns.handle("이 답변 노션에 넣어줘", _msgs(
        ("user", "매출은?"), ("assistant", "55.1억"),
        ("user", "이 답변 노션에 넣어줘")), 7)
    assert "관리자" in answer
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_notion_save.py -k handle -v`
Expected: FAIL — `has no attribute 'handle'`

- [ ] **Step 3: 구현한다**

```python
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
    waiting = pending(messages)
    if waiting:
        url = extract_url(query)
        if not url:
            # ⚠️ 마음이 바뀐 것일 수 있다 — 저장 요청이 아니면 놓아준다.
            if not notion_save_intent(query):
                return None
            return build_prompt(waiting.get("kind", "답변"))
        return _do_save(user_id, url, target_answer(messages),
                        waiting.get("kind", "답변"))

    if not notion_save_intent(query):
        return None

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
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_notion_save.py -v`
Expected: PASS (전부)

- [ ] **Step 5: 커밋**

```bash
git add app/core/notion_save.py tests/test_notion_save.py
git commit -m "feat(notion): 판정부터 저장까지 하나의 핸들러로"
```

---

### Task 11: 오케스트레이터 배선 — 두 경로 모두

**Files:**
- Modify: `app/agents/orchestrator.py:1126`(`route_and_execute`), `app/agents/orchestrator.py:1411`(`route_and_stream`)
- Test: `tests/test_notion_save.py`

**Interfaces:**
- Consumes: `notion_save.handle`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_gate_is_wired_into_both_orchestrator_paths():
    """⛔ 한쪽만 달면 경로에 따라 답이 갈린다.

    채팅은 **스트리밍**으로 나간다 — `answer_check` 를 만들어 놓고 비스트리밍에만
    배선해 실트래픽에서 한 번도 돌지 않았던 사고가 있었다.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "agents" / "orchestrator.py").read_text(encoding="utf-8")
    body = source.split("async def route_and_execute", 1)[1]
    non_stream, stream = body.split("async def route_and_stream", 1)
    assert "notion_save" in non_stream, "비스트리밍 경로에 관문이 없다"
    assert "notion_save" in stream, "스트리밍 경로에 관문이 없다"


def test_gate_runs_before_the_source_fast_path():
    """⛔ `@@물류` 를 켠 채로도 저장이 돼야 한다 — db_entry 판정보다 앞에 둔다."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "agents" / "orchestrator.py").read_text(encoding="utf-8")
    for chunk in source.split("async def route_and_")[1:]:
        gate = chunk.find("notion_save")
        parse = chunk.find("self.parse_db_prefix")
        assert gate > 0 and parse > 0
        assert gate < parse, "관문이 @@ 소스 판정보다 뒤에 있다"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_notion_save.py -k gate -v`
Expected: FAIL — `AssertionError: 비스트리밍 경로에 관문이 없다`

- [ ] **Step 3: 비스트리밍 경로에 배선한다**

`app/agents/orchestrator.py` 의 1126행 `conversation_context = _build_conversation_context(messages)` **바로 다음 줄**에 넣는다:

```python

        # ═══ 노션 저장 관문 ═══
        # ⛔ 라우터보다, 그리고 @@ 소스 판정보다 **먼저** 돈다 — `@@물류` 를 켠 채로
        #    "이 답변 노션에 넣어줘" 라고 해도 저장돼야 한다.
        # ⚠️ 스트리밍 경로에도 **같은 관문**이 있다. 한쪽만 고치면 답이 갈린다.
        from app.core import notion_save
        _notion_answer = await asyncio.to_thread(
            notion_save.handle, query, messages, user_id)
        if _notion_answer:
            logger.info("notion_save_handled", path="route_and_execute")
            return {"source": "direct", "answer": _notion_answer}
```

- [ ] **Step 4: 스트리밍 경로에 배선한다**

1411행 `conversation_context = _build_conversation_context(messages)` **바로 다음 줄**에 넣는다:

```python

        # ⚠️ 비스트리밍과 **같은 관문** — 한쪽만 달면 경로에 따라 답이 갈린다.
        from app.core import notion_save
        _notion_answer = await asyncio.to_thread(
            notion_save.handle, query, messages, user_id)
        if _notion_answer:
            logger.info("notion_save_handled", path="route_and_stream")
            yield ("source", "direct")
            yield ("done", _notion_answer)
            return
```

파일 상단에 `import asyncio` 가 있는지 확인하고, 없으면 더한다.

- [ ] **Step 5: 통과를 확인한다**

Run: `python -m pytest tests/test_notion_save.py tests/test_notion_export.py -v`
Expected: PASS (전부)

- [ ] **Step 6: 전체 테스트로 회귀를 확인한다**

Run: `python -m pytest tests/ -x -q`
Expected: PASS — ⚠️ 실패가 나오면 **내 변경 탓인지 먼저 확인한다**. 이 작업트리는 다른 세션과 공유한다(남의 미완성 파일이 섞여 들어온다).

- [ ] **Step 7: 커밋**

```bash
git add app/agents/orchestrator.py tests/test_notion_save.py
git commit -m "feat(notion): 저장 관문을 일반·스트리밍 두 경로에 건다"
```

---

### Task 12: 실물 관통 확인과 문서

**Files:**
- Modify: `CLAUDE.md`
- Run: `scripts/notion_write_probe.py`

- [ ] **Step 1: 노션에서 인테그레이션을 만든다**

1. notion.so/my-integrations → `셀라` 내부 인테그레이션 생성
2. capability: **읽기 + 콘텐츠 삽입**, 사용자 정보 **없음**
3. 토큰을 로컬 `.env` 에 `NOTION_WRITE_TOKEN=...` 으로 넣는다
4. 노션에서 테스트용 페이지를 하나 만들고 `⋯ > 연결 > 셀라` 를 추가한다

- [ ] **Step 2: 관통시킨다**

Run: `python scripts/notion_write_probe.py <그 페이지 URL>`
Expected: `✅ 관통 성공` 과 함께 `database_id` · `data_sources` · 행 URL 이 찍힌다

⛔ 여기서 막히면 **멈추고 보고한다.** 응답 형태가 다르면 Task 6·7의 코드가 틀린 것이고,
나머지를 짓기 전에 알아야 한다.

- [ ] **Step 3: 실제 답변 하나로 표를 확인한다**

노션에서 만들어진 행을 열어 **표가 표로 보이는지** 눈으로 본다.
⚠️ 노션 표 블록은 규격이 까다로워 400이 나기 쉽다. 깨졌으면 Task 5로 돌아간다.

- [ ] **Step 4: CLAUDE.md 에 규칙을 적는다**

`## OP 재고` 절 **앞**에 새 절을 넣는다:

```markdown
## 노션 저장 — 쓰기는 다른 토큰·다른 API 버전이다 (2026-09-07)

- **파일**: `app/core/notion_export.py`(엔진) · `app/core/notion_save.py`(관문) ·
  진단 `scripts/notion_write_probe.py`
- ⛔ **쓰기 헤더는 `2025-09-03` 이다.** 옛 버전(`2022-06-28`)의 `parent: {database_id}` 는
  **단일 소스 DB 에서만** 행 생성이 된다 — 사용자가 그 DB 에 데이터 소스를 하나 더
  붙이는 순간 저장이 실패한다. 읽기 경로(학습·제품정보)는 옛 버전 그대로 둔다
- ⛔ **토큰을 학습용과 섞지 마라.** `NOTION_WRITE_TOKEN` 은 쓰기 전용 인테그레이션이다.
  크롤러 토큰이 개인 페이지 쓰기 권한을 갖는 구조를 만들지 않는다.
  ⚠️ `.env` 는 배포에서 제외된다 — WAS 에서 직접 넣고 재기동해야 한다
- ⛔ **사용자가 만든 DB 의 스키마를 고치지 마라.** `type == "title"` 인 속성만 이름과
  무관하게 찾아 채우고, 나머지는 이름·타입이 맞을 때만 채운다. 모르는 속성을 보내면
  노션은 400 을 준다. 못 채운 것은 답변에 적는다
- ⛔ **DB 를 매번 만들지 마라** — 순서는 ① 캐시(`user_notion_databases`)
  ② 부모의 자식 DB 중 제목이 `셀라` ③ 없으면 생성. ②가 있어야 사용자가 DB 를
  옮겨도 회복한다
- ⛔ **`<details>실행된 쿼리</details>` 를 반드시 걷는다** — 노션에서는 평문으로
  펼쳐져 **내부 테이블 경로가 페이지에 남는다.** 노션 페이지는 공유가 쉽다
- ⛔ **저장 요청과 문서 검색을 가른다.** `노션` 은 사내 문서 검색의 확신
  키워드이기도 하다 — 저장 **동사**가 함께 있고 검색 동사가 없을 때만 관문이 잡는다.
  회귀가 양방향으로 지킨다
- ⚠️ 관문은 **일반·스트리밍 두 경로 모두**에, 그리고 `@@` 소스 판정보다 **앞에** 있다
  (`@@물류` 를 켠 채로도 저장돼야 한다). 정적 검사가 위치를 지킨다
- ⚠️ URL 만으로는 DB 와 페이지를 구분할 수 없다 — `GET /v1/databases/{id}` 로 물어본다.
  `?v=` 뒤에도 32자 hex 가 있으니 쿼리를 먼저 버릴 것
- ⛔ `*.notion.site`(외부 공개 사이트)는 인테그레이션으로 쓸 수 없다 — 영원히 404 다
```

Run: `python scripts/sync_agents_md.py`

- [ ] **Step 5: 커밋**

```bash
git add CLAUDE.md AGENTS.md
git commit -m "docs: 노션 저장 규칙 — 쓰기 토큰·API 버전·관문 위치"
```

---

## 완료 조건

- `python -m pytest tests/test_notion_export.py tests/test_notion_save.py -v` 전부 통과
- `python -m pytest tests/ -q` 에 새 실패 없음
- `scripts/notion_write_probe.py` 가 실제 페이지에서 관통 성공
- 실제 매출 답변 하나가 노션에 **표까지 제대로** 저장됨
- CLAUDE.md / AGENTS.md 갱신

## 2단계에서 할 것 (이 계획 밖)

브리핑 자동 배송 — `user_notion_targets` · `briefing_notion_outbox` ·
`personal_briefing._enqueue_notion` · 잡 `notion_push_halfhourly` ·
`app/api/notion_briefing_api.py` · 첫 화면 `노션으로 받기` · 자가 점검 `notion_push`.
