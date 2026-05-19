# Notion Sync Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** DB-HUB를 단일 진입점으로 사용해 노션 데이터를 자동 스캔·벡터화하는 파이프라인을 재작성하고, `/notion-sync` Claude Code 커맨드와 wiki 문서를 추가한다.

**Architecture:** DB-HUB 페이지(`2e12b4283b008011ae32e39bf73b7f7b`)의 팀 토글을 재귀 탐색해 모든 페이지를 수집한다. 로컬 JSON(`data/notion_vectors_gemini.json`)의 `last_edited_time`과 비교해 변경/신규 페이지만 Gemini 임베딩 후 저장, `qdrant_agent` 인메모리 hot-reload까지 자동 처리한다.

**Tech Stack:** Python 3.11+, httpx, Gemini embedding-001 (1536d), numpy, google-genai SDK

---

## File Map

| 파일 | 작업 |
|------|------|
| `scripts/notion_qdrant_pipeline.py` | 전체 재작성 — DB-HUB 재귀 탐색, `DATABASE_TARGETS` 제거 |
| `.claude/commands/notion-sync.md` | 신규 생성 — Claude Code `/notion-sync` 커맨드 |
| `knowledge_map/wiki/notion_pipeline.md` | 신규 생성 — 파이프라인 wiki 문서 |

---

## Task 1: notion_qdrant_pipeline.py 재작성

**Files:**
- Modify: `scripts/notion_qdrant_pipeline.py` (전체 교체)

- [ ] **Step 1: 파일 상단 — imports, config, DB-HUB 상수**

```python
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
COLLECTION = "notion_hub_gemini"


def notion_headers() -> dict:
    return {"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": NOTION_VERSION}


def qdrant_headers() -> dict:
    return {"api-key": QDRANT_API_KEY, "Content-Type": "application/json"}
```

- [ ] **Step 2: DB-HUB 재귀 탐색 함수 작성**

```python
def crawl_hub(client: httpx.Client) -> list[dict]:
    """DB-HUB 팀 토글을 재귀 탐색해 {page_id, title, team, url} 목록 반환."""
    hub_blocks = _get_block_children(DB_HUB_ID, client)
    pages = []
    for block in hub_blocks:
        if block.get("type") == "toggle":
            team = _rich_text(block["toggle"])
            _collect_from_block(block["id"], team, client, pages)
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
    """토글/paragraph 블록에서 child_page, child_database, mention_page 수집."""
    children = _get_block_children(block_id, client)
    for b in children:
        btype = b.get("type", "")
        bid = b.get("id", "")

        if btype == "child_page":
            title = b.get("child_page", {}).get("title", "")
            pages.append({"page_id": bid, "title": title, "team": team, "source_type": "child_page"})

        elif btype == "child_database":
            title = b.get("child_database", {}).get("title", "")
            pages.append({"page_id": bid, "title": title, "team": team, "source_type": "child_db"})

        elif btype == "toggle":
            sub_team = _rich_text(b["toggle"]) or team
            _collect_from_block(bid, sub_team, client, pages)

        elif btype == "paragraph":
            for chunk in b.get("paragraph", {}).get("rich_text", []):
                if chunk.get("type") == "mention":
                    m = chunk.get("mention", {})
                    if m.get("type") == "page":
                        pid = m["page"]["id"]
                        text = chunk.get("plain_text", "")
                        pages.append({"page_id": pid, "title": text, "team": team, "source_type": "mention"})
```

- [ ] **Step 3: 페이지 메타/본문 조회 함수 작성**

```python
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


def fetch_page_text(page_id: str, client: httpx.Client) -> str:
    """Notion 페이지 블록 텍스트 추출."""
    resp = client.get(
        f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100",
        headers=notion_headers(),
    )
    if resp.status_code != 200:
        return ""
    texts = []
    for b in resp.json().get("results", []):
        btype = b.get("type", "")
        rt = b.get(btype, {}).get("rich_text", [])
        text = "".join(t.get("plain_text", "") for t in rt)
        if text.strip():
            texts.append(text.strip())
    return "\n".join(texts)
```

- [ ] **Step 4: 청킹·임베딩·로컬 JSON 관리 함수 작성**

```python
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
```

- [ ] **Step 5: 핵심 파이프라인 함수 작성**

