"""
Hub 페이지에서 팀별 대상 페이지 목록을 수집하는 Discovery 모듈

구조:
  A (Hub 페이지)
  ├── [토글] 팀1
  │   ├── link_to_page 블록         → page_id 수집
  │   ├── child_page 블록           → page_id 수집
  │   ├── embed / link_preview 블록 → notion.site면 공개 페이지 수집
  │   ├── page mention (rich_text)  → page_id 수집
  │   └── notion.so URL             → page_id 수집 / 외부 URL은 skip
  ├── [토글] Craver (줄글만 있는 경우)
  │   └── paragraph 블록들          → 텍스트 직접 수집 (인라인 콘텐츠)
  ├── [토글] 팀2
  │   └── 노션X                     → skip
  └── ...
"""

import re
from app.core.logging import logger
from app.notion.client import NotionClient, _format_uuid
from app.notion.models import DiscoveredPage

# notion.so 페이지 URL에서 page_id 추출 (32자리 hex)
_NOTION_URL_RE = re.compile(
    r"notion\.so/(?:[^/]+/)?(?:[^-]+-)?([0-9a-f]{32})", re.IGNORECASE
)

_NO_NOTION_KEYWORDS = ["노션x", "노션 x", "notion x", "notionx", "없음", "미사용"]

# 재귀 탐색 시 수집만 하고 내부로는 들어가지 않을 블록 타입
# (이 블록들 자체가 페이지 참조이므로 내용을 파고들 필요 없음)
_PAGE_REF_TYPES = {"child_page", "link_to_page"}


def _is_no_notion(text: str) -> bool:
    return any(kw in text.lower() for kw in _NO_NOTION_KEYWORDS)


def _extract_page_id_from_url(url: str) -> str | None:
    m = _NOTION_URL_RE.search(url)
    if m:
        return _format_uuid(m.group(1))
    return None


def _is_notion_api_url(url: str) -> bool:
    """Notion API로 접근 가능한 URL (notion.so)"""
    return "notion.so" in url.lower()


def _is_notion_public_url(url: str) -> bool:
    """공개 게시된 Notion 페이지 (notion.site) - API 불가, 스크래핑 필요"""
    return "notion.site" in url.lower()


def _extract_rich_text_plain(rich_text: list) -> str:
    return "".join(t.get("plain_text", "") for t in rich_text)


def _blocks_to_markdown(blocks: list[dict]) -> str:
    """블록 목록을 단순 markdown 텍스트로 변환 (인라인 콘텐츠용)"""
    lines = []
    for block in blocks:
        btype = block.get("type", "")
        data = block.get(btype, {})
        text = _extract_rich_text_plain(data.get("rich_text", []))

        if not text.strip():
            continue

        if btype == "heading_1":
            lines.append(f"# {text}")
        elif btype == "heading_2":
            lines.append(f"## {text}")
        elif btype == "heading_3":
            lines.append(f"### {text}")
        elif btype == "bulleted_list_item":
            lines.append(f"- {text}")
        elif btype == "numbered_list_item":
            lines.append(f"1. {text}")
        elif btype == "quote":
            lines.append(f"> {text}")
        elif btype == "callout":
            lines.append(f"> {text}")
        else:
            lines.append(text)

    return "\n".join(lines)


def _fetch_nested_blocks(client: NotionClient, block_id: str, depth: int = 0) -> list[dict]:
    """블록의 자식을 재귀적으로 평탄하게 수집.

    toggle / column / synced_block 등 어떤 타입이든 자식이 있으면 내려간다.
    단, child_page / link_to_page 는 페이지 참조 자체이므로 내부를 파고들지 않는다.
    """
    if depth > 5:
        return []
    results = []
    cursor = None
    while True:
        resp = client.get_blocks(block_id, cursor)
        for child in resp.get("results", []):
            results.append(child)
            if child.get("has_children") and child.get("type") not in _PAGE_REF_TYPES:
                results.extend(_fetch_nested_blocks(client, child["id"], depth + 1))
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return results


