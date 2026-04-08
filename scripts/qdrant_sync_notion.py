"""Qdrant Notion 동기화 — GitHub qdrant_db 타겟 기반.

Notion DB 크롤링 → Gemini embedding-001 (1536dim) → Qdrant Cloud upsert.
notion_hub_gemini 컬렉션에 팀별 데이터를 동기화합니다.

Usage:
  python -X utf8 scripts/qdrant_sync_notion.py             # 전체 동기화
  python -X utf8 scripts/qdrant_sync_notion.py --list       # 소스 목록 확인
  python -X utf8 scripts/qdrant_sync_notion.py --source DB  # 특정 소스만
"""
import argparse
import asyncio
import hashlib
import json
import sys
import time
import uuid
from typing import Optional

import httpx

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import os
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

class _Settings:
    notion_mcp_token = os.getenv("NOTION_MCP_TOKEN", "")
    gemini_api_key = os.getenv("GEMINI_API_KEY", "")

settings = _Settings()

# ── Qdrant Config ──
QDRANT_URL = "https://bf41bcbe-af68-416f-9d26-1b3d64f7bed0.us-east-1-1.aws.cloud.qdrant.io:6333"
QDRANT_API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2Nlc3MiOiJtIiwic3ViamVjdCI6ImFwaS1rZXk6OTFkOGVkZWYtNTFkNi00ODNhLTg0MDItZTdjNjI0ZjA2NThmIn0.K0zdMdpnbIMl_yfXV8EJfcClpPnkoPa_SS_XbDI1kv4"
COLLECTION = "notion_hub_gemini"
EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIM = 1536

# ── Notion Config ──
NOTION_TOKEN = settings.notion_mcp_token
NOTION_VERSION = "2022-06-28"

# ── 수집 대상 (qdrant_db/reload_data.py 기반) ──
DATABASE_TARGETS = [
    # DB팀
    {"source": "DB-JP", "database_id": "d86180c9236541d6b154dcb4c4143f23", "team": "DB", "desc": "DB 재필님 개인 페이지"},
    {"source": "DB-tablet", "database_id": "2532b4283b0080eba96ce35ae8ba8743", "team": "DB", "desc": "법인 태블릿 사용법"},
    {"source": "DB-SY", "database_id": "12f2b4283b0080cbaf9fe103d7c91490", "team": "DB", "desc": "DB 소영님 개인 페이지"},
    {"source": "DB-da-part", "database_id": "1602b4283b0080f186cfc6425d9a53dd", "team": "DB", "desc": "데이터 분석 파트"},
    {"source": "DB-ads-input", "database_id": "1dc2b4283b0080cb8790cf5218896ebd", "team": "DB", "desc": "Daily 광고 데이터 입력"},
    # EAST
    {"source": "EAST-guide-archive", "database_id": "2e62b4283b00803a8007df0d3003705c", "team": "[GM]EAST", "desc": "EAST 가이드 아카이브"},
    {"source": "EAST-2026-work", "database_id": "2e12b4283b0080b48a1dd7bbbd6e0e53", "team": "[GM]EAST", "desc": "EAST 2026 업무파악"},
    {"source": "EAST-tiktok-access", "database_id": "19d2b4283b0080dc89d9e6d9c11ec1e5", "team": "[GM]EAST", "desc": "틱톡샵 접속 방법"},
    {"source": "EAST-travel-guide", "database_id": "1982b4283b008039ad79ec0c1c1e38fb", "team": "[GM]EAST", "desc": "해외 출장 가이드북"},
    # WEST
    {"source": "WEST-tiktok-dashboard", "database_id": "22e2b4283b008060bac6cef042c3787b", "team": "[GM]WEST", "desc": "틱톡샵US 대시보드"},
    # KBT
    {"source": "KBT-smartstore-guide", "database_id": "c058d9e89e8a4780b32e866b8248b5b1", "team": "KBT", "desc": "스마트스토어 운영방법"},
    {"source": "KBT-smartstore-work", "database_id": "1fb2b4283b00802883faef2df97c6f73", "team": "KBT", "desc": "네이버 스마트스토어 업무"},
    # GM 광고
    {"source": "GM-ads-meeting", "database_id": "3032b4283b00801188e1f65eb0d46fae", "team": "[GM]WEST", "desc": "GM 광고 인사이트 미팅"},
    # B2B
    {"source": "B2B-guide", "database_id": "07d3489594fa4db6829d1fee397ecdf1", "team": "B2B2", "desc": "B2B 신규 입사자 안내"},
]


