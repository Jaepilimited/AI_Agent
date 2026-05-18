"""Notion → Local JSON 증분 동기화 파이프라인 (v3.0).

DB-HUB를 단일 진입점으로 사용:
  - DB-HUB(2e12b4283b008011ae32e39bf73b7f7b) 팀 토글 재귀 탐색
  - 로컬 JSON(notion_vectors_gemini.json)과 last_edited_time 비교
  - 변경/신규 페이지만 Gemini 임베딩 → 로컬 JSON 직접 업데이트
  - qdrant_agent 인메모리 hot-reload

Usage:
  python -X utf8 scripts/notion_qdrant_pipeline.py            # 증분 sync
  python -X utf8 scripts/notion_qdrant_pipeline.py --full     # 전체 재동기화
  python -X utf8 scripts/notion_qdrant_pipeline.py --status   # 현황 출력
  python -X utf8 scripts/notion_qdrant_pipeline.py --upload-cloud  # Qdrant Cloud 백업
"""

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

NOTION_TOKEN = os.getenv("NOTION_MCP_TOKEN", "")
NOTION_VERSION = "2022-06-28"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

DB_HUB_ID = "2e12b4283b008011ae32e39bf73b7f7b"
EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIM = 1536
LOCAL_JSON = _ROOT / "data" / "notion_vectors_gemini.json"

QDRANT_URL = "https://bf41bcbe-af68-416f-9d26-1b3d64f7bed0.us-east-1-1.aws.cloud.qdrant.io:6333"
QDRANT_API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2Nlc3MiOiJtIiwic3ViamVjdCI6ImFwaS1rZXk6OTFkOGVkZWYtNTFkNi00ODNhLTg0MDItZTdjNjI0ZjA2NThmIn0.K0zdMdpnbIMl_yfXV8EJfcClpPnkoPa_SS_XbDI1kv4"
COLLECTION = "Craver"


# ── Headers ──

def notion_headers() -> dict:
    return {"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": NOTION_VERSION}


def qdrant_headers() -> dict:
    return {"api-key": QDRANT_API_KEY, "Content-Type": "application/json"}


# ── DB-HUB 재귀 탐색 ──

def crawl_hub(client: httpx.Client) -> list[dict]:
    """DB-HUB 팀 토글을 재귀 탐색해 {page_id, title, team} 목록 반환."""
    hub_blocks = _get_block_children(DB_HUB_ID, client)
    pages = []
    for block in hub_blocks:
        if block.get("type") == "toggle":
            team = _rich_text(block["toggle"])
            # 학습 현황 자동 삽입 블록은 수집 제외 (🤖 AI 학습 현황 marker)
            if team.startswith("🤖 AI 학습 현황"):
                continue
            collected_before = len(pages)
            _collect_from_block(block["id"], team, client, pages)
            # 멘션/child_page가 없고 텍스트만 있는 토글(예: Craver 회사소개)은
            # 토글 블록 자체를 페이지로 등록해 내용 추출
            if len(pages) == collected_before:
                pages.append({"page_id": block["id"], "title": team, "team": team, "is_block": True})
    return pages


def _get_block_children(block_id: str, client: httpx.Client) -> list[dict]:
    resp = client.get(
        f"https://api.notion.com/v1/blocks/{block_id}/children?page_size=100",
        headers=notion_headers(),
    )
    if resp.status_code != 200:
        return []
    return resp.json().get("results", [])


def _rich_text(content: dict) -> str:
    return "".join(t.get("plain_text", "") for t in content.get("rich_text", []))