```python
def run_incremental_sync(full: bool = False) -> dict:
    print(f"\n=== Notion → Local JSON {'전체' if full else '증분'} 동기화 (DB-HUB 기준) ===")
    t0 = time.time()
    stats = {"new": 0, "updated": 0, "deleted": 0, "unchanged": 0, "errors": 0, "skipped_404": 0}

    local_page_map: dict[str, str] = {} if full else get_local_page_map()

    with httpx.Client(timeout=20) as client:
        print(f"\n  DB-HUB 탐색 중...")
        hub_pages = crawl_hub(client)
        # page_id 중복 제거 (같은 페이지가 여러 팀 토글에 멘션될 수 있음)
        seen_ids: set[str] = set()
        unique_pages = []
        for p in hub_pages:
            pid = p["page_id"].replace("-", "")
            if pid not in seen_ids:
                seen_ids.add(pid)
                unique_pages.append(p)
        print(f"  HUB 등록 페이지: {len(unique_pages)}개 (중복 제거 후)")

        notion_page_ids: set[str] = set()
        new_page_vectors: dict[str, list[dict]] = {}

        for entry in unique_pages:
            page_id = entry["page_id"]
            team = entry["team"]

            # 실제 페이지 메타 조회
            meta = fetch_page_meta(page_id, client)
            if meta is None:
                print(f"  SKIP(404): {entry['title'][:50]}")
                stats["skipped_404"] += 1
                continue

            notion_page_ids.add(page_id)
            local_edited = local_page_map.get(page_id)
            is_new = local_edited is None

            if not full and not is_new and meta["last_edited_time"] == local_edited:
                stats["unchanged"] += 1
                continue

            # 본문 수집 + 임베딩
            try:
                text = fetch_page_text(page_id, client)
                if not text.strip():
                    print(f"  EMPTY: {meta['title'][:50]}")
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
                print(f"  ERROR: {meta['title'][:40]}: {e}")
                stats["errors"] += 1

    # 로컬에만 있고 HUB에서 사라진 페이지
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
    stats = run_incremental_sync(full=full)
    try:
        from app.agents.qdrant_agent import reload_vectors
        reload_vectors()
        print("  인메모리 벡터 스토어 hot-reload 완료")
        stats["reloaded"] = True
    except Exception:
        stats["reloaded"] = False
    return stats
```

- [ ] **Step 6: Qdrant Cloud 백업 + CLI main 함수 작성**

```python
def upload_to_qdrant_cloud() -> int:
    if not LOCAL_JSON.exists():
        print("  [ERROR] 로컬 JSON 없음")
        return 0
    with open(LOCAL_JSON, "r", encoding="utf-8") as f:
        all_points = json.load(f)
    print(f"  Qdrant Cloud 업로드: {len(all_points)} 포인트...")
    with httpx.Client(timeout=30) as client:
        client.delete(f"{QDRANT_URL}/collections/{COLLECTION}", headers=qdrant_headers())
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
    print(f"  Qdrant Cloud 업로드 완료")
    return len(all_points)


def main():
    parser = argparse.ArgumentParser(description="Notion DB-HUB → Local JSON 증분 동기화")
    parser.add_argument("--full",         action="store_true", help="전체 재동기화")
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
```

- [ ] **Step 7: 파일 전체 조립 후 동작 확인**

```bash
cd C:\Users\DB_PC\Desktop\python_bcj\AI_Agent
python -X utf8 scripts/notion_qdrant_pipeline.py --status
```

Expected: `로컬 JSON: 없음` (삭제했으니까)

- [ ] **Step 8: 증분 sync 실행 테스트**

```bash
python -X utf8 scripts/notion_qdrant_pipeline.py 2>&1
```

Expected: DB-HUB 탐색 → 페이지 발견 → 임베딩 → 로컬 JSON 저장 → hot-reload 완료

- [ ] **Step 9: 커밋**

```bash
git add scripts/notion_qdrant_pipeline.py
git commit -m "feat(pipeline): DB-HUB 단일 진입점으로 파이프라인 재작성 (v3.0)"
```

---

## Task 2: `/notion-sync` Claude Code 커맨드 생성

**Files:**
- Create: `.claude/commands/notion-sync.md`

- [ ] **Step 1: 커맨드 파일 작성**

`.claude/commands/notion-sync.md` 에 다음 내용을 작성:

```markdown
노션 사내 문서를 DB-HUB 기준으로 스캔하고, 변경/미학습 페이지를 벡터화해 프로그램에 반영합니다.

## 작업 순서

1. **현황 확인**
   ```bash
   python -X utf8 scripts/notion_qdrant_pipeline.py --status
   ```
   - 로컬 JSON 크기와 페이지 수 출력

2. **증분 동기화 실행** (기본 — 변경된 페이지만)
   ```bash
   python -X utf8 scripts/notion_qdrant_pipeline.py
   ```

3. **전체 재동기화** (필요 시 — 로컬 JSON 초기화 후 전체 재학습)
   ```bash
   python -X utf8 scripts/notion_qdrant_pipeline.py --full
   ```

4. **결과 리포트**: 완료 후 아래 통계 출력
   - 신규/업데이트/삭제/404스킵/오류 페이지 수
   - 총 포인트 수 및 파일 크기

## DB-HUB 정보

- **URL**: https://www.notion.so/skin1004/DB-HUB-2e12b4283b008011ae32e39bf73b7f7b
- **역할**: 모든 팀 노션 페이지의 단일 진입점
- **팀 구조**: Craver, DB, KBT, JBT, [GM]EAST, [GM]WEST, BCM, PEOPLE, IT, CS, B2B1, B2B2
- **페이지 추가**: DB-HUB의 해당 팀 토글에 페이지 멘션 추가 → 다음 sync에 자동 반영

## 데이터 흐름

```
DB-HUB → 팀 토글 재귀 탐색 → 페이지 목록 수집
  → last_edited_time diff → 변경 페이지만 선별
  → Gemini embedding-001 (1536d, 800자 청킹)
  → data/notion_vectors_gemini.json 저장
  → qdrant_agent 인메모리 hot-reload