def notion_headers():
    return {"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": NOTION_VERSION}


def qdrant_headers():
    return {"api-key": QDRANT_API_KEY, "Content-Type": "application/json"}


def crawl_database_pages(database_id: str) -> list[dict]:
    """Notion DB의 모든 페이지 ID + 제목 목록 가져오기."""
    pages = []
    url = f"https://api.notion.com/v1/databases/{database_id}/query"
    has_more = True
    start_cursor = None

    with httpx.Client(timeout=15) as client:
        while has_more:
            body = {"page_size": 100}
            if start_cursor:
                body["start_cursor"] = start_cursor
            resp = client.post(url, headers=notion_headers(), json=body)
            if resp.status_code != 200:
                print(f"    [WARN] DB query failed: {resp.status_code}")
                break
            data = resp.json()
            for page in data.get("results", []):
                title = ""
                for prop in page.get("properties", {}).values():
                    if prop.get("type") == "title":
                        title = "".join(t.get("plain_text", "") for t in prop.get("title", []))
                        break
                pages.append({
                    "page_id": page["id"],
                    "title": title or page["id"][:8],
                    "url": page.get("url", ""),
                    "last_edited": page.get("last_edited_time", ""),
                })
            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")

    return pages


def crawl_page_blocks(page_id: str) -> str:
    """페이지의 블록 콘텐츠를 텍스트로 추출."""
    texts = []
    url = f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100"

    with httpx.Client(timeout=15) as client:
        resp = client.get(url, headers=notion_headers())
        if resp.status_code != 200:
            return ""
        blocks = resp.json().get("results", [])
        for b in blocks:
            btype = b.get("type", "")
            block_data = b.get(btype, {})
            rich_text = block_data.get("rich_text", [])
            text = "".join(t.get("plain_text", "") for t in rich_text)
            if text.strip():
                texts.append(text.strip())

    return "\n".join(texts)