def _collect_from_block(block_id: str, team: str, client: httpx.Client, pages: list):
    """토글/paragraph/bulleted_list_item 블록에서 child_page, child_database, mention_page 수집."""
    children = _get_block_children(block_id, client)
    for b in children:
        btype = b.get("type", "")
        bid = b.get("id", "")

        if btype == "child_page":
            title = b.get("child_page", {}).get("title", "")
            pages.append({"page_id": bid, "title": title, "team": team})

        elif btype == "child_database":
            title = b.get("child_database", {}).get("title", "")
            pages.append({"page_id": bid, "title": title, "team": team, "is_database": True})

        elif btype == "toggle":
            sub_team = _rich_text(b["toggle"]) or team
            _collect_from_block(bid, sub_team, client, pages)

        elif btype in ("paragraph", "bulleted_list_item", "numbered_list_item"):
            rich = b.get(btype, {}).get("rich_text", [])
            for chunk in rich:
                if chunk.get("type") == "mention":
                    m = chunk.get("mention", {})
                    if m.get("type") == "page":
                        pid = m["page"]["id"]
                        text = chunk.get("plain_text", "")
                        pages.append({"page_id": pid, "title": text, "team": team})
                    elif m.get("type") == "database":
                        did = m["database"]["id"]
                        text = chunk.get("plain_text", "")
                        pages.append({"page_id": did, "title": text, "team": team, "is_database": True})
            # 중첩 멘션(list item 하위 paragraph 등) 재귀 탐색
            if b.get("has_children"):
                _collect_from_block(bid, team, client, pages)


# ── Notion 페이지 조회 ──

def fetch_page_meta(page_id: str, client: httpx.Client) -> dict | None:
    """Notion 페이지 메타 조회. 404면 None 반환."""
    resp = client.get(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=notion_headers(),
    )
    if resp.status_code != 200:
        return None
    pdata = resp.json()
    title = ""
    for prop in pdata.get("properties", {}).values():
        if prop.get("type") == "title":
            title = "".join(t.get("plain_text", "") for t in prop.get("title", []))
            break
    return {
        "page_id": page_id,
        "title": title or pdata.get("url", "")[-8:],
        "url": pdata.get("url", ""),
        "last_edited_time": pdata.get("last_edited_time", ""),
    }


def fetch_database_meta(db_id: str, client: httpx.Client) -> dict | None:
    """Notion 데이터베이스 메타 조회. 404면 None 반환."""
    resp = client.get(
        f"https://api.notion.com/v1/databases/{db_id}",
        headers=notion_headers(),
    )
    if resp.status_code != 200:
        return None
    data = resp.json()
    title = "".join(t.get("plain_text", "") for t in data.get("title", []))
    return {
        "page_id": db_id,
        "title": title or db_id[-8:],
        "url": data.get("url", ""),
        "last_edited_time": data.get("last_edited_time", ""),
    }


def fetch_page_text(page_id: str, client: httpx.Client, _depth: int = 0) -> str:
    """Notion 페이지 블록 텍스트 추출 (페이지네이션 + child_database + table_row + 재귀)."""
    if _depth > 3:
        return ""
    texts: list[str] = []
    url = f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100"
    while url:
        resp = client.get(url, headers=notion_headers())
        if resp.status_code != 200:
            break
        data = resp.json()
        for b in data.get("results", []):
            btype = b.get("type", "")
            bid = b.get("id", "")

            if btype == "child_database":
                # 인라인 DB: 항목 제목+속성 텍스트 추출
                _extract_database_text(bid, client, texts)
                continue

            if btype == "table":
                # 테이블: 행 셀 내용 추출
                _extract_table_text(bid, client, texts)
                continue

            # 일반 블록: rich_text 추출
            rt = b.get(btype, {}).get("rich_text", [])
            text = "".join(t.get("plain_text", "") for t in rt)
            if text.strip():
                texts.append(text.strip())

            # has_children인 블록(toggle, callout 등) 재귀
            if b.get("has_children") and btype not in ("child_page", "child_database", "table"):
                sub = fetch_page_text(bid, client, _depth + 1)
                if sub:
                    texts.append(sub)

        # 페이지네이션
        if data.get("has_more") and data.get("next_cursor"):
            cursor = data["next_cursor"]
            url = f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100&start_cursor={cursor}"
        else:
            url = None

    return "\n".join(texts)


