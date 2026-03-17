"""Notion Sub Agent (v6.2).

Accesses Notion workspace via direct API calls (no MCP dependency).
Searches ONLY allowlisted pages/databases, reads block content,
generates answer using the user's selected LLM.

v3.0: MCP + Claude Sonnet
v4.0: Direct Notion API + user-selected LLM (Gemini/Claude)
v5.0: Enhanced indexing (depth 6), content limits, improved search & prompt
v6.0: Allowlist-based search — only 10 pre-defined pages/databases
v6.1: Fix httpx client re-creation, sheet read timeout (30s), search punctuation cleanup
v6.2: Parallel page reads (asyncio.gather), parallel sheet reads, parallel warmup
"""

import asyncio
import re
import httpx
import structlog

from app.config import get_settings
from app.core.google_sheets import parse_spreadsheet_id, read_google_sheet
from app.core.llm import MODEL_GEMINI, get_flash_client, get_llm_client

logger = structlog.get_logger(__name__)

# Notion API constants
_NOTION_BASE = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"
_MAX_BLOCKS = 200  # Max blocks to read per page
_MAX_CONTENT_CHARS = 15000  # Max chars to read from a single page (prevent timeout)
_SHEET_READ_TIMEOUT = 30.0  # Max seconds to wait for a single Google Sheet read
_SHEET_MAX_ROWS = 50  # Max rows to read from Notion-linked Google Sheets

# Retry constants
_MAX_RETRIES = 3
_RETRY_BACKOFF = [1.0, 2.0, 4.0]  # seconds between retries
_RETRYABLE_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
    httpx.WriteError,
    ConnectionError,
)

# Shared client configuration
_CLIENT_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
_CLIENT_LIMITS = httpx.Limits(max_connections=5, max_keepalive_connections=3)


def _format_uuid(raw_id: str) -> str:
    """Format a 32-char hex string as a Notion-style UUID (8-4-4-4-12)."""
    raw = raw_id.replace("-", "")
    if len(raw) == 32:
        return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"
    return raw_id


# ── Allowlist: only these pages/databases are searchable ──
_ALLOWED_PAGES = [
    {"id": "2532b4283b0080eba96ce35ae8ba8743", "description": "법인 태블릿", "type": "database"},
    {"id": "1602b4283b0080f186cfc6425d9a53dd", "description": "데이터 분석 파트", "type": "database"},
    {"id": "2e62b4283b00803a8007df0d3003705c", "description": "EAST 2팀 가이드 아카이브", "type": "database"},
    {"id": "2e12b4283b0080b48a1dd7bbbd6e0e53", "description": "EAST 2026 업무파악", "type": "database"},
    {"id": "19d2b4283b0080dc89d9e6d9c11ec1e5", "description": "EAST 틱톡샵 접속 방법", "type": "page"},
    {"id": "1982b4283b008039ad79ec0c1c1e38fb", "description": "EAST 해외 출장 가이드북", "type": "page"},
    {"id": "22e2b4283b008060bac6cef042c3787b", "description": "WEST 틱톡샵US 대시보드", "type": "database"},
    {"id": "c058d9e89e8a4780b32e866b8248b5b1", "description": "KBT 스스 운영방법", "type": "page"},
    {"id": "1fb2b4283b00802883faef2df97c6f73", "description": "네이버 스스 업무 공유", "type": "page"},
    {"id": "1dc2b4283b0080cb8790cf5218896ebd", "description": "DB daily 광고 입력 업무", "type": "page"},
]

# Module-level title cache: populated by _warm_up()
# Maps page_id -> {"id", "title", "description", "type"}
_page_titles: dict[str, dict] = {}
_titles_loaded = False


