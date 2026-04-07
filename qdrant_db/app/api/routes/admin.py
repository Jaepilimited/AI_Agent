"""
POST /admin/reindex/page  - 특정 page 재색인
GET  /admin/status        - 컬렉션 상태 조회
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import settings
from app.qdrant.store import QdrantStore
from app.services.ingest_page import ingest_page

router = APIRouter(prefix="/admin")


class ReindexRequest(BaseModel):
    page_id: str
    team: str
    hub_id: str = settings.notion_hub_id


@router.post("/reindex/page")
def reindex_page(req: ReindexRequest):
    result = ingest_page(
        page_id=req.page_id,
        team=req.team,
        hub_id=req.hub_id,
    )
    return result


@router.get("/status")
def status():
    store = QdrantStore()
    return store.get_collection_info()
