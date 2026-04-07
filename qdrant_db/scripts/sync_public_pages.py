"""
notion.site 공개 페이지 동기화 스크립트 (주기적 업데이트용)

Hub를 다시 순회하여 notion.site 페이지만 재스크래핑 → Qdrant 업데이트.
페이지가 수정되어도 delete + re-ingest로 최신 상태 유지.

실행:
  python scripts/sync_public_pages.py          # 전체 동기화
  python scripts/sync_public_pages.py --team HR  # 특정 팀만
"""

import sys
import os
import argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.core.logging import logger
from app.notion.client import NotionClient
from app.notion.discovery import discover_pages_from_hub
from app.embeddings.client import EmbeddingClient
from app.qdrant.store import QdrantStore
from app.services.ingest_page import ingest_page


def sync_public_pages(team_filter: str = None) -> dict:
    notion_client = NotionClient()
    embedder = EmbeddingClient()
    store = QdrantStore()
    store.ensure_collection()

    # Hub discovery
    discovered = discover_pages_from_hub(
        settings.notion_hub_page_id,
        settings.notion_hub_id,
        notion_client,
    )

    # 공개 페이지만 필터링
    public_pages = [p for p in discovered if p.is_public]

    if team_filter:
        public_pages = [p for p in public_pages if p.team == team_filter]

    if not public_pages:
        logger.warning("동기화할 공개 페이지 없음")
        return {"total": 0, "ok": 0, "skip": 0, "error": 0}

    logger.info(f"공개 페이지 {len(public_pages)}개 동기화 시작")
    results = {"total": len(public_pages), "ok": 0, "skip": 0, "error": 0}

    for dp in public_pages:
        logger.info(f"[{dp.team}] 동기화 중: {dp.public_url}")
        result = ingest_page(
            page_id=dp.page_id,
            team=dp.team,
            hub_id=dp.hub_id,
            embedder=embedder,
            store=store,
            is_public=True,
            public_url=dp.public_url,
        )
        status = result.get("status", "error")
        results[status] = results.get(status, 0) + 1

    info = store.get_collection_info()
    logger.info(
        f"동기화 완료 | ok={results['ok']} skip={results['skip']} "
        f"error={results['error']} | Qdrant 총 {info['points_count']}개 points"
    )
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--team", help="특정 팀만 동기화")
    args = parser.parse_args()

    sync_public_pages(team_filter=args.team)