def chunk_text(text: str, max_size: int = 800, overlap: int = 100) -> list[str]:
    """텍스트를 청크로 분할."""
    if len(text) <= max_size:
        return [text] if text.strip() else []

    chunks = []
    start = 0
    while start < len(text):
        end = start + max_size
        # 문장 경계에서 자르기
        if end < len(text):
            for sep in ["\n\n", "\n", ". ", "。", "! ", "? "]:
                idx = text.rfind(sep, start + max_size // 2, end)
                if idx > start:
                    end = idx + len(sep)
                    break
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap

    return chunks


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Gemini embedding-001로 임베딩."""
    from google import genai
    client = genai.Client(api_key=settings.gemini_api_key)

    all_embeddings = []
    BATCH = 50
    for i in range(0, len(texts), BATCH):
        batch = texts[i:i+BATCH]
        batch = [t[:8000] for t in batch]  # Gemini limit
        result = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=batch,
            config={"output_dimensionality": EMBEDDING_DIM},
        )
        all_embeddings.extend([e.values for e in result.embeddings])
        time.sleep(0.5)

    return all_embeddings


def upsert_to_qdrant(points: list[dict]):
    """Qdrant에 upsert."""
    BATCH = 100
    with httpx.Client(timeout=30) as client:
        for i in range(0, len(points), BATCH):
            batch = points[i:i+BATCH]
            resp = client.put(
                f"{QDRANT_URL}/collections/{COLLECTION}/points",
                headers=qdrant_headers(),
                json={"points": batch},
            )
            if resp.status_code != 200:
                print(f"    [ERROR] Qdrant upsert: {resp.status_code}")


def delete_source_points(source: str):
    """특정 source의 포인트 삭제."""
    with httpx.Client(timeout=15) as client:
        resp = client.post(
            f"{QDRANT_URL}/collections/{COLLECTION}/points/delete",
            headers=qdrant_headers(),
            json={"filter": {"must": [{"key": "source", "match": {"value": source}}]}},
        )
        return resp.status_code == 200


def get_source_count(source: str) -> int:
    """특정 source의 포인트 수."""
    with httpx.Client(timeout=10) as client:
        resp = client.post(
            f"{QDRANT_URL}/collections/{COLLECTION}/points/count",
            headers=qdrant_headers(),
            json={"filter": {"must": [{"key": "source", "match": {"value": source}}]}},
        )
        if resp.status_code == 200:
            return resp.json().get("result", {}).get("count", 0)
    return 0


def sync_source(target: dict):
    """단일 소스 동기화: 크롤링 → 청킹 → 임베딩 → Qdrant."""
    source = target["source"]
    db_id = target["database_id"]
    team = target["team"]
    desc = target["desc"]

    print(f"\n  [{source}] {desc} (team: {team})")

    # 1. Notion DB에서 페이지 목록 (실패 시 단일 페이지로 fallback)
    pages = crawl_database_pages(db_id)
    if not pages:
        # DB가 아니라 page일 수 있음 — 단일 페이지로 시도
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.get(f"https://api.notion.com/v1/pages/{db_id}", headers=notion_headers())
                if resp.status_code == 200:
                    pdata = resp.json()
                    title = ""
                    for prop in pdata.get("properties", {}).values():
                        if prop.get("type") == "title":
                            title = "".join(t.get("plain_text", "") for t in prop.get("title", []))
                    pages = [{"page_id": db_id, "title": title or source, "url": pdata.get("url", ""), "last_edited": pdata.get("last_edited_time", "")}]
        except:
            pass
    print(f"    Pages: {len(pages)}")
    if not pages:
        return 0

    # 2. 페이지별 콘텐츠 크롤링 + 청킹
    all_chunks = []
    for page in pages:
        text = crawl_page_blocks(page["page_id"])
        if not text.strip():
            continue
        chunks = chunk_text(text)
        for idx, chunk in enumerate(chunks):
            chunk_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{page['page_id']}:{idx}"))
            all_chunks.append({
                "id": chunk_id,
                "text": chunk,
                "payload": {
                    "source": source,
                    "team": team,
                    "page_id": page["page_id"],
                    "page_title": page["title"],
                    "page_url": page["url"],
                    "breadcrumb": f"{team} > {page['title']}",
                    "chunk_index": idx,
                    "last_edited_time": page["last_edited"],
                    "content_sha256": hashlib.sha256(chunk.encode()).hexdigest(),
                    "text": chunk,
                },
            })

    print(f"    Chunks: {len(all_chunks)}")
    if not all_chunks:
        return 0

    # 3. 임베딩
    texts = [c["text"] for c in all_chunks]
    embeddings = embed_texts(texts)
    print(f"    Embedded: {len(embeddings)}")

    # 4. 기존 소스 삭제 + 새 데이터 upsert
    delete_source_points(source)
    points = []
    for c, emb in zip(all_chunks, embeddings):
        points.append({
            "id": c["id"],
            "vector": emb,
            "payload": c["payload"],
        })
    upsert_to_qdrant(points)
    print(f"    Upserted: {len(points)}")

    return len(points)


def main():
    parser = argparse.ArgumentParser(description="Qdrant Notion 동기화")
    parser.add_argument("--list", action="store_true", help="소스 목록 + 현재 포인트 수")
    parser.add_argument("--source", type=str, help="특정 소스만 동기화 (prefix 매칭)")
    parser.add_argument("--all", action="store_true", help="전체 동기화")
    args = parser.parse_args()

    if not args.list and not args.source and not args.all:
        args.all = True  # 기본: 전체 동기화

    if args.list:
        print(f"\n{'Source':25s} {'Team':12s} {'Points':>7s}  Description")
        print("-" * 80)
        for t in DATABASE_TARGETS:
            count = get_source_count(t["source"])
            print(f"  {t['source']:23s} {t['team']:12s} {count:>5d}    {t['desc']}")
        # Total
        with httpx.Client(timeout=10) as client:
            resp = client.get(f"{QDRANT_URL}/collections/{COLLECTION}", headers=qdrant_headers())
            total = resp.json().get("result", {}).get("points_count", 0)
        print(f"\n  Total: {total} points in {COLLECTION}")
        return

    targets = DATABASE_TARGETS
    if args.source:
        targets = [t for t in DATABASE_TARGETS if args.source.lower() in t["source"].lower()]
        if not targets:
            print(f"[ERROR] '{args.source}' 매칭 소스 없음")
            return

    print(f"\n=== Qdrant Notion 동기화 ({len(targets)} 소스) ===")
    total_points = 0
    t0 = time.time()

    for target in targets:
        try:
            n = sync_source(target)
            total_points += n
        except Exception as e:
            print(f"    [ERROR] {target['source']}: {e}")

    elapsed = time.time() - t0
    print(f"\n=== 완료: {total_points} chunks, {elapsed:.0f}초 ===")


if __name__ == "__main__":
    main()