def _collect_from_toggle_children(
    blocks: list[dict],
    team: str,
    hub_id: str,
) -> tuple[list[DiscoveredPage], str]:
    """
    토글 children(평탄화된)에서 Notion 페이지 링크와 인라인 텍스트를 수집.

    Returns:
        (페이지 링크 목록, 인라인 텍스트 markdown)
    """
    results: list[DiscoveredPage] = []
    seen: set[str] = set()

    def add(page_id: str):
        fid = _format_uuid(page_id)
        if fid and fid not in seen:
            seen.add(fid)
            results.append(DiscoveredPage(page_id=fid, team=team, hub_id=hub_id))

    def add_public(url: str):
        if url not in seen:
            seen.add(url)
            from app.notion.public_scraper import extract_public_page_id
            page_id = extract_public_page_id(url)
            results.append(DiscoveredPage(
                page_id=page_id,
                team=team,
                hub_id=hub_id,
                is_public=True,
                public_url=url,
            ))
            logger.info(f"[{team}] 공개 Notion 페이지 수집: {url}")

    def _handle_url(url: str):
        if _is_notion_api_url(url):
            pid = _extract_page_id_from_url(url)
            if pid:
                add(pid)
            else:
                logger.debug(f"[{team}] notion URL page_id 추출 실패: {url}")
        elif _is_notion_public_url(url):
            add_public(url)
        else:
            logger.info(f"[{team}] 외부 사이트 URL skip: {url}")

    for block in blocks:
        btype = block.get("type", "")

        # ── child_page 블록 ──────────────────────────────────────────────
        if btype == "child_page":
            add(block["id"])
            continue

        # ── link_to_page 블록 ────────────────────────────────────────────
        if btype == "link_to_page":
            ltp = block.get("link_to_page", {})
            ltp_type = ltp.get("type", "")
            if ltp_type == "page_id":
                add(ltp["page_id"])
            elif ltp_type == "database_id":
                logger.debug(f"[{team}] database_id link skip: {ltp.get('database_id')}")
            continue

        # ── embed / link_preview 블록 (notion.site 포함) ─────────────────
        if btype in ("embed", "link_preview"):
            url = block.get(btype, {}).get("url", "")
            if url:
                _handle_url(url)
            continue

        # ── bookmark 블록 ────────────────────────────────────────────────
        if btype == "bookmark":
            url = block.get("bookmark", {}).get("url", "")
            if url:
                _handle_url(url)
            continue

        # ── rich_text 내 mention(page) / URL ─────────────────────────────
        block_data = block.get(btype, {})
        rich_text = block_data.get("rich_text", [])

        for rt in rich_text:
            rt_type = rt.get("type", "")

            if rt_type == "mention":
                mention = rt.get("mention", {})
                if mention.get("type") == "page":
                    add(mention["page"]["id"])
                continue

            if rt_type == "text":
                href = rt.get("href") or rt.get("text", {}).get("link", {}) or {}
                url = href.get("url", "") if isinstance(href, dict) else str(href)

                if not url:
                    plain = rt.get("plain_text", "")
                    if plain.startswith("http"):
                        url = plain

                if url:
                    _handle_url(url)

    # 페이지 링크가 없는 경우 → 인라인 텍스트 수집
    inline_markdown = ""
    if not results:
        inline_markdown = _blocks_to_markdown(blocks)

    return results, inline_markdown


def _discover_from_database(
    database_id: str,
    hub_id: str,
    team: str,
    client: NotionClient,
    seen_ids: set[str],
) -> list[DiscoveredPage]:
    """
    Notion 데이터베이스(child_database)의 모든 페이지 엔트리를 수집.
    각 엔트리는 독립 페이지이므로 재귀적으로 하위 페이지도 탐색.
    """
    results = []
    cursor = None

    while True:
        try:
            resp = client.query_database(database_id, cursor)
        except Exception as e:
            logger.warning(f"database query 실패 ({database_id}): {e}")
            break

        for entry in resp.get("results", []):
            if entry.get("object") != "page":
                continue
            entry_id = _format_uuid(entry["id"])
            if entry_id and entry_id not in seen_ids:
                seen_ids.add(entry_id)
                results.append(DiscoveredPage(page_id=entry_id, team=team, hub_id=hub_id))
                # 엔트리 하위의 child_page / child_database도 재귀 수집
                results.extend(
                    discover_child_pages(entry_id, hub_id, team, client, seen_ids)
                )

        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")

    return results


def discover_child_pages(
    page_id: str,
    hub_id: str,
    team: str,
    client: NotionClient,
    seen_ids: set[str] = None,
) -> list[DiscoveredPage]:
    """
    페이지의 child_page / child_database 블록을 재귀적으로 탐색하여 하위 페이지 목록 반환.
    - child_page: 일반 하위 페이지
    - child_database: 갤러리/표 형태 DB → query_database API로 엔트리 수집
    """
    if seen_ids is None:
        seen_ids = set()

    results = []

    try:
        all_blocks = _fetch_nested_blocks(client, page_id)
    except Exception as e:
        logger.warning(f"child_page 탐색 실패 ({page_id}): {e}")
        return results

    for block in all_blocks:
        btype = block.get("type")

        if btype == "child_page":
            child_id = _format_uuid(block["id"])
            if child_id and child_id not in seen_ids:
                seen_ids.add(child_id)
                results.append(DiscoveredPage(page_id=child_id, team=team, hub_id=hub_id))
                results.extend(
                    discover_child_pages(child_id, hub_id, team, client, seen_ids)
                )

        elif btype == "child_database":
            db_id = _format_uuid(block["id"])
            if db_id and db_id not in seen_ids:
                seen_ids.add(db_id)
                logger.info(f"[{team}] child_database 발견, DB 쿼리 시작: {db_id}")
                results.extend(
                    _discover_from_database(db_id, hub_id, team, client, seen_ids)
                )

    return results