```

## 자동 실행

- **매일 02:00** APScheduler(`qdrant_pipeline_daily`)가 자동 증분 sync
- 수동 실행은 이 커맨드로

## 주의 사항

- Qdrant Cloud는 3일 만료 → 로컬 JSON이 소스 오브 트루스
- 전체 재동기화(`--full`)는 약 10~30분 소요 (페이지 수에 따라)
- 404 에러 페이지 = Notion 통합에 권한이 없는 페이지 (DB-HUB에서 제거 권장)
- Gemini API 한도 초과 시 `errors` 카운트 증가 → 재실행으로 해결
```

- [ ] **Step 2: 커밋**

```bash
git add .claude/commands/notion-sync.md
git commit -m "feat(skill): /notion-sync 커맨드 추가 — DB-HUB 기준 노션 동기화"
```

---

## Task 3: knowledge_map wiki 문서 추가

**Files:**
- Create: `knowledge_map/wiki/notion_pipeline.md`

- [ ] **Step 1: wiki 파일 작성**

`knowledge_map/wiki/notion_pipeline.md` 에 다음 내용을 작성:

```markdown
# Notion 벡터 파이프라인

**스크립트**: `scripts/notion_qdrant_pipeline.py` (v3.0)
**커맨드**: `/notion-sync`
**자동 실행**: 매일 02:00 (APScheduler `qdrant_pipeline_daily`)

## 전체 흐름

```
DB-HUB (단일 진입점)
  └─ 팀 토글 (Craver, DB, KBT, JBT, EAST, WEST, BCM, PEOPLE, IT, CS, B2B1, B2B2)
       └─ 멘션/child_page/child_database
            ↓ Notion API 블록 텍스트 추출
       800자 청킹 (overlap 100자)
            ↓ Gemini embedding-001 (1536d, 50개 배치)
       data/notion_vectors_gemini.json (로컬 소스 오브 트루스)
            ↓ app.agents.qdrant_agent.reload_vectors()
       인메모리 numpy 벡터 스토어 (검색에 사용)
```

## DB-HUB

| 항목 | 값 |
|------|-----|
| URL | https://www.notion.so/skin1004/DB-HUB-2e12b4283b008011ae32e39bf73b7f7b |
| Page ID | 2e12b4283b008011ae32e39bf73b7f7b |
| 역할 | 모든 팀 페이지의 단일 등록 허브 |

새 페이지를 학습시키려면 → **DB-HUB의 해당 팀 토글에 페이지 멘션 추가** → 다음 sync에 자동 반영.

## 로컬 JSON 구조

```json
[
  {
    "id": "uuid-v5",
    "vector": [0.123, ...],
    "payload": {
      "source": "{team}-hub",
      "team": "DB",
      "page_id": "...",
      "page_title": "...",
      "page_url": "https://notion.so/...",
      "breadcrumb": "DB > 페이지명",
      "chunk_index": 0,
      "last_edited_time": "2026-05-07T...",
      "content_sha256": "...",
      "text": "실제 청크 텍스트"
    }
  }
]
```

## 검색 흐름 (런타임)

1. 사용자 질문 → `qdrant_agent.run(query, team_key)`
2. Gemini embedding-001으로 질문 임베딩
3. 인메모리 numpy 배열과 코사인 유사도 계산
4. 상위 8개 (score ≥ 0.3) 추출
5. Gemini Flash로 답변 생성

## 운영 명령

```bash
# 현황 확인
python -X utf8 scripts/notion_qdrant_pipeline.py --status

# 증분 sync (변경 페이지만)
python -X utf8 scripts/notion_qdrant_pipeline.py

# 전체 재동기화
python -X utf8 scripts/notion_qdrant_pipeline.py --full

# Qdrant Cloud 백업 (선택)
python -X utf8 scripts/notion_qdrant_pipeline.py --upload-cloud
```

## 관련 파일

- `app/agents/qdrant_agent.py` — 인메모리 벡터 스토어 및 검색
- `data/notion_vectors_gemini.json` — 로컬 벡터 데이터 (~수십 MB)
- `.claude/commands/notion-sync.md` — Claude Code 커맨드
```

- [ ] **Step 2: 커밋**

```bash
git add knowledge_map/wiki/notion_pipeline.md
git commit -m "docs(wiki): notion_pipeline 운영 wiki 추가"
```