class NotionAgent:
    def __init__(self):
        settings = get_settings()
        self.token = settings.notion_mcp_token
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": _NOTION_VERSION,
            "Content-Type": "application/json",
        }
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Return the shared httpx client, creating if needed."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=_CLIENT_TIMEOUT,
                limits=_CLIENT_LIMITS,
                headers=self.headers,
            )
        return self._client

    async def _close_client(self):
        """Close the shared client after a run() call."""
        if self._client is not None:
            if not self._client.is_closed:
                await self._client.aclose()
            self._client = None

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        **kwargs,
    ) -> httpx.Response:
        """Execute an HTTP request with retry on transient errors.

        Args:
            method: "GET" or "POST"
            url: Full URL
            **kwargs: Passed to httpx client.request()

        Returns:
            httpx.Response

        Raises:
            Last exception after all retries exhausted.
        """
        last_exc: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            client = await self._get_client()  # Re-acquire client each attempt
            try:
                resp = await client.request(method, url, **kwargs)
                return resp
            except _RETRYABLE_ERRORS as e:
                last_exc = e
                wait = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
                logger.warning(
                    "notion_request_retry",
                    attempt=attempt + 1,
                    max_retries=_MAX_RETRIES,
                    url=url.split("/")[-1][:20],
                    error=type(e).__name__,
                    wait=wait,
                )
                # Re-create client if connection pool is broken
                if isinstance(e, (httpx.ConnectError, httpx.RemoteProtocolError)):
                    await self._close_client()
                await asyncio.sleep(wait)

        raise last_exc  # type: ignore[misc]

    async def run(self, query: str, model_type: str = MODEL_GEMINI) -> str:
        """Search Notion and generate an answer.

        Args:
            query: User question (may include conversation context prefix).
            model_type: Which LLM to use for answer generation.

        Returns:
            Answer text.
        """
        if not self.token:
            return "Notion API 토큰이 설정되지 않았습니다. .env 파일에서 NOTION_MCP_TOKEN을 확인해주세요."

        try:
            # Ensure titles are loaded
            if not _titles_loaded:
                await self._warm_up()

            # Step 1: Search for relevant pages in allowlist
            pages = await self._search_pages(query)
            if not pages:
                # Extract clean search term for helpful message
                clean_term = self._extract_search_term(query)
                return (
                    f"**'{clean_term}'** 관련 내용을 Notion 문서에서 찾을 수 없습니다.\n\n"
                    "현재 접근 가능한 Notion 문서 목록:\n"
                    + "\n".join(f"- {e['description']}" for e in _ALLOWED_PAGES)
                    + "\n\n위 문서에서 검색할 수 있는 키워드로 다시 질문해주세요."
                )

            # Step 2: Read content from top pages (max 3) — IN PARALLEL
            async def _read_one_page(page: dict) -> str | None:
                """Read a single page/database and return its content."""
                page_id = page["id"]
                title = page.get("title", page.get("description", "제목 없음"))
                page_type = page.get("type", "page")

                if page_type == "database":
                    return await self._read_database_entries(page_id, title)

                blocks_text = await self._read_page_blocks(page_id)
                if blocks_text:
                    return f"## {title}\n{blocks_text}"
                # No block content — try properties + Google Sheets fallback
                return await self._read_page_fallback(page_id, title)

            results = await asyncio.gather(
                *[_read_one_page(p) for p in pages[:3]],
                return_exceptions=True,
            )
            all_content = []
            for r in results:
                if isinstance(r, Exception):
                    logger.warning("notion_parallel_read_error", error=repr(r))
                elif r:
                    all_content.append(r)

            if not all_content:
                return (
                    "노션 페이지를 찾았으나 내용을 읽을 수 없습니다. "
                    "Integration 권한을 확인해주세요."
                )

            content = "\n\n---\n\n".join(all_content)
            logger.info(
                "notion_content_loaded",
                pages=len(pages),
                content_length=len(content),
            )

            # Step 3: Generate answer using LLM
            return await self._generate_answer(query, content, model_type)

        except Exception as e:
            logger.error("notion_agent_failed", error=repr(e))
            return f"노션 검색 중 오류가 발생했습니다: {repr(e)}"

        finally:
            await self._close_client()

    # ── Warmup: fetch titles for allowlisted pages ──

    async def _warm_up(self):
        """Fetch real Notion titles for each allowlisted page/database.

        Makes one API call per entry (10 calls total). Results are cached
        in module-level _page_titles so subsequent searches are instant.
        If the declared type fails, tries the opposite endpoint as fallback.
        Uses a dedicated client with retry for warmup.
        """
        global _page_titles, _titles_loaded

        logger.info("notion_warmup_start", count=len(_ALLOWED_PAGES))

        async with httpx.AsyncClient(
            timeout=_CLIENT_TIMEOUT, limits=_CLIENT_LIMITS, headers=self.headers
        ) as client:

            async def _fetch_one_title(entry: dict) -> None:
                """Fetch title for a single allowlisted page/database."""
                page_id = entry["id"]
                formatted_id = _format_uuid(page_id)
                page_type = entry.get("type", "page")
                description = entry["description"]

                try:
                    data, actual_type = await self._fetch_title_with_fallback(
                        client, formatted_id, page_type
                    )

                    if data is None:
                        logger.warning(
                            "notion_warmup_fetch_failed",
                            page_id=page_id[:12],
                            description=description,
                        )
                        _page_titles[page_id] = {
                            "id": formatted_id,
                            "title": description,
                            "description": description,
                            "type": page_type,
                        }
                        return

                    title = self._extract_title_from_api(data, actual_type)

                    _page_titles[page_id] = {
                        "id": formatted_id,
                        "title": title or description,
                        "description": description,
                        "type": actual_type,
                    }

                    logger.info(
                        "notion_warmup_loaded",
                        page_id=page_id[:12],
                        title=title or description,
                        type=actual_type,
                    )

                except Exception as e:
                    logger.warning(
                        "notion_warmup_error",
                        page_id=page_id[:12],
                        error=repr(e),
                    )
                    _page_titles[page_id] = {
                        "id": formatted_id,
                        "title": description,
                        "description": description,
                        "type": page_type,
                    }

            # Fetch all titles in parallel
            await asyncio.gather(
                *[_fetch_one_title(entry) for entry in _ALLOWED_PAGES],
                return_exceptions=True,
            )

        _titles_loaded = True
        logger.info(
            "notion_warmup_done",
            loaded=len(_page_titles),
            titles=[v["title"] for v in _page_titles.values()],
        )

    async def _fetch_title_with_fallback(
        self, client: httpx.AsyncClient, formatted_id: str, page_type: str
    ) -> tuple[dict | None, str]:
        """Try to fetch page/database info, falling back to the other type.

        Returns (data_dict, actual_type) or (None, page_type) on failure.
        """
        # Try declared type first
        endpoints = (
            [("database", f"{_NOTION_BASE}/databases/{formatted_id}"),
             ("page", f"{_NOTION_BASE}/pages/{formatted_id}")]
            if page_type == "database"
            else [("page", f"{_NOTION_BASE}/pages/{formatted_id}"),
                  ("database", f"{_NOTION_BASE}/databases/{formatted_id}")]
        )

        for etype, url in endpoints:
            resp = await client.get(url, headers=self.headers)
            if resp.status_code == 200:
                return resp.json(), etype

        return None, page_type

    @staticmethod
    def _extract_title_from_api(data: dict, page_type: str) -> str:
        """Extract title from a Notion API page or database response."""
        if page_type == "database":
            title_arr = data.get("title", [])
            return "".join(t.get("plain_text", "") for t in title_arr)
        else:
            # Page: title is inside properties
            props = data.get("properties", {})
            for val in props.values():
                if isinstance(val, dict) and "title" in val:
                    title_arr = val["title"]
                    return "".join(t.get("plain_text", "") for t in title_arr)
        return ""

    # ── Search: keyword matching against allowlist titles/descriptions ──

    # Action words to strip from search queries (longer first for greedy match)
    _STRIP_SUFFIXES = [
        "정보 가져와줘", "정보 알려줘", "정보 보여줘",
        "내용 가져와줘", "내용 가져와", "내용 알려줘", "내용 보여줘",
        "가져와줘", "가져와", "알려줘", "보여줘", "읽어줘", "찾아줘",
        "검색해줘", "확인해줘", "가져다줘",
        "에 대해서", "에 대해", "에 대한",
        "관련 정보", "관련 내용", "관련",
        "정보", "내용",
    ]
    _STRIP_PREFIXES = [
        "노션에서 ", "노션 ", "notion에서 ", "notion ",
    ]

    def _extract_search_term(self, query: str) -> str:
        """Extract clean search term from user query."""
        search_term = query

        # Strip conversation context prefix
        if "[현재 질문]" in search_term:
            search_term = search_term.split("[현재 질문]")[-1].strip()

        # Strip common prefixes
        lower = search_term.lower()
        for prefix in self._STRIP_PREFIXES:
            if lower.startswith(prefix):
                search_term = search_term[len(prefix):]
                lower = search_term.lower()

        # Strip action suffixes (apply repeatedly for chained suffixes)
        changed = True
        while changed:
            changed = False
            for suffix in self._STRIP_SUFFIXES:
                if search_term.endswith(suffix):
                    search_term = search_term[: -len(suffix)].strip()
                    changed = True
                    break

        return search_term.strip()

    async def _search_pages(self, query: str) -> list:
        """Search allowlisted pages by keyword matching against titles/descriptions.

        Returns list of dicts with 'id', 'title', 'description', 'type',
        sorted by match quality: exact > partial > word match.
        """
        search_term = self._extract_search_term(query)
        term_lower = search_term.lower()

        exact = []
        partial = []
        word_match = []

        # Strip punctuation and Korean particles from each word for better matching
        _KR_PARTICLES = (
            "부터", "까지", "에서", "으로", "에게", "이랑", "하고",
            "은", "는", "이", "가", "을", "를", "의", "와", "과",
            "도", "만", "에", "로", "라", "랑",
        )
        search_words = []
        for w in term_lower.split():
            w = re.sub(r'[^\w가-힣a-zA-Z0-9]', '', w)
            # Strip trailing Korean particles (longest first)
            for p in sorted(_KR_PARTICLES, key=len, reverse=True):
                if w.endswith(p) and len(w) > len(p) + 1:
                    w = w[:-len(p)]
                    break
            if len(w) >= 2:
                search_words.append(w)

        for entry in _page_titles.values():
            title_lower = entry["title"].lower()
            desc_lower = entry["description"].lower()
            # Match against both title and description
            match_text = f"{title_lower} {desc_lower}"

            if term_lower == title_lower or term_lower == desc_lower:
                exact.append(entry)
            elif term_lower in match_text or title_lower in term_lower or desc_lower in term_lower:
                partial.append(entry)
            elif search_words and any(w in match_text for w in search_words):
                # Score by number of matching words
                score = sum(1 for w in search_words if w in match_text)
                word_match.append((score, entry))

        # Sort word matches by score descending
        word_match.sort(key=lambda x: x[0], reverse=True)

        results = exact + partial + [wm[1] for wm in word_match]

        # Fallback: if no keyword match, use LLM to pick relevant pages
        if not results:
            results = await self._llm_select_pages(search_term)
            logger.info(
                "notion_search_fallback_llm",
                query=search_term[:50],
                count=len(results),
                matched=[r["title"] for r in results[:5]],
            )
        else:
            logger.info(
                "notion_search_results",
                query=search_term[:50],
                count=len(results),
                matched=[r["title"] for r in results[:5]],
            )
        return results

    async def _llm_select_pages(self, query: str) -> list:
        """Use Gemini Flash to select the most relevant pages for a query.

        Called as fallback when keyword matching finds no results.
        Only considers accessible pages. Returns up to 3 pages.
        """
        # Filter out pages that returned 404 during warmup (not accessible)
        accessible = []
        for e in _page_titles.values():
            # Skip pages that we know are inaccessible
            raw_id = e["id"].replace("-", "")
            orig = next((p for p in _ALLOWED_PAGES if p["id"] == raw_id), None)
            if orig and e["title"] == orig["description"] and e["type"] == orig.get("type", "page"):
                # Title was never fetched (fallback to description) — likely inaccessible
                continue
            accessible.append(e)

        if not accessible:
            return list(_page_titles.values())

        page_list = "\n".join(
            f"{i+1}. [{e['type']}] {e['title']} — {e['description']}"
            for i, e in enumerate(accessible)
        )
        prompt = f"""다음은 SKIN1004 회사의 Notion 페이지 목록입니다:
{page_list}

사용자 질문: "{query}"

이 질문과 가장 관련 있을 것 같은 페이지 번호를 최대 3개 골라주세요.
제품 정보, SKU, 번들 등은 '대시보드'나 '제품 마스터' 관련 페이지에 있을 수 있습니다.

⚠️ 중요: 목록에 질문과 관련된 페이지가 전혀 없다면, 반드시 숫자 0만 답하세요.
관련 없는 페이지를 억지로 선택하지 마세요!

반드시 숫자만 쉼표로 구분하여 답하세요. 예: 1,3,7 또는 관련 없으면: 0"""

        try:
            flash = get_flash_client()
            response = flash.generate(prompt, temperature=0.0)
            nums = [int(n.strip()) for n in response.strip().split(",") if n.strip().isdigit()]
            # If LLM says "0" — no relevant pages found
            if nums == [0]:
                logger.info("notion_llm_select_no_match", query=query[:50])
                return []
            selected = [accessible[n - 1] for n in nums if 1 <= n <= len(accessible)]
            return selected or accessible[:3]
        except Exception as e:
            logger.warning("notion_llm_select_failed", error=repr(e))
            return accessible[:3]

    # ── Page/block reading ──

    async def _read_page_blocks(self, page_id: str) -> str:
        """Read all blocks from a Notion page and convert to text.

        Handles nested blocks (toggles, child pages) up to 3 levels deep.
        Stops reading once _MAX_CONTENT_CHARS is reached to prevent timeout.
        Also detects and reads linked Google Sheets found in block hrefs.
        """
        self._content_chars = 0
        self._found_sheet_urls: list[str] = []
        lines = await self._read_blocks_recursive(page_id, depth=0, max_depth=3)

        # Read linked Google Sheets found in blocks — IN PARALLEL (up to 2 sheets)
        if self._found_sheet_urls:
            seen: set[str] = set()
            sheet_ids: list[str] = []
            for url in self._found_sheet_urls:
                sid = parse_spreadsheet_id(url)
                if not sid or sid in seen:
                    continue
                seen.add(sid)
                sheet_ids.append(sid)
                if len(sheet_ids) >= 2:
                    break

            async def _read_one_sheet(sid: str) -> str | None:
                try:
                    logger.info("notion_reading_linked_sheet_from_block", spreadsheet_id=sid)
                    sheet_data = await asyncio.wait_for(
                        asyncio.to_thread(read_google_sheet, sid, max_rows=_SHEET_MAX_ROWS),
                        timeout=_SHEET_READ_TIMEOUT,
                    )
                    if sheet_data:
                        return f"\n### 연결된 시트 데이터\n{sheet_data}"
                except asyncio.TimeoutError:
                    logger.warning("notion_sheet_read_timeout", spreadsheet_id=sid, timeout=_SHEET_READ_TIMEOUT)
                    return f"\n### 연결된 시트 데이터\n(시트 데이터가 너무 커서 읽기 시간 초과)"
                except Exception as e:
                    logger.warning("notion_sheet_read_failed", spreadsheet_id=sid, error=repr(e))
                return None

            sheet_results = await asyncio.gather(
                *[_read_one_sheet(sid) for sid in sheet_ids],
                return_exceptions=True,
            )
            for sr in sheet_results:
                if isinstance(sr, Exception):
                    logger.warning("notion_sheet_parallel_error", error=repr(sr))
                elif sr:
                    lines.append(sr)
                    self._content_chars += len(sr)

        return "\n".join(lines)

    async def _read_blocks_recursive(
        self, block_id: str, depth: int, max_depth: int
    ) -> list[str]:
        """Recursively read blocks with character budget."""
        if depth > max_depth or self._content_chars >= _MAX_CONTENT_CHARS:
            return []

        blocks = await self._fetch_blocks(block_id)
        if not blocks:
            return []

        lines = []
        for block in blocks:
            if self._content_chars >= _MAX_CONTENT_CHARS:
                break

            # Collect Google Sheet URLs from rich_text hrefs
            self._collect_sheet_urls(block)

            text = self._block_to_text(block, indent=depth)
            if text:
                lines.append(text)
                self._content_chars += len(text)

            # Read children if the block has them (toggles, columns, etc.)
            if block.get("has_children") and self._content_chars < _MAX_CONTENT_CHARS:
                child_lines = await self._read_blocks_recursive(
                    block["id"], depth + 1, max_depth
                )
                lines.extend(child_lines)

        return lines

    def _collect_sheet_urls(self, block: dict):
        """Extract Google Sheets URLs from rich_text hrefs in a block."""
        btype = block.get("type", "")
        rich_text_keys = [
            "paragraph", "bulleted_list_item", "numbered_list_item",
            "toggle", "callout", "quote", "heading_1", "heading_2", "heading_3",
        ]
        for key in rich_text_keys:
            if btype == key:
                for rt in block.get(key, {}).get("rich_text", []):
                    href = rt.get("href", "") or ""
                    if "docs.google.com/spreadsheets" in href:
                        self._found_sheet_urls.append(href)

        # Also check bookmark and embed blocks
        if btype == "bookmark":
            url = block.get("bookmark", {}).get("url", "")
            if url and "docs.google.com/spreadsheets" in url:
                self._found_sheet_urls.append(url)
        elif btype == "embed":
            url = block.get("embed", {}).get("url", "")
            if url and "docs.google.com/spreadsheets" in url:
                self._found_sheet_urls.append(url)

    async def _fetch_blocks(self, block_id: str) -> list:
        """Fetch child blocks of a given block/page.

        Uses shared client with retry for transient errors.
        """
        all_blocks = []
        cursor = None

        while len(all_blocks) < _MAX_BLOCKS:
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor

            try:
                resp = await self._request_with_retry(
                    "GET",
                    f"{_NOTION_BASE}/blocks/{block_id}/children",
                    params=params,
                )
            except _RETRYABLE_ERRORS as e:
                logger.warning(
                    "notion_fetch_blocks_failed_after_retries",
                    block_id=block_id,
                    error=type(e).__name__,
                )
                break

            if resp.status_code != 200:
                logger.warning(
                    "notion_fetch_blocks_failed",
                    block_id=block_id,
                    status=resp.status_code,
                )
                break

            data = resp.json()
            all_blocks.extend(data.get("results", []))

            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")

        return all_blocks

    def _block_to_text(self, block: dict, indent: int = 0) -> str:
        """Convert a Notion block to plain text."""
        btype = block.get("type", "")
        prefix = "  " * indent

        # Rich text extraction helper
        def _rich_text(key: str) -> str:
            rich = block.get(key, {}).get("rich_text", [])
            return "".join(rt.get("plain_text", "") for rt in rich)

        if btype == "paragraph":
            text = _rich_text("paragraph")
            return f"{prefix}{text}" if text else ""

        elif btype == "heading_1":
            return f"{prefix}# {_rich_text('heading_1')}"

        elif btype == "heading_2":
            return f"{prefix}## {_rich_text('heading_2')}"

        elif btype == "heading_3":
            return f"{prefix}### {_rich_text('heading_3')}"

        elif btype == "bulleted_list_item":
            return f"{prefix}- {_rich_text('bulleted_list_item')}"

        elif btype == "numbered_list_item":
            return f"{prefix}1. {_rich_text('numbered_list_item')}"

        elif btype == "toggle":
            return f"{prefix}> {_rich_text('toggle')}"

        elif btype == "to_do":
            checked = block.get("to_do", {}).get("checked", False)
            mark = "x" if checked else " "
            return f"{prefix}- [{mark}] {_rich_text('to_do')}"

        elif btype == "code":
            lang = block.get("code", {}).get("language", "")
            code_text = _rich_text("code")
            return f"{prefix}```{lang}\n{prefix}{code_text}\n{prefix}```"

        elif btype == "quote":
            return f"{prefix}> {_rich_text('quote')}"

        elif btype == "callout":
            icon = block.get("callout", {}).get("icon", {})
            emoji = icon.get("emoji", "") if icon else ""
            return f"{prefix}{emoji} {_rich_text('callout')}"

        elif btype == "divider":
            return f"{prefix}---"

        elif btype == "child_page":
            title = block.get("child_page", {}).get("title", "")
            return f"{prefix}[하위 페이지: {title}]"

        elif btype == "child_database":
            title = block.get("child_database", {}).get("title", "")
            return f"{prefix}[하위 데이터베이스: {title}]"

        elif btype == "table_row":
            cells = block.get("table_row", {}).get("cells", [])
            cell_texts = []
            for cell in cells:
                cell_texts.append(
                    "".join(rt.get("plain_text", "") for rt in cell)
                )
            return f"{prefix}| {' | '.join(cell_texts)} |"

        elif btype == "bookmark":
            url = block.get("bookmark", {}).get("url", "")
            return f"{prefix}[북마크: {url}]" if url else ""

        elif btype == "embed":
            url = block.get("embed", {}).get("url", "")
            return f"{prefix}[임베드: {url}]" if url else ""

        elif btype == "image":
            img = block.get("image", {})
            url = img.get("file", img.get("external", {})).get("url", "")
            caption = "".join(
                rt.get("plain_text", "")
                for rt in img.get("caption", [])
            )
            return f"{prefix}[이미지: {caption or url}]"

        return ""

    # ── Database entry reading (unchanged from v5.0) ──

    async def _read_database_entries(
        self, database_id: str, title: str, max_entries: int = 20,
    ) -> str | None:
        """Query a Notion database and format entries as content."""
        try:
            resp = await self._request_with_retry(
                "POST",
                f"{_NOTION_BASE}/databases/{database_id}/query",
                json={"page_size": max_entries},
            )
            if resp.status_code != 200:
                logger.warning(
                    "notion_database_query_failed",
                    database_id=database_id,
                    status=resp.status_code,
                )
                return None
            data = resp.json()

            entries = data.get("results", [])
            if not entries:
                return None

            logger.info(
                "notion_database_entries_loaded",
                database_id=database_id,
                title=title,
                count=len(entries),
            )

            parts = [f"## 데이터베이스: {title}\n"]

            # Check first entry for Google Sheets link
            first_props = entries[0].get("properties", {})
            sheet_url = self._extract_google_sheet_url(first_props)
            sheet_data = None
            if sheet_url:
                spreadsheet_id = parse_spreadsheet_id(sheet_url)
                if spreadsheet_id:
                    logger.info(
                        "notion_db_reading_linked_sheet",
                        spreadsheet_id=spreadsheet_id,
                    )
                    try:
                        sheet_data = await asyncio.wait_for(
                            asyncio.to_thread(read_google_sheet, spreadsheet_id, max_rows=_SHEET_MAX_ROWS),
                            timeout=_SHEET_READ_TIMEOUT,
                        )
                    except asyncio.TimeoutError:
                        logger.warning("notion_db_sheet_read_timeout", spreadsheet_id=spreadsheet_id)
                        sheet_data = None

            if sheet_data:
                parts.append(f"### 시트 데이터\n{sheet_data}")
            else:
                # Format each entry's properties
                for i, entry in enumerate(entries):
                    props = entry.get("properties", {})
                    prop_lines = self._format_properties(props)
                    if prop_lines:
                        # Get entry title
                        entry_title = self._get_entry_title(props)
                        parts.append(f"### {i+1}. {entry_title}")
                        parts.append(prop_lines)
                        parts.append("")

            content = "\n".join(parts)
            # Respect content budget
            if len(content) > _MAX_CONTENT_CHARS:
                content = content[:_MAX_CONTENT_CHARS] + "\n\n... (항목이 많아 일부 생략)"

            return content

        except Exception as e:
            logger.warning(
                "notion_database_read_failed",
                database_id=database_id,
                error=repr(e),
            )
            return None

    def _get_entry_title(self, properties: dict) -> str:
        """Extract the title text from a database entry's properties."""
        for prop_val in properties.values():
            if prop_val.get("type") == "title":
                return "".join(
                    t.get("plain_text", "") for t in prop_val.get("title", [])
                ) or "제목 없음"
        return "제목 없음"

    # ── Page fallback reading (unchanged from v5.0) ──

    async def _read_page_fallback(self, page_id: str, title: str) -> str | None:
        """Fallback for pages with no block content (e.g. database entries).

        1. Read page properties and format them as content.
        2. If a Google Sheets URL is found, also read the sheet data.
        """
        try:
            props = await self._read_page_properties(page_id)
            if not props:
                return None

            prop_lines = self._format_properties(props)
            parts = [f"## {title}"]
            if prop_lines:
                parts.append(prop_lines)

            # Check for linked Google Sheets
            sheet_url = self._extract_google_sheet_url(props)
            if sheet_url:
                spreadsheet_id = parse_spreadsheet_id(sheet_url)
                if spreadsheet_id:
                    logger.info(
                        "notion_reading_linked_sheet",
                        page_id=page_id,
                        title=title,
                        spreadsheet_id=spreadsheet_id,
                    )
                    try:
                        sheet_data = await asyncio.wait_for(
                            asyncio.to_thread(read_google_sheet, spreadsheet_id, max_rows=_SHEET_MAX_ROWS),
                            timeout=_SHEET_READ_TIMEOUT,
                        )
                        if sheet_data:
                            parts.append(f"\n### 시트 데이터\n{sheet_data}")
                    except asyncio.TimeoutError:
                        logger.warning("notion_fallback_sheet_timeout", spreadsheet_id=spreadsheet_id)
                        parts.append("\n### 시트 데이터\n(시트 데이터가 너무 커서 읽기 시간 초과)")

            # Return if we have any content beyond just the title
            if len(parts) > 1:
                return "\n".join(parts)
            return None

        except Exception as e:
            logger.warning(
                "notion_page_fallback_failed",
                page_id=page_id,
                error=repr(e),
            )
            return None

    async def _read_page_properties(self, page_id: str) -> dict | None:
        """Read a Notion page's properties via the Pages API."""
        try:
            resp = await self._request_with_retry(
                "GET",
                f"{_NOTION_BASE}/pages/{page_id}",
            )
        except _RETRYABLE_ERRORS as e:
            logger.warning(
                "notion_page_properties_failed_after_retries",
                page_id=page_id,
                error=type(e).__name__,
            )
            return None

        if resp.status_code != 200:
            logger.warning(
                "notion_page_properties_failed",
                page_id=page_id,
                status=resp.status_code,
            )
            return None
        return resp.json().get("properties", {})

    def _extract_google_sheet_url(self, properties: dict) -> str | None:
        """Extract a Google Sheets URL from Notion page properties."""
        for prop_name, prop_val in properties.items():
            prop_type = prop_val.get("type", "")

            if prop_type == "url":
                url = prop_val.get("url", "")
                if url and "docs.google.com/spreadsheets" in url:
                    return url

            elif prop_type == "files":
                for f in prop_val.get("files", []):
                    ext_url = f.get("external", {}).get("url", "")
                    if ext_url and "docs.google.com/spreadsheets" in ext_url:
                        return ext_url
                    name = f.get("name", "")
                    if "docs.google.com/spreadsheets" in name:
                        return name

            elif prop_type == "rich_text":
                for rt in prop_val.get("rich_text", []):
                    text = rt.get("plain_text", "")
                    if "docs.google.com/spreadsheets" in text:
                        return text
                    href = rt.get("href", "")
                    if href and "docs.google.com/spreadsheets" in href:
                        return href

        return None

    def _format_properties(self, properties: dict) -> str:
        """Format Notion page properties as text (excluding URLs and IDs)."""
        lines = []
        for prop_name, prop_val in properties.items():
            prop_type = prop_val.get("type", "")
            text = ""

            if prop_type == "title":
                text = "".join(
                    t.get("plain_text", "") for t in prop_val.get("title", [])
                )
            elif prop_type == "rich_text":
                text = "".join(
                    t.get("plain_text", "") for t in prop_val.get("rich_text", [])
                )
            elif prop_type == "number":
                val = prop_val.get("number")
                if val is not None:
                    text = str(val)
            elif prop_type == "select":
                sel = prop_val.get("select")
                if sel:
                    text = sel.get("name", "")
            elif prop_type == "multi_select":
                text = ", ".join(
                    s.get("name", "") for s in prop_val.get("multi_select", [])
                )
            elif prop_type == "date":
                d = prop_val.get("date")
                if d:
                    text = d.get("start", "")
            elif prop_type == "checkbox":
                text = "Yes" if prop_val.get("checkbox") else "No"

            if text:
                lines.append(f"- **{prop_name}**: {text}")

        return "\n".join(lines)

    # ── Answer generation (unchanged from v5.0) ──

    async def _generate_answer(
        self, query: str, content: str, model_type: str
    ) -> str:
        """Generate answer from Notion content using Flash for speed.

        Answer formatting is a lightweight task — Flash handles it well.
        Switched from Pro/Claude (80-100s) to Flash (10-20s) in v6.3.
        """
        # Truncate content if too large (prevent token limit issues)
        max_content = 12000
        if len(content) > max_content:
            content = content[:max_content] + "\n\n... (내용이 길어 일부 생략)"

        llm = get_flash_client()
        prompt = f"""다음은 Notion 워크스페이스에서 검색한 문서 내용입니다.
이 내용을 바탕으로 사용자의 질문에 **구조화되고 상세한 형태**로 답변하세요.

## Notion 문서 내용
{content}

## 사용자 질문
{query}

## 답변 형식 (반드시 아래 구조를 따르세요)

### 📋 [질문에 맞는 제목]

#### 요약
[1-2문장 핵심 요약. 중요 키워드는 **굵게** 표시]

#### 주요 내용
[문서에서 찾은 관련 정보를 구조화하여 정리]
- 항목이 여러 개면 번호 목록이나 표로 정리
- 프로세스/절차는 단계별 정리 (1. → 2. → 3.) — 각 단계에 구체적 설명 포함
- 수치나 기준이 있으면 마크다운 표로 정리
- URL/링크가 있으면 포함

#### 관련 세부 사항
[추가 맥락, 조건, 예외 사항, 주의 사항 등]
- 관련 담당자/부서 정보가 있으면 포함
- 기한이나 주기 정보가 있으면 포함
- 없으면 이 섹션 생략

---
*출처: Notion 사내 문서*

> 💡 **이런 것도 물어보세요**
> - [관련 사내 문서/프로세스 질문]
> - [이 주제의 세부 사항 질문]
> - [관련 업무 가이드 질문]

## 작성 규칙
1. 제공된 문서 내용을 기반으로 답변하세요. 문서에 없는 내용은 추측하지 마세요.
2. 질문과 정확히 일치하지 않더라도 관련 내용이 있으면 포함하세요.
3. 핵심 키워드와 중요 수치는 **굵게** 표시하세요.
4. 전문적이면서도 친절한 톤으로 답변하세요.
5. 내용이 간단한 경우(1-2줄)에는 "관련 세부 사항" 섹션을 생략하세요.
6. ⚠️ **후속 질문 제안 필수**: 답변 끝에 반드시 "💡 이런 것도 물어보세요" 블록을 포함하세요. Notion에서 답변 가능한 질문만 제안. 이 블록이 없으면 답변이 불완전합니다.

## ⚠️ 질문-답변 정합성 (최우선 — 반드시 준수!)
7. **사용자의 원래 질문에 정확히 답변하세요.** 질문을 임의로 재해석하거나 다른 주제로 답변하지 마세요.
8. **문서 내용이 질문 주제와 관련이 없으면, 절대 그 내용으로 답변하지 마세요.** 대신 "해당 내용은 현재 Notion 문서에서 찾을 수 없습니다."라고 솔직히 답하세요. 관련 없는 문서 내용을 억지로 제시하는 것은 금지!
9. 문서 내용이 질문과 부분적으로만 관련될 경우, 관련 부분만 답하고 나머지는 "해당 정보는 문서에 없습니다"로 명시하세요.
10. 예시: 사용자가 "반품 프로세스"를 물었는데 문서에 "틱톡샵 접속 방법"만 있다면 → "반품 프로세스 관련 내용은 Notion 문서에서 찾을 수 없습니다." (틱톡샵 접속 방법을 답변으로 제시하면 안 됨!)"""

        return llm.generate(prompt, temperature=0.2)
