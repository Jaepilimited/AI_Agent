"""
Notion hub 기준 임베딩 현황 진단 서비스.
"""

from collections import defaultdict
from typing import Any

from app.core.config import settings
from app.core.logging import logger
from app.notion.client import NotionClient
from app.notion.discovery import discover_pages_from_hub
from app.notion.markdown import get_page_metadata
from app.notion.models import DiscoveredPage
from app.qdrant.store import QdrantStore


def generate_embedding_diagnostics(
    hub_page_id: str = None,
    hub_id: str = None,
    team_filter: str = None,
    resolve_metadata: bool = True,
) -> dict[str, Any]:
    """
    Hub에 연결된 페이지와 Qdrant 적재 현황을 대조해 진단 결과를 반환한다.
    """
    hub_page_id = hub_page_id or settings.notion_hub_page_id
    hub_id = hub_id or settings.notion_hub_id

    notion_client = NotionClient()
    store = QdrantStore()

    discovered = discover_pages_from_hub(
        hub_page_id=hub_page_id,
        hub_id=hub_id,
        client=notion_client,
    )

    if team_filter:
        discovered = [page for page in discovered if page.team == team_filter]

    discovered_pages = [
        _build_discovered_item(page, notion_client, resolve_metadata)
        for page in discovered
    ]

    indexed_pages = store.list_page_chunk_stats(
        hub_id_filter=hub_id,
        team_filter=team_filter,
    )
    indexed_map = {page["page_id"]: page for page in indexed_pages}
    discovered_map = {page["page_id"]: page for page in discovered_pages}

    embedded_pages: list[dict[str, Any]] = []
    missing_pages: list[dict[str, Any]] = []
    outdated_pages: list[dict[str, Any]] = []
    metadata_errors: list[dict[str, Any]] = []

    for page in discovered_pages:
        indexed = indexed_map.get(page["page_id"])

        if page.get("metadata_error"):
            metadata_errors.append(page)

        if indexed is None:
            missing_pages.append({
                **page,
                "status": "missing",
                "reason": "Qdrant에 해당 page_id가 없습니다.",
            })
            continue

        merged = _merge_page_state(page, indexed)
        if _is_outdated(merged):
            merged["status"] = "outdated"
            merged["reason"] = "Notion 최신 수정 시간이 현재 인덱스보다 더 최신입니다."
            outdated_pages.append(merged)
        else:
            merged["status"] = "embedded"
            merged["reason"] = ""
            embedded_pages.append(merged)

    extra_pages: list[dict[str, Any]] = []
    for indexed in indexed_pages:
        if indexed["page_id"] in discovered_map:
            continue

        extra_pages.append({
            "page_id": indexed["page_id"],
            "team": indexed.get("team", ""),
            "hub_id": indexed.get("hub_id", ""),
            "title": indexed.get("page_title", "") or "(untitled)",
            "page_url": indexed.get("page_url", ""),
            "source_type": "qdrant_only",
            "chunk_count": indexed.get("chunk_count", 0),
            "indexed_last_edited_time": indexed.get("last_edited_time", ""),
            "status": "extra",
            "reason": "현재 hub discovery에는 없지만 Qdrant에는 남아 있습니다.",
        })

    report = {
        "hub_page_id": hub_page_id,
        "hub_id": hub_id,
        "team_filter": team_filter,
        "collection_exists": store.collection_exists(),
        "summary": {
            "expected_pages": len(discovered_pages),
            "embedded_pages": len(embedded_pages),
            "missing_pages": len(missing_pages),
            "outdated_pages": len(outdated_pages),
            "extra_pages": len(extra_pages),
            "metadata_error_pages": len(metadata_errors),
            "indexed_pages": len(indexed_pages),
        },
        "team_summary": _build_team_summary(
            discovered_pages=discovered_pages,
            embedded_pages=embedded_pages,
            missing_pages=missing_pages,
            outdated_pages=outdated_pages,
            extra_pages=extra_pages,
        ),
        "embedded_pages": _sort_pages(embedded_pages),
        "missing_pages": _sort_pages(missing_pages),
        "outdated_pages": _sort_pages(outdated_pages),
        "extra_pages": _sort_pages(extra_pages),
        "metadata_error_pages": _sort_pages(metadata_errors),
    }

    logger.info(
        "embedding diagnostics complete | expected=%s embedded=%s missing=%s outdated=%s extra=%s",
        report["summary"]["expected_pages"],
        report["summary"]["embedded_pages"],
        report["summary"]["missing_pages"],
        report["summary"]["outdated_pages"],
        report["summary"]["extra_pages"],
    )
    return report