def _extract_database_text(db_id: str, client: httpx.Client, texts: list[str]) -> None:
    """child_database 내 항목 텍스트를 texts에 추가."""
    cursor = None
    while True:
        body: dict = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        resp = client.post(
            f"https://api.notion.com/v1/databases/{db_id}/query",
            headers=notion_headers(), json=body,
        )
        if resp.status_code != 200:
            break
        data = resp.json()
        for item in data.get("results", []):
            parts: list[str] = []
            for prop_name, prop in item.get("properties", {}).items():
                ptype = prop.get("type", "")
                val = ""
                if ptype == "title":
                    val = "".join(t.get("plain_text", "") for t in prop.get("title", []))
                elif ptype == "rich_text":
                    val = "".join(t.get("plain_text", "") for t in prop.get("rich_text", []))
                elif ptype in ("select", "status"):
                    val = (prop.get(ptype) or {}).get("name", "")
                elif ptype == "number":
                    v = prop.get("number")
                    val = str(v) if v is not None else ""
                elif ptype == "url":
                    val = prop.get("url") or ""
                if val:
                    parts.append(f"{prop_name}: {val}")
            if parts:
                texts.append(" | ".join(parts))
        if data.get("has_more") and data.get("next_cursor"):
            cursor = data["next_cursor"]
        else:
            break


def _extract_table_text(table_id: str, client: httpx.Client, texts: list[str]) -> None:
    """table 블록의 행 셀 텍스트를 texts에 추가."""
    resp = client.get(
        f"https://api.notion.com/v1/blocks/{table_id}/children?page_size=100",
        headers=notion_headers(),
    )
    if resp.status_code != 200:
        return
    for row in resp.json().get("results", []):
        cells = row.get("table_row", {}).get("cells", [])
        cell_texts = []
        for cell in cells:
            cell_text = "".join(t.get("plain_text", "") for t in cell)
            if cell_text.strip():
                cell_texts.append(cell_text.strip())
        if cell_texts:
            texts.append(" | ".join(cell_texts))


# ── 청킹 · 임베딩 ──

def chunk_text(text: str, max_size: int = 800, overlap: int = 100) -> list[str]:
    if len(text) <= max_size:
        return [text] if text.strip() else []
    chunks = []
    start = 0
    while start < len(text):
        end = start + max_size
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
    from google import genai
    client = genai.Client(api_key=GEMINI_API_KEY)
    all_embeddings = []
    for i in range(0, len(texts), 50):
        batch = [t[:8000] for t in texts[i:i + 50]]
        result = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=batch,
            config={"output_dimensionality": EMBEDDING_DIM},
        )
        all_embeddings.extend([e.values for e in result.embeddings])
        time.sleep(0.3)
    return all_embeddings


# ── 로컬 JSON 관리 ──

def get_local_page_map() -> dict[str, str]:
    """로컬 JSON → {page_id: last_edited_time}"""
    if not LOCAL_JSON.exists():
        return {}
    with open(LOCAL_JSON, "r", encoding="utf-8") as f:
        raw = json.load(f)
    page_map: dict[str, str] = {}
    for pt in raw:
        payload = pt.get("payload", {})
        pid = payload.get("page_id")
        edited = payload.get("last_edited_time", "")
        if pid and pid not in page_map:
            page_map[pid] = edited
    print(f"  로컬 JSON: {len(raw)} 포인트, {len(page_map)} 페이지")
    return page_map


def update_local_json(new_page_vectors: dict[str, list[dict]], removed_page_ids: set[str]) -> int:
    existing: list[dict] = []
    if LOCAL_JSON.exists():
        with open(LOCAL_JSON, "r", encoding="utf-8") as f:
            existing = json.load(f)
    affected = set(new_page_vectors.keys()) | removed_page_ids
    kept = [pt for pt in existing if pt.get("payload", {}).get("page_id") not in affected]
    added = [pt for pts in new_page_vectors.values() for pt in pts]
    result = kept + added
    LOCAL_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCAL_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    size_mb = LOCAL_JSON.stat().st_size / 1024 / 1024
    print(f"  로컬 JSON 저장: {len(result)} 포인트 ({size_mb:.1f} MB)")
    return len(result)


# ── 핵심 파이프라인 ──