def discover_linked_pages(
    page_id: str,
    hub_id: str,
    team: str,
    client: NotionClient,
    seen_ids: set[str] = None,
    depth: int = 0,
) -> list[DiscoveredPage]:
    """
    페이지 내 link_to_page 블록을 탐색하여 연결된 페이지 목록 반환 (최대 2단계).

    markdown.py에서 link_to_page를 재귀 수집하면 API 호출이 폭발하므로,
    discovery 단계에서 별도 페이지로 분리해 독립 적재한다.
    """
    if seen_ids is None:
        seen_ids = set()
    if depth > 2:
        return []

    results = []
    cursor = None

    while True:
        try:
            resp = client.get_blocks(page_id, cursor)
        except Exception as e:
            logger.warning(f"linked_page 탐색 실패 ({page_id}): {e}")
            break

        for block in resp.get("results", []):
            btype = block.get("type", "")
            if btype == "link_to_page":
                ltp = block.get("link_to_page", {})
                if ltp.get("type") == "page_id":
                    linked_id = _format_uuid(ltp["page_id"])
                    if linked_id and linked_id not in seen_ids:
                        seen_ids.add(linked_id)
                        results.append(DiscoveredPage(
                            page_id=linked_id,
                            team=team,
                            hub_id=hub_id,
                        ))
                        results.extend(
                            discover_linked_pages(linked_id, hub_id, team, client, seen_ids, depth + 1)
                        )

        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")

    return results


def discover_pages_from_hub(
    hub_page_id: str,
    hub_id: str,
    client: NotionClient = None,
) -> list[DiscoveredPage]:
    """
    Hub 페이지의 토글 구조를 순회하여 팀별 대상 페이지 목록 반환.

    - 토글 children에 페이지 링크가 있으면 → DiscoveredPage(is_inline=False)
    - 토글 children에 링크 없고 텍스트만 있으면 → DiscoveredPage(is_inline=True)
    - "노션X" 등 → skip

    Returns:
        DiscoveredPage 리스트 (중복 제거, Hub 자신 제외)
    """
    if client is None:
        client = NotionClient()

    hub_fid = _format_uuid(hub_page_id)
    all_results: list[DiscoveredPage] = []
    all_page_ids: set[str] = set()
    cursor = None

    logger.info(f"Hub discovery 시작: {hub_fid}")

    while True:
        response = client.get_blocks(hub_fid, cursor)
        blocks = response.get("results", [])

        for block in blocks:
            btype = block.get("type", "")

            if btype != "toggle":
                continue

            toggle_data = block.get("toggle", {})
            toggle_text = _extract_rich_text_plain(toggle_data.get("rich_text", []))

            if not toggle_text.strip():
                continue

            if _is_no_notion(toggle_text):
                logger.info(f"팀 skip (노션 미사용): {toggle_text}")
                continue

            team_name = toggle_text.strip()

            if not block.get("has_children"):
                logger.info(f"[{team_name}] 토글 children 없음, skip")
                continue

            # 토글 직접 자식 수집
            children_cursor = None
            direct_children: list[dict] = []

            while True:
                children_resp = client.get_blocks(block["id"], children_cursor)
                direct_children.extend(children_resp.get("results", []))
                if not children_resp.get("has_more"):
                    break
                children_cursor = children_resp.get("next_cursor")

            # toggle / column / synced_block 등 중첩 블록을 재귀적으로 평탄화
            toggle_children = list(direct_children)
            for child in direct_children:
                if child.get("has_children") and child.get("type") not in _PAGE_REF_TYPES:
                    toggle_children.extend(_fetch_nested_blocks(client, child["id"]))

            # children 텍스트 전체로 "노션X" 확인
            children_plain = " ".join(
                _extract_rich_text_plain(
                    child.get(child.get("type", ""), {}).get("rich_text", [])
                )
                for child in toggle_children
            )
            if _is_no_notion(children_plain) and len(toggle_children) <= 2:
                logger.info(f"[{team_name}] 노션 미사용 표시 확인, skip")
                continue

            # 페이지 링크 수집 + 인라인 텍스트 수집
            found, inline_markdown = _collect_from_toggle_children(
                toggle_children, team_name, hub_id
            )

            if found:
                deduped = [
                    p for p in found
                    if p.page_id != hub_fid and p.page_id not in all_page_ids
                ]
                for p in deduped:
                    all_page_ids.add(p.page_id)
                all_results.extend(deduped)
                logger.info(f"[{team_name}] 페이지 링크 {len(deduped)}개 수집")

            elif inline_markdown.strip():
                inline_id = f"inline:{hub_id}:{team_name}"
                all_results.append(
                    DiscoveredPage(
                        page_id=inline_id,
                        team=team_name,
                        hub_id=hub_id,
                        is_inline=True,
                        inline_title=team_name,
                        inline_markdown=inline_markdown,
                    )
                )
                logger.info(f"[{team_name}] 인라인 콘텐츠 수집 ({len(inline_markdown)}자)")

            else:
                logger.warning(f"[{team_name}] 수집 가능한 콘텐츠 없음")

        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")

    logger.info(f"Hub discovery 완료: 총 {len(all_results)}개 항목")
    return all_results
