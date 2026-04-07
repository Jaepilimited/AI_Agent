"""
POST /webhooks/notion - Notion webhook 이벤트 수신

지원 이벤트:
  - page.created
  - page.content_updated
  - page.properties_updated
  - page.deleted
"""

from fastapi import APIRouter, Request, HTTPException
from app.core.logging import logger
from app.services.sync_page import sync_page_update, sync_page_delete

router = APIRouter(prefix="/webhooks")


@router.post("/notion")
async def notion_webhook(request: Request):
    body = await request.json()

    event_type = body.get("type", "")
    page_id = (
        body.get("entity", {}).get("id")
        or body.get("page", {}).get("id")
        or body.get("id")
    )

    if not page_id:
        raise HTTPException(status_code=400, detail="page_id를 찾을 수 없습니다")

    logger.info(f"webhook 수신: type={event_type} page_id={page_id}")

    if event_type in ("page.created", "page.content_updated", "page.properties_updated"):
        result = sync_page_update(page_id=page_id)
        return {"status": "reindexed", "result": result}

    if event_type == "page.deleted":
        result = sync_page_delete(page_id=page_id)
        return {"status": "deleted", "result": result}

    logger.info(f"처리하지 않는 이벤트 타입: {event_type}")
    return {"status": "ignored", "type": event_type}
