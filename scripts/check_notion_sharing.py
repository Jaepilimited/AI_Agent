# -*- coding: utf-8 -*-
"""노션 인테그레이션 공유 여부를 확인한다 — 공유 작업의 진행률 자.

왜 필요한가 (2026-08-25, 붐따 #105):
    색인된 노션 문서 중 `last_edited_time` 이 비어 있는 것들이 있다. 그건 버그가
    아니라 **표식**이다 (`qdrant_db/app/services/ingest_page.py`):
    인테그레이션에 공유되지 않아 API 대신 **공개 링크를 긁어** 넣은 페이지다.

    ⛔ 수정일이 없으면 **"값이 다르면 최신 문서를 따르라" 규칙이 작동하지 않는다.**
       야근 식대 답변이 2023년 문서의 10,000원을 골랐고, 15,000원이 적힌 `복리후생`
       은 날짜가 없어 밀렸다. 인사 규정 문서군이 통째로 이 상태였다.

⚠️ **공유 여부는 파이프라인을 기다리지 않고 지금 알 수 있다.** 공유 안 된 페이지는
   `GET /v1/pages/{id}` 가 404 를 준다. 색인에 반영되는 것은 그 다음 문제다.

사용:
    python scripts/check_notion_sharing.py              # 문서 목록으로 확인
    python scripts/check_notion_sharing.py --rescan     # 색인을 다시 훑어 목록 갱신 후 확인

`--rescan` 은 `qdrant_client` 가 필요하다 (서버에는 있다). 없으면 문서 목록만 쓴다.
목록 원본: docs/notion_unshared_pages.md
"""
from __future__ import annotations

import io
import os
import re
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
DOC = PROJ / "docs" / "notion_unshared_pages.md"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _page_id(url: str) -> str:
    """노션 URL 끝의 32자리 16진수 페이지 id."""
    return re.sub(r"[^0-9a-f]", "", (url or "").split("/")[-1].lower())[-32:]


def from_doc() -> list[tuple[str, str, str]]:
    """문서 표에서 (팀, 제목, page_id) 를 읽는다."""
    if not DOC.exists():
        return []
    out = []
    for line in io.open(DOC, encoding="utf-8"):
        m = re.match(r"\|\s*\d+\s*\|\s*([^|]+)\|\s*([^|]+)\|\s*(\S+)\s*\|", line)
        if m and "notion.so" in m.group(3):
            pid = _page_id(m.group(3))
            if len(pid) == 32:
                out.append((m.group(1).strip(), m.group(2).strip(), pid))
    return out


def from_index() -> list[tuple[str, str, str]]:
    """색인을 훑어 **수정일 없는 notion 소스** 페이지를 다시 모은다.

    ⚠️ `source='google_sheets'` 링크 카드는 원래 노션 수정일이 없다 — 제외한다.
    """
    sys.path.insert(0, str(PROJ))
    from qdrant_client import QdrantClient

    from app.agents.qdrant_agent import COLLECTION, _qdrant_api_key, _qdrant_url

    # ⚠️ 공용 클라이언트(timeout=15)로는 scroll 이 자주 끊긴다 — 넉넉히 준다
    cl = QdrantClient(url=_qdrant_url(), api_key=_qdrant_api_key(), timeout=180)
    pages, nxt = {}, None
    while True:
        pts, nxt = cl.scroll(collection_name=COLLECTION, limit=256, offset=nxt,
                             with_payload=True, with_vectors=False)
        if not pts:
            break
        for p in pts:
            pl = p.payload or {}
            if str(pl.get("last_edited_time") or "") or pl.get("source") != "notion":
                continue
            pid = _page_id(str(pl.get("page_url") or ""))
            if len(pid) == 32:
                pages[pid] = (str(pl.get("team") or "?"),
                              str(pl.get("page_title") or "(제목없음)"), pid)
        if nxt is None:
            break
    return sorted(pages.values(), key=lambda r: (r[0], r[1]))


def main() -> int:
    rows = []
    if "--rescan" in sys.argv:
        try:
            rows = from_index()
            print(f"색인 재스캔: 수정일 없는 notion 페이지 {len(rows)}개")
        except ImportError as e:
            print(f"qdrant_client 없음 — 문서 목록을 쓴다 ({e})")
        except Exception as e:
            print(f"색인 스캔 실패 — 문서 목록을 쓴다: {str(e)[:120]}")
    if not rows:
        rows = from_doc()
        print(f"문서 목록: {len(rows)}개 ({DOC.relative_to(PROJ)})")
    if not rows:
        print("확인할 페이지가 없다.")
        return 1

    from dotenv import load_dotenv
    load_dotenv(PROJ / ".env")
    import httpx

    token = os.getenv("NOTION_MCP_TOKEN", "")
    if not token:
        print("NOTION_MCP_TOKEN 이 필요하다 (.env)")
        return 1
    headers = {"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28"}

    shared, unshared, other = [], [], []
    print()
    with httpx.Client(timeout=30) as c:
        for team, title, pid in rows:
            try:
                r = c.get(f"https://api.notion.com/v1/pages/{pid}", headers=headers)
            except Exception as e:
                other.append((title, f"요청 실패: {str(e)[:60]}"))
                print(f"  ⚠️  {title[:34]:<34} 요청 실패")
                continue
            if r.status_code == 200:
                edited = str(r.json().get("last_edited_time", ""))[:10]
                shared.append((title, edited))
                print(f"  ✅ 공유됨  {title[:34]:<34} 노션 수정일 {edited}")
            elif r.status_code == 404:
                unshared.append(title)
                print(f"  ❌ 미공유  {title[:34]:<34} ({team})")
            else:
                other.append((title, f"HTTP {r.status_code}"))
                print(f"  ⚠️  {title[:34]:<34} HTTP {r.status_code} {r.text[:60]}")

    print(f"\n공유됨 {len(shared)} · 미공유 {len(unshared)} · 기타 {len(other)}")
    if unshared:
        print("\n해야 할 일: 노션에서 각 페이지 ••• → 연결(Connections) → 인테그레이션 추가")
        print("그 뒤 05:00 qdrant_pipeline_daily 가 API 로 다시 수집하며 수정일이 채워진다.")
    elif shared:
        print("\n전부 공유됐다. 다음 파이프라인 실행 뒤 색인의 수정일이 채워졌는지 확인할 것:")
        print("  python scripts/check_notion_sharing.py --rescan   # 0개면 끝난 것")
    return 0 if not unshared else 2


if __name__ == "__main__":
    raise SystemExit(main())