def run_incremental_sync(full: bool = False) -> dict:
    print(f"\n=== Notion → Local JSON {'전체' if full else '증분'} 동기화 (DB-HUB 기준) ===")
    t0 = time.time()
    stats = {"new": 0, "updated": 0, "deleted": 0, "unchanged": 0, "errors": 0, "skipped_404": 0}

    local_page_map: dict[str, str] = {} if full else get_local_page_map()

    with httpx.Client(timeout=20) as client:
        print(f"\n  DB-HUB 탐색 중...")
        hub_pages = crawl_hub(client)

        # page_id 중복 제거
        seen_ids: set[str] = set()
        unique_pages = []
        for p in hub_pages:
            pid = p["page_id"]
            if pid not in seen_ids:
                seen_ids.add(pid)
                unique_pages.append(p)
        print(f"  HUB 등록 페이지: {len(unique_pages)}개 (중복 제거 후)")

        notion_page_ids: set[str] = set()
        new_page_vectors: dict[str, list[dict]] = {}

        for entry in unique_pages:
            page_id = entry["page_id"]
            team = entry["team"]

            # is_block: True → Notion 블록(토글 등)이라 /pages/ 대신 직접 텍스트 추출
            if entry.get("is_block"):
                notion_page_ids.add(page_id)
                local_edited = local_page_map.get(page_id)
                is_new = local_edited is None
                meta = {
                    "page_id": page_id,
                    "title": entry["title"],
                    "url": f"https://www.notion.so/skin1004/{DB_HUB_ID.replace('-', '')}",
                    "last_edited_time": "block",
                }
                if not full and not is_new:
                    stats["unchanged"] += 1
                    continue

            elif entry.get("is_database"):
                meta = fetch_database_meta(page_id, client)
                if meta is None:
                    print(f"  SKIP(404): [{team}] {entry['title'][:50]}")
                    stats["skipped_404"] += 1
                    continue

                notion_page_ids.add(page_id)
                local_edited = local_page_map.get(page_id)
                is_new = local_edited is None

                if not full and not is_new and meta["last_edited_time"] == local_edited:
                    stats["unchanged"] += 1
                    continue

            else:
                meta = fetch_page_meta(page_id, client)
                if meta is None:
                    print(f"  SKIP(404): [{team}] {entry['title'][:50]}")
                    stats["skipped_404"] += 1
                    continue

                notion_page_ids.add(page_id)
                local_edited = local_page_map.get(page_id)
                is_new = local_edited is None

                if not full and not is_new and meta["last_edited_time"] == local_edited:
                    stats["unchanged"] += 1
                    continue

            try:
                if entry.get("is_database"):
                    db_texts: list[str] = []
                    _extract_database_text(page_id, client, db_texts)
                    text = "\n".join(db_texts)
                else:
                    text = fetch_page_text(page_id, client)
                if not text.strip():
                    print(f"  EMPTY: [{team}] {meta['title'][:50]}")
                    continue

                raw_chunks = chunk_text(text)
                if not raw_chunks:
                    continue

                chunk_objs = [
                    {"idx": i, "text": c, "sha256": hashlib.sha256(c.encode()).hexdigest()}
                    for i, c in enumerate(raw_chunks)
                ]
                embeddings = embed_texts([c["text"] for c in chunk_objs])
                points = []
                for c, emb in zip(chunk_objs, embeddings):
                    chunk_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{page_id}:{c['idx']}"))
                    points.append({
                        "id": chunk_id,
                        "vector": emb,
                        "payload": {
                            "source": f"{team}-hub",
                            "team": team,
                            "page_id": page_id,
                            "page_title": meta["title"],
                            "page_url": meta["url"],
                            "breadcrumb": f"{team} > {meta['title']}",
                            "chunk_index": c["idx"],
                            "last_edited_time": meta["last_edited_time"],
                            "content_sha256": c["sha256"],
                            "text": c["text"],
                        },
                    })
                new_page_vectors[page_id] = points
                label = "NEW" if is_new else "UPDATED"
                print(f"  {label}: [{team}] {meta['title'][:50]} ({len(points)} chunks)")
                if is_new:
                    stats["new"] += 1
                else:
                    stats["updated"] += 1

            except Exception as e:
                print(f"  ERROR: [{team}] {entry['title'][:40]}: {e}")
                stats["errors"] += 1

    # 로컬에만 있고 HUB에서 사라진 페이지 제거
    if not full:
        removed = set(local_page_map.keys()) - notion_page_ids
        stats["deleted"] = len(removed)
        for pid in removed:
            print(f"  DELETED: {pid}")
    else:
        removed = set()

    if new_page_vectors or removed:
        update_local_json(new_page_vectors, removed)
    else:
        print("\n  변경 없음 — 로컬 JSON 유지")

    elapsed = time.time() - t0
    print(f"\n  동기화 완료: {elapsed:.0f}초")
    print(f"  신규={stats['new']}, 업데이트={stats['updated']}, 삭제={stats['deleted']}, "
          f"변동없음={stats['unchanged']}, 404스킵={stats['skipped_404']}, 오류={stats['errors']}")
    return stats


