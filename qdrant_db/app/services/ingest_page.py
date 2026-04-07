"""
단일 페이지 수집 → chunk → embedding → Qdrant upsert 파이프라인

두 가지 경로:
  1. 일반 Notion 페이지 (is_inline=False): Notion API로 markdown 수집
  2. 인라인 콘텐츠 (is_inline=True): Hub 토글 텍스트를 직접 사용
"""

from app.core.logging import logger
from app.notion.client import NotionClient
from app.notion.markdown import get_page_markdown, get_page_metadata
from app.chunking.chunker import chunk_markdown
from app.embeddings.client import EmbeddingClient
from app.qdrant.store import QdrantStore
from app.utils.hashes import sha256
from app.utils.ids import make_point_id


def ingest_page(
    page_id: str,
    team: str,
    hub_id: str,
    notion_client: NotionClient = None,
    embedder: EmbeddingClient = None,
    store: QdrantStore = None,
    # 인라인 콘텐츠용
    is_inline: bool = False,
    inline_title: str = "",
    inline_markdown: str = "",
    # 공개 notion.site 페이지용
    is_public: bool = False,
    public_url: str = "",
    # 증분 처리용: Qdrant에 저장된 last_edited_time (None이면 미적재)
    existing_last_edited: str = None,
    # 공개 페이지 강제 재스크래핑 (daily 스크립트용)
    force_public: bool = False,
) -> dict:
    """
    단일 페이지(또는 인라인 콘텐츠)를 Qdrant에 적재.

    Returns:
        {"page_id": ..., "chunks": N, "status": "ok" | "skip" | "error", "reason": ...}
    """
    if embedder is None:
        embedder = EmbeddingClient()
    if store is None:
        store = QdrantStore()

    # ── 공개 notion.site 페이지 경로 ──────────────────────────────────────
    if is_public:
        # notion.site는 last_edited_time을 알 수 없으므로 존재 여부만 확인
        if existing_last_edited is not None:
            logger.debug(f"[{team}] skip (이미 적재됨): {page_id}")
            return {"page_id": page_id, "chunks": 0, "status": "skip", "reason": "already indexed"}
        return _ingest_public(
            page_id=page_id,
            team=team,
            hub_id=hub_id,
            url=public_url,
            embedder=embedder,
            store=store,
        )

    # ── 인라인 콘텐츠 경로 ─────────────────────────────────────────────────
    if is_inline:
        # 인라인 콘텐츠도 last_edited_time 없으므로 존재 여부만 확인
        if existing_last_edited is not None:
            logger.debug(f"[{team}] skip (이미 적재됨): {page_id}")
            return {"page_id": page_id, "chunks": 0, "status": "skip", "reason": "already indexed"}
        return _ingest_inline(
            page_id=page_id,
            team=team,
            hub_id=hub_id,
            title=inline_title,
            markdown=inline_markdown,
            embedder=embedder,
            store=store,
        )

    # ── 일반 Notion 페이지 경로 ────────────────────────────────────────────
    if notion_client is None:
        notion_client = NotionClient()

    # 증분 처리: 이전에 공개 페이지로 스크래핑된 경우 skip
    # (last_edited_time=""로 저장된 페이지 = Playwright로 적재된 것)
    # force_public=True이면 재스크래핑
    if existing_last_edited == "" and not force_public:
        logger.debug(f"[{team}] skip (공개 페이지 이미 적재됨): {page_id}")
        return {"page_id": page_id, "chunks": 0, "status": "skip", "reason": "already indexed"}

    # 1. 메타데이터 조회
    try:
        meta = get_page_metadata(page_id, notion_client)
    except Exception as e:
        error_msg = str(e)
        # 통합 권한 없음 → 공개 URL로 Playwright fallback
        if "Could not find" in error_msg or "not shared" in error_msg.lower():
            public_url = f"https://www.notion.so/{page_id.replace('-', '')}"
            logger.info(f"[{team}] API 접근 불가, 공개 페이지로 fallback: {public_url}")
            return _ingest_public(
                page_id=page_id,
                team=team,
                hub_id=hub_id,
                url=public_url,
                embedder=embedder,
                store=store,
            )
        logger.error(f"[{team}] 메타데이터 조회 실패 ({page_id}): {e}")
        return {"page_id": page_id, "chunks": 0, "status": "error", "reason": str(e)}

    # 증분 처리: last_edited_time이 동일하면 skip
    if existing_last_edited and meta.last_edited_time == existing_last_edited:
        logger.debug(f"[{team}] skip (변경 없음): {meta.title}")
        return {"page_id": page_id, "chunks": 0, "status": "skip", "reason": "not modified"}

    breadcrumb = f"{team} > {meta.title}"
    meta.breadcrumb = [team, meta.title]

    logger.info(f"[{team}] 수집 시작: {meta.title} ({page_id})")

    # 2. markdown 수집
    try:
        markdown = get_page_markdown(page_id, notion_client)
    except Exception as e:
        logger.error(f"[{team}] markdown 수집 실패 ({page_id}): {e}")
        return {"page_id": page_id, "chunks": 0, "status": "error", "reason": str(e)}

    if not markdown.strip():
        logger.warning(f"[{team}] 본문 없음, skip ({page_id})")
        return {"page_id": page_id, "chunks": 0, "status": "skip", "reason": "empty content"}

    return _ingest_markdown(
        page_id=meta.page_id,
        team=team,
        hub_id=hub_id,
        title=meta.title,
        url=meta.url,
        last_edited_time=meta.last_edited_time,
        breadcrumb=breadcrumb,
        markdown=markdown,
        embedder=embedder,
        store=store,
    )


