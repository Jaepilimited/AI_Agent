"""
webhook 이벤트 수신 시 단일 페이지 동기화
"""

from app.core.logging import logger
from app.notion.client import NotionClient
from app.qdrant.store import QdrantStore
from app.services.ingest_page import ingest_page
from app.core.config import settings


def sync_page_update(page_id: str, team: str = None, hub_id: str = None) -> dict:
    """page.content_updated 이벤트 처리 - 해당 page 재색인"""
    hub_id = hub_id or settings.notion_hub_id
    team = team or "unknown"

    logger.info(f"sync 시작: page_id={page_id} team={team}")
    return ingest_page(page_id=page_id, team=team, hub_id=hub_id)


def sync_page_delete(page_id: str) -> dict:
    """page.deleted 이벤트 처리 - 해당 page chunk 삭제"""
    store = QdrantStore()
    store.delete_by_page_id(page_id)
    logger.info(f"page 삭제 처리 완료: {page_id}")
    return {"page_id": page_id, "status": "deleted"}