def _build_discovered_item(
    page: DiscoveredPage,
    notion_client: NotionClient,
    resolve_metadata: bool,
) -> dict[str, Any]:
    item = {
        "page_id": page.page_id,
        "team": page.team,
        "hub_id": page.hub_id,
        "title": _title_from_discovered_page(page),
        "page_url": page.public_url if page.is_public else "",
        "source_type": _source_type(page),
        "current_last_edited_time": "",
        "indexed_last_edited_time": "",
        "chunk_count": 0,
        "metadata_error": "",
    }

    if not resolve_metadata or page.is_inline or page.is_public:
        return item

    try:
        metadata = get_page_metadata(page.page_id, notion_client)
        item["title"] = metadata.title or item["title"]
        item["page_url"] = metadata.url or item["page_url"]
        item["current_last_edited_time"] = metadata.last_edited_time or ""
    except Exception as exc:
        item["metadata_error"] = str(exc)
        logger.warning("diagnostics metadata lookup failed (%s): %s", page.page_id, exc)

    return item


def _title_from_discovered_page(page: DiscoveredPage) -> str:
    if page.is_inline:
        return page.inline_title or page.team
    if page.is_public:
        return page.public_url or page.page_id
    return page.page_id


def _source_type(page: DiscoveredPage) -> str:
    if page.is_inline:
        return "inline"
    if page.is_public:
        return "public"
    return "notion_page"


def _merge_page_state(
    discovered: dict[str, Any],
    indexed: dict[str, Any],
) -> dict[str, Any]:
    return {
        **discovered,
        "title": discovered.get("title") or indexed.get("page_title", "") or discovered["page_id"],
        "page_url": discovered.get("page_url") or indexed.get("page_url", ""),
        "chunk_count": indexed.get("chunk_count", 0),
        "indexed_last_edited_time": indexed.get("last_edited_time", ""),
        "indexed_page_title": indexed.get("page_title", ""),
        "indexed_page_url": indexed.get("page_url", ""),
    }


def _is_outdated(page: dict[str, Any]) -> bool:
    current_last_edited = page.get("current_last_edited_time", "")
    indexed_last_edited = page.get("indexed_last_edited_time", "")
    return bool(current_last_edited and indexed_last_edited and current_last_edited > indexed_last_edited)


def _build_team_summary(
    discovered_pages: list[dict[str, Any]],
    embedded_pages: list[dict[str, Any]],
    missing_pages: list[dict[str, Any]],
    outdated_pages: list[dict[str, Any]],
    extra_pages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    teams: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "expected_pages": 0,
            "embedded_pages": 0,
            "missing_pages": 0,
            "outdated_pages": 0,
            "extra_pages": 0,
        }
    )

    for page in discovered_pages:
        teams[page["team"]]["expected_pages"] += 1
    for page in embedded_pages:
        teams[page["team"]]["embedded_pages"] += 1
    for page in missing_pages:
        teams[page["team"]]["missing_pages"] += 1
    for page in outdated_pages:
        teams[page["team"]]["outdated_pages"] += 1
    for page in extra_pages:
        teams[page["team"]]["extra_pages"] += 1

    return [
        {"team": team, **counts}
        for team, counts in sorted(teams.items(), key=lambda item: item[0])
    ]


def _sort_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        pages,
        key=lambda item: (
            item.get("team", ""),
            item.get("title", ""),
            item.get("page_id", ""),
        ),
    )
