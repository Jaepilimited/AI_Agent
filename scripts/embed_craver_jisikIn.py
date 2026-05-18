"""CRAVER 지식in 페이지 전체 내용 추출 → Gemini 임베딩 → Qdrant upsert.

Notion 통합 권한 없이 내부 API로 직접 접근.
"""
import httpx, json, time, uuid, os, asyncio
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

NOTION_BASE = "https://www.notion.so/api/v3"
NOTION_HEADERS = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}

PAGE_ID  = "7cecb6b5-3fac-43c8-8812-7729c29b7436"
PAGE_URL = "https://www.notion.so/7cecb6b53fac43c888127729c29b7436"
PAGE_TITLE = "CRAVER 지식in"
TEAM = "PEOPLE"

LOCAL_JSON = Path(__file__).parent.parent / "data" / "notion_vectors_gemini.json"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
QDRANT_URL = os.getenv("QDRANT_URL", "https://bf41bcbe-af68-416f-9d26-1b3d64f7bed0.us-east-1-1.aws.cloud.qdrant.io:6333")
QDRANT_KEY = os.getenv("QDRANT_API_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2Nlc3MiOiJtIiwic3ViamVjdCI6ImFwaS1rZXk6OTFkOGVkZWYtNTFkNi00ODNhLTg0MDItZTdjNjI0ZjA2NThmIn0.K0zdMdpnbIMl_yfXV8EJfcClpPnkoPa_SS_XbDI1kv4")
COLLECTION = "Craver"
EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIM = 1536


# ── Step 1: Fetch all blocks via Notion internal API ─────────────────────────

def fetch_all_blocks():
    resp = httpx.post(f"{NOTION_BASE}/loadPageChunk",
        json={"pageId": PAGE_ID, "limit": 200, "cursor": {"stack": []}, "chunkNumber": 0, "verticalColumns": False},
        headers=NOTION_HEADERS, timeout=20)
    resp.raise_for_status()
    blocks = resp.json().get("recordMap", {}).get("block", {})

    for _ in range(8):
        new_ids = []
        visited = set(blocks.keys())
        for bid, bdata in list(blocks.items()):
            b = bdata.get("value", {}).get("value", {})
            for cid in b.get("content", []):
                if cid not in visited:
                    new_ids.append(cid)
                    visited.add(cid)
        if not new_ids:
            break
        for i in range(0, len(new_ids), 30):
            batch = new_ids[i:i+30]
            r = httpx.post(f"{NOTION_BASE}/syncRecordValues",
                json={"requests": [{"pointer": {"table": "block", "id": bid}, "version": -1} for bid in batch]},
                headers=NOTION_HEADERS, timeout=20)
            if r.status_code == 200:
                blocks.update(r.json().get("recordMap", {}).get("block", {}))
            time.sleep(0.3)

    print(f"[fetch] 총 블록: {len(blocks)}")
    return blocks


# ── Step 2: Extract text chunks by section/person ────────────────────────────

def get_text(b):
    title_arr = b.get("properties", {}).get("title", [])
    parts = []
    for seg in title_arr:
        if isinstance(seg, list) and seg:
            parts.append(str(seg[0]))
    return "".join(parts).strip()


def extract_chunks(blocks):
    main_key = next((k for k in blocks if "7cecb6b5" in k.replace("-", "")), None)
    if not main_key:
        raise ValueError("메인 페이지 블록을 찾을 수 없습니다.")
    main_children = blocks[main_key].get("value", {}).get("value", {}).get("content", [])

    chunks = []
    current_section = PAGE_TITLE
    current_texts = []

    def flush():
        txt = "\n".join(current_texts).strip()
        if txt and len(txt) > 30:
            chunks.append({"section": current_section, "text": txt})
        current_texts.clear()

    def traverse(bid):
        nonlocal current_section
        b = blocks.get(bid, {}).get("value", {}).get("value", {})
        if not b:
            return
        btype = b.get("type", "")
        t = get_text(b)

        if btype == "sub_header":
            flush()
            current_section = t
        elif btype == "toggle":
            # 담당자별 mini-chunk
            lines = [t] if t else []
            for cid in b.get("content", []):
                cb = blocks.get(cid, {}).get("value", {}).get("value", {})
                ct = get_text(cb)
                if ct:
                    lines.append("  - " + ct)
            chunk_txt = "\n".join(lines)
            if len(chunk_txt) > 20:
                chunks.append({"section": current_section, "text": chunk_txt})
        elif btype in ("bulleted_list", "text", "callout") and t:
            current_texts.append(t)

        if btype not in ("toggle",):
            for cid in b.get("content", []):
                traverse(cid)

    for cid in main_children:
        traverse(cid)
    flush()

    print(f"[extract] 추출된 chunk: {len(chunks)}개")
    return chunks


# ── Step 3: Embed via Gemini ──────────────────────────────────────────────────

def embed_texts(texts: list[str]) -> list[list[float]]:
    from google import genai
    client = genai.Client(api_key=GEMINI_API_KEY)
    vectors = []
    for i in range(0, len(texts), 50):
        batch = texts[i:i+50]
        result = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=batch,
            config={"output_dimensionality": EMBEDDING_DIM},
        )
        vectors.extend([e.values for e in result.embeddings])
        print(f"  embed {i+len(batch)}/{len(texts)}")
        time.sleep(0.5)
    return vectors


# ── Step 4: Upsert to Qdrant ─────────────────────────────────────────────────

def upsert_to_qdrant(points_data: list[dict]) -> int:
    from qdrant_client import QdrantClient
    from qdrant_client.models import PointStruct

    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_KEY, timeout=30)
    points = [
        PointStruct(id=p["id"], vector=p["vector"], payload=p["payload"])
        for p in points_data
    ]
    for i in range(0, len(points), 50):
        client.upsert(collection_name=COLLECTION, points=points[i:i+50])
    print(f"[qdrant] upserted {len(points)}개")
    return len(points)


# ── Step 5: Update local JSON ─────────────────────────────────────────────────

def update_local_json(new_points: list[dict]):
    existing = json.loads(LOCAL_JSON.read_text(encoding="utf-8"))
    # Remove old CRAVER 지식in entries
    existing = [p for p in existing if p.get("payload", {}).get("page_url", "") != PAGE_URL]
    existing.extend(new_points)
    LOCAL_JSON.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[json] 로컬 JSON 업데이트: {len(existing)}개 total")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=== CRAVER 지식in 임베딩 시작 ===")

    blocks = fetch_all_blocks()
    chunks = extract_chunks(blocks)

    if not chunks:
        print("추출된 chunk 없음. 종료.")
        return

    texts = [c["text"] for c in chunks]
    print(f"\n[embed] {len(texts)}개 chunk 임베딩 중...")
    vectors = embed_texts(texts)

    points_data = []
    for chunk, vector in zip(chunks, vectors):
        pt_id = str(uuid.uuid5(uuid.NAMESPACE_URL, PAGE_URL + chunk["text"][:100]))
        payload = {
            "team": TEAM,
            "page_title": PAGE_TITLE,
            "page_url": PAGE_URL,
            "section_path": chunk["section"],
            "text": chunk["text"],
            "last_edited_time": "2026-05-18T00:00:00.000Z",
        }
        points_data.append({"id": pt_id, "vector": vector, "payload": payload})

    upsert_to_qdrant(points_data)
    update_local_json(points_data)

    print(f"\n=== 완료: {len(points_data)}개 chunk 학습 ===")
    for c in chunks:
        print(f"  [{c['section']}] {c['text'][:80]}")


if __name__ == "__main__":
    main()