def run_pipeline(full: bool = False) -> dict:
    """전체 파이프라인: 증분 sync + hot-reload + Notion 학습 현황 업데이트."""
    stats = run_incremental_sync(full=full)
    try:
        from app.agents.qdrant_agent import reload_vectors
        reload_vectors()
        print("  Qdrant Cloud 업로드 및 hot-reload 완료")
        stats["reloaded"] = True
    except Exception as e:
        print(f"  Qdrant 업로드 실패 (무시): {e}")
        stats["reloaded"] = False

    # Notion DB-HUB 학습 현황 업데이트
    try:
        from scripts.update_notion_learning_status import main as update_status
        print("\n  Notion 학습 현황 업데이트 중...")
        update_status()
    except Exception as e:
        print(f"  학습 현황 업데이트 실패 (무시): {e}")

    return stats


# ── Qdrant Cloud 백업 (선택) ──

def upload_to_qdrant_cloud() -> int:
    """로컬 JSON → Qdrant Cloud 전체 업로드 (백업용)."""
    if not LOCAL_JSON.exists():
        print("  [ERROR] 로컬 JSON 없음")
        return 0
    with open(LOCAL_JSON, "r", encoding="utf-8") as f:
        all_points = json.load(f)
    print(f"  Qdrant Cloud 업로드: {len(all_points)} 포인트...")
    with httpx.Client(timeout=30) as client:
        # 컬렉션 없으면 생성, 있으면 기존 데이터 보존
        chk = client.get(f"{QDRANT_URL}/collections/{COLLECTION}", headers=qdrant_headers())
        if chk.status_code != 200:
            client.put(
                f"{QDRANT_URL}/collections/{COLLECTION}",
                headers=qdrant_headers(),
                json={"vectors": {"size": EMBEDDING_DIM, "distance": "Cosine"}},
            )
    with httpx.Client(timeout=60) as client:
        for i in range(0, len(all_points), 100):
            batch = all_points[i:i + 100]
            resp = client.put(
                f"{QDRANT_URL}/collections/{COLLECTION}/points",
                headers=qdrant_headers(),
                json={"points": batch},
            )
            if resp.status_code != 200:
                print(f"  [ERROR] 업로드 {i}: {resp.status_code}")
    print(f"  Qdrant Cloud 업로드 완료: {len(all_points)} 포인트")
    return len(all_points)


# ── CLI ──

def main():
    parser = argparse.ArgumentParser(description="Notion DB-HUB → Local JSON 증분 동기화")
    parser.add_argument("--full",         action="store_true", help="전체 재동기화 (로컬 JSON 기준 무시)")
    parser.add_argument("--upload-cloud", action="store_true", help="로컬 JSON → Qdrant Cloud 업로드")
    parser.add_argument("--status",       action="store_true", help="현황 출력")
    args = parser.parse_args()

    if args.status:
        print(f"\n  로컬 JSON: ", end="")
        if LOCAL_JSON.exists():
            size_mb = LOCAL_JSON.stat().st_size / 1024 / 1024
            page_map = get_local_page_map()
            print(f"{size_mb:.1f} MB, {len(page_map)} 페이지")
        else:
            print("없음")
        return

    if args.upload_cloud:
        upload_to_qdrant_cloud()
        return

    run_pipeline(full=args.full)


if __name__ == "__main__":
    main()
