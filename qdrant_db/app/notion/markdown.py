"""
Notion 페이지 블록을 markdown으로 변환 + 메타데이터 수집
"""

from app.core.logging import logger
from app.notion.client import NotionClient, _format_uuid
from app.notion.models import PageMetadata


# ── 블록 타입별 markdown 변환 ────────────────────────────────────────────────

def _rich_text_to_plain(rich_text: list) -> str:
    return "".join(t.get("plain_text", "") for t in rich_text)


def _block_to_markdown(block: dict, depth: int = 0) -> str:
    """단일 블록 → markdown 문자열 (heading, list, code, callout 등 처리)"""
    btype = block.get("type", "")
    data = block.get(btype) or {}  # Notion API가 None을 반환하는 블록 타입 대응
    text = _rich_text_to_plain(data.get("rich_text", []))
    indent = "  " * depth

    if btype == "heading_1":
        return f"\n# {text}\n"
    if btype == "heading_2":
        return f"\n## {text}\n"
    if btype == "heading_3":
        return f"\n### {text}\n"
    if btype == "paragraph":
        return f"{text}\n" if text else ""
    if btype == "bulleted_list_item":
        return f"{indent}- {text}\n"
    if btype == "numbered_list_item":
        return f"{indent}1. {text}\n"
    if btype == "to_do":
        checked = "x" if data.get("checked") else " "
        return f"{indent}- [{checked}] {text}\n"
    if btype == "quote":
        return f"> {text}\n"
    if btype == "callout":
        icon = (data.get("icon") or {}).get("emoji", "")
        return f"> {icon} {text}\n"
    if btype == "toggle":
        return f"**{text}**\n" if text else ""
    if btype == "code":
        lang = data.get("language", "")
        return f"\n```{lang}\n{text}\n```\n"
    if btype == "divider":
        return "\n---\n"
    if btype in ("table_of_contents", "breadcrumb", "column_list", "column"):
        return ""

    # link_to_page는 discovery에서 처리하므로 markdown에서는 skip
    if btype == "link_to_page":
        return ""

    return f"{text}\n" if text else ""


_RECURSE_TYPES = {
    "toggle", "bulleted_list_item", "numbered_list_item",
    "quote", "callout", "to_do", "column", "column_list",
    "paragraph", "child_page",
    "heading_1", "heading_2", "heading_3",  # 토글형 헤딩 대응
}


def _get_all_blocks(
    client: NotionClient,
    block_id: str,
    depth: int = 0,
    seen_ids: set = None,
) -> list[dict]:
    """블록 전체를 페이지네이션 처리하여 반환 (재귀 depth 제한: 5)

    - toggle / child_page / 헤딩: has_children이면 재귀 탐색
    - link_to_page: 링크된 페이지의 블록도 재귀 수집 (깊이 3 제한)
    """
    if depth > 5:
        return []

    if seen_ids is None:
        seen_ids = set()

    if block_id in seen_ids:
        return []
    seen_ids.add(block_id)

    results = []
    cursor = None

    while True:
        try:
            response = client.get_blocks(block_id, cursor)
        except Exception as e:
            logger.warning(f"블록 조회 실패 ({block_id}): {e}")
            break

        for block in response.get("results", []):
            results.append(block)
            btype = block.get("type", "")

            # 자식 블록 재귀 탐색 (현재 페이지 내부 구조만)
            if block.get("has_children") and btype in _RECURSE_TYPES:
                children = _get_all_blocks(client, block["id"], depth + 1, seen_ids)
                results.extend(children)
            # link_to_page는 별도 페이지로 독립 적재 (discovery 단계에서 처리)
            # 여기서 재귀 수집하면 API 호출 폭발 → 타임아웃 원인

        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")

    return results


def _normalize_markdown(md: str) -> str:
    """연속 빈 줄 2개 이상 → 1개로 정리"""
    import re
    return re.sub(r"\n{3,}", "\n\n", md).strip()


# ── 공개 인터페이스 ───────────────────────────────────────────────────────────

def get_page_metadata(page_id: str, client: NotionClient = None) -> PageMetadata:
    """페이지 메타데이터 조회"""
    if client is None:
        client = NotionClient()

    fid = _format_uuid(page_id)
    page = client.get_page(fid)

    title = client.get_page_title(page)
    url = page.get("url", f"https://www.notion.so/{fid.replace('-', '')}")
    last_edited = page.get("last_edited_time", "")

    return PageMetadata(
        page_id=fid,
        title=title,
        url=url,
        last_edited_time=last_edited,
        breadcrumb=[title],  # breadcrumb는 ingest 단계에서 팀명으로 보강
    )


def get_page_markdown(page_id: str, client: NotionClient = None) -> str:
    """
    페이지 블록 전체를 markdown으로 변환하여 반환.
    변환 실패 시 예외를 그대로 전파 (silent failure 금지).
    """
    if client is None:
        client = NotionClient()

    fid = _format_uuid(page_id)
    blocks = _get_all_blocks(client, fid)

    lines = []
    for block in blocks:
        md = _block_to_markdown(block)
        if md:
            lines.append(md)

    raw = "".join(lines)
    return _normalize_markdown(raw)