def _ingest_inline(
    page_id: str,
    team: str,
    hub_id: str,
    title: str,
    markdown: str,
    embedder: EmbeddingClient,
    store: QdrantStore,
) -> dict:
    """Hub 토글 인라인 텍스트 임베딩"""
    if not markdown.strip():
        return {"page_id": page_id, "chunks": 0, "status": "skip", "reason": "empty inline content"}

    logger.info(f"[{team}] 인라인 콘텐츠 수집 시작: {title}")

    return _ingest_markdown(
        page_id=page_id,
        team=team,
        hub_id=hub_id,
        title=title,
        url="",
        last_edited_time="",
        breadcrumb=f"{team} > {title}",
        markdown=markdown,
        embedder=embedder,
        store=store,
    )


def _ingest_markdown(
    page_id: str,
    team: str,
    hub_id: str,
    title: str,
    url: str,
    last_edited_time: str,
    breadcrumb: str,
    markdown: str,
    embedder: EmbeddingClient,
    store: QdrantStore,
) -> dict:
    """markdown → chunk → embedding → upsert 공통 처리"""

    # chunking
    chunks = chunk_markdown(
        markdown=markdown,
        page_id=page_id,
        page_title=title,
        breadcrumb=breadcrumb,
        page_url=url,
    )

    if not chunks:
        logger.warning(f"[{team}] chunk 없음, skip ({page_id})")
        return {"page_id": page_id, "chunks": 0, "status": "skip", "reason": "no chunks"}

    # 기존 chunk 삭제 (멱등성)
    store.delete_by_page_id(page_id)

    # embedding: 제목을 본문 앞에 붙여 임베딩 (검색 정확도 향상)
    # 저장되는 text는 원본 그대로 유지
    embed_texts = [f"{title}\n{c.text}" if title else c.text for c in chunks]
    vectors = embedder.embed_batch(embed_texts)

    # payload 구성
    payloads = []
    point_ids = []

    for chunk, vector in zip(chunks, vectors):
        content_hash = sha256(chunk.text)
        point_id = make_point_id(hub_id, page_id, chunk.chunk_index, content_hash)

        payloads.append({
            "source": "notion",
            "hub_id": hub_id,
            "team": team,
            "status": "active",
            "page_id": page_id,
            "page_url": url,
            "page_title": title,
            "breadcrumb": breadcrumb,
            "section_path": chunk.section_path,
            "chunk_index": chunk.chunk_index,
            "last_edited_time": last_edited_time,
            "content_sha256": content_hash,
            "text": chunk.text,
        })
        point_ids.append(point_id)

    count = store.upsert_chunks(payloads, vectors, point_ids)
    logger.info(f"[{team}] '{title}': {count}개 chunk 적재 완료")

    return {"page_id": page_id, "page_title": title, "chunks": count, "status": "ok"}


def _ingest_public(
    page_id: str,
    team: str,
    hub_id: str,
    url: str,
    embedder: EmbeddingClient,
    store: QdrantStore,
) -> dict:
    """notion.site 공개 페이지 Playwright 스크래핑 후 적재"""
    from app.notion.public_scraper import scrape_notion_public_page

    try:
        scraped = scrape_notion_public_page(url)
    except Exception as e:
        logger.error(f"[{team}] 공개 페이지 스크래핑 실패 ({url}): {e}")
        return {"page_id": page_id, "chunks": 0, "status": "error", "reason": str(e)}

    if not scraped["text"].strip():
        logger.warning(f"[{team}] 공개 페이지 본문 없음, skip ({url})")
        return {"page_id": page_id, "chunks": 0, "status": "skip", "reason": "empty content"}

    return _ingest_markdown(
        page_id=page_id,
        team=team,
        hub_id=hub_id,
        title=scraped["title"],
        url=url,
        last_edited_time="",
        breadcrumb=f"{team} > {scraped['title']}",
        markdown=scraped["text"],
        embedder=embedder,
        store=store,
    )
