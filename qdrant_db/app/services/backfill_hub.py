"""
Hub 전체 backfill 서비스
"""

from app.core.config import settings
from app.core.logging import logger
from app.notion.client import NotionClient
from app.notion.discovery import discover_pages_from_hub, discover_child_pages, discover_linked_pages
from app.embeddings.client import EmbeddingClient
from app.qdrant.store import QdrantStore
from app.services.ingest_page import ingest_page


def backfill_hub(
    hub_page_id: str = None,
    hub_id: str = None,
    team_filter: str = None,
    force: bool = False,
    force_public: bool = False,
) -> dict:
    """
    Hub 페이지 전체 backfill.

    Args:
        hub_page_id: Hub 페이지 ID (None이면 설정값 사용)
        hub_id: hub 식별자
        team_filter: 특정 팀만 처리 (None이면 전체)
    """
    hub_page_id = hub_page_id or settings.notion_hub_page_id
    hub_id = hub_id or settings.notion_hub_id

    notion_client = NotionClient()
    embedder = EmbeddingClient()
    store = QdrantStore()

    store.ensure_collection()

    # 증분 처리: Qdrant 현재 상태 로드 (force=True면 전체 재적재)
    indexed_pages = {} if force else store.get_indexed_pages(hub_id_filter=hub_id)
    if force:
        logger.info("강제 전체 재적재 모드 (--force)")
    else:
        logger.info(f"Qdrant 기존 적재 페이지: {len(indexed_pages)}개")

    # Discovery
    discovered = discover_pages_from_hub(hub_page_id, hub_id, notion_client)

    if team_filter:
        discovered = [p for p in discovered if p.team == team_filter]
        logger.info(f"팀 필터 적용: '{team_filter}' → {len(discovered)}개 항목")

    if not discovered:
        logger.warning("수집 대상 없음")
        return {"total": 0, "ok": 0, "skip": 0, "error": 0, "details": []}

    # 일반 Notion 페이지의 하위 페이지 추가 수집
    # - child_page: CS팀처럼 child_page 블록으로 구성된 중첩 페이지
    # - link_to_page: EAST DMS처럼 페이지 내 link_to_page로 연결된 하위 페이지
    existing_ids = {p.page_id for p in discovered}
    extra_pages = []
    for dp in discovered:
        if dp.is_inline or dp.is_public:
            continue
        shared_seen = set(existing_ids)

        for page in discover_child_pages(dp.page_id, dp.hub_id, dp.team, notion_client, shared_seen):
            if page.page_id not in existing_ids:
                existing_ids.add(page.page_id)
                extra_pages.append(page)

        for page in discover_linked_pages(dp.page_id, dp.hub_id, dp.team, notion_client, shared_seen):
            if page.page_id not in existing_ids:
                existing_ids.add(page.page_id)
                extra_pages.append(page)

    if extra_pages:
        logger.info(f"하위 페이지 {len(extra_pages)}개 추가 수집 (child_page + link_to_page)")
        discovered = discovered + extra_pages

    logger.info(f"총 {len(discovered)}개 항목 ingest 시작")

    results = {"total": len(discovered), "ok": 0, "skip": 0, "error": 0, "details": []}

    for dp in discovered:
        result = ingest_page(
            page_id=dp.page_id,
            team=dp.team,
            hub_id=dp.hub_id,
            notion_client=notion_client,
            embedder=embedder,
            store=store,
            is_inline=dp.is_inline,
            inline_title=dp.inline_title,
            inline_markdown=dp.inline_markdown,
            is_public=dp.is_public,
            public_url=dp.public_url,
            existing_last_edited=indexed_pages.get(dp.page_id),
            force_public=force_public,
        )
        result["team"] = dp.team
        results["details"].append(result)
        status = result.get("status", "error")
        if status in results:
            results[status] += 1
        else:
            results["error"] += 1

    info = store.get_collection_info()
    logger.info(
        f"backfill 완료 | ok={results['ok']} skip={results['skip']} "
        f"error={results['error']} | Qdrant 총 {info['points_count']}개 points"
    )

    return results
