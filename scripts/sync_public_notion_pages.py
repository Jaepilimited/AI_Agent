# -*- coding: utf-8 -*-
"""공개 노션 페이지(`*.notion.site`)를 다시 긁어 색인을 최신으로 유지한다.

왜 따로 필요한가:
    DB-HUB 에 걸린 페이지 중 일부는 **인테그레이션에 연결돼 있지 않아** Notion API 가
    404 를 준다 (2026-08-25 확인: 21개). 그 페이지들은 **공개 게시**돼 있어서
    브라우저로는 읽힌다. 그래서 예전에 Playwright 로 한 번 긁어 넣었는데 —
    **그 뒤로 다시 읽는 경로가 없었다.**

    ⛔ 05:00 `qdrant_pipeline_daily`(`notion_qdrant_pipeline.py`)는 API 로만 수집하므로
       이 페이지들을 건드리지 못한다. 노션에서 규정이 바뀌어도 색인은 그대로다.
       에러가 나지 않는다 — 검색도 되고 답도 나온다. 낡은 값이 나갈 뿐이다.

⛔ **임베딩은 반드시 Gemini 다.** 예전 수집기(`qdrant_db/`)는 OpenAI 를 썼고, 그래서
   컬렉션의 55%가 질의와 다른 공간에 있었다 — 검색에 영영 안 걸렸다 (붐따 #105).
   여기서는 `notion_qdrant_pipeline` 의 `chunk_text`·`embed_texts` 를 **그대로 재사용**한다.
   같은 규칙을 두 번 구현하지 않는다.

⚠️ **WAS 에서는 돌 수 없다.** `notion.site` 가 프록시 화이트리스트에 없어 20초 타임아웃이
   나고 Playwright 도 깔려 있지 않다 (2026-08-25 실측). DB_PC 에서 돌린다.

대상은 손으로 적지 않는다 — **색인이 스스로 말하게** 한다:
`source == "notion"` 이고 `last_edited_time` 이 빈 것 = 공개 링크로 긁어 넣은 페이지.

사용:
    python scripts/sync_public_notion_pages.py            # 미리보기 (대상·변경 여부만)
    python scripts/sync_public_notion_pages.py --apply    # 바뀐 페이지만 다시 색인
    python scripts/sync_public_notion_pages.py --apply --force   # 전부 다시 색인
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import time
import uuid
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_DEFAULT_QDRANT_URL = ("https://bf41bcbe-af68-416f-9d26-1b3d64f7bed0"
                       ".us-east-1-1.aws.cloud.qdrant.io:6333")
_DEFAULT_QDRANT_KEY = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2Nlc3MiOiJtIiwic3Vi"
                       "amVjdCI6ImFwaS1rZXk6OTFkOGVkZWYtNTFkNi00ODNhLTg0MDItZTdjNjI0"
                       "ZjA2NThmIn0.K0zdMdpnbIMl_yfXV8EJfcClpPnkoPa_SS_XbDI1kv4")

# 노션 공개 사이트가 머리·꼬리에 붙이는 상용구 — 본문이 아니다
_JUNK = ("Skip to content", "Get Notion free")

# ⛔ **토글은 기본으로 접혀 있다.** 펼치지 않으면 본문의 상당 부분이 통째로 빠진다.
#    처음 만들 때 이걸 빠뜨려 `근태/휴가` 가 379자로 읽혔다 — 색인에는 10청크짜리
#    문서다. 그대로 덮어썼으면 문서를 잘라 먹었다 (2026-08-25 미리보기에서 발견).
#    `qdrant_db/app/notion/public_scraper.py` 가 쓰던 것과 같은 방식이다.
_EXPAND_TOGGLES_JS = """
    const buttons = document.querySelectorAll('.notion-toggle-block [role="button"]');
    let count = 0;
    buttons.forEach(btn => {
        if (btn.getAttribute('aria-expanded') === 'false') { btn.click(); count++; }
    });
    count;
"""
# 본문은 `.notion-page-content` 다 — body 전체를 쓰면 머리말·푸터가 섞인다
_EXTRACT_CONTENT_JS = """
    const el = document.querySelector('.notion-page-content');
    el ? el.innerText : (document.body ? document.body.innerText : '');
"""

# ⛔ 스크래핑은 불안정하다 — 같은 페이지가 한 번은 2,990자, 다음엔 0자로 읽혔다
#    (2026-08-25 실측). 짧게 읽힌 것으로 색인을 덮으면 **검색에서 조용히 얇아진다.**
#    기존 색인 본문 길이의 이 비율보다 작으면 **거부**한다.
_SHRINK_GUARD = 0.7
# 토글이 더 안 열릴 때까지 반복한다 (중첩 토글 때문에 한 번으로는 부족하다)
_EXPAND_ROUNDS = 4
# ⚠️ 한 브라우저 컨텍스트로 18개를 연달아 열면 뒤쪽이 빈 채로 온다 (2026-08-25 실측:
#    미리보기는 18/18 성공했는데 바로 다음 실행에서 4개가 "본문 없음"). 노션이
#    조이는 것으로 보인다. **페이지마다 컨텍스트를 새로 열고 사이를 띄운다.**
_PAGE_GAP_SEC = 3.0


def _qdrant():
    from qdrant_client import QdrantClient
    # ⚠️ 공용 클라이언트(timeout=15)로는 scroll·upsert 가 자주 끊긴다
    return QdrantClient(url=os.getenv("QDRANT_URL") or _DEFAULT_QDRANT_URL,
                        api_key=os.getenv("QDRANT_API_KEY") or _DEFAULT_QDRANT_KEY,
                        timeout=180)


def find_public_pages(q, collection: str) -> dict:
    """색인에서 공개 수집 페이지를 찾는다 → {page_id: {...}}."""
    pages: dict = {}
    nxt = None
    while True:
        pts, nxt = q.scroll(collection_name=collection, limit=256, offset=nxt,
                            with_payload=True, with_vectors=False)
        if not pts:
            break
        for p in pts:
            pl = p.payload or {}
            if str(pl.get("source")) != "notion" or str(pl.get("last_edited_time") or ""):
                continue
            pid = str(pl.get("page_id") or "")
            url = str(pl.get("page_url") or "")
            if not pid or "notion.so" not in url:
                continue
            e = pages.setdefault(pid, {"url": url, "team": str(pl.get("team") or "?"),
                                       "title": str(pl.get("page_title") or ""),
                                       "chunks": 0, "indexed_len": 0,
                                       "doc_sha256": ""})
            e["chunks"] += 1
            e["indexed_len"] += len(str(pl.get("text") or ""))
            e["doc_sha256"] = e["doc_sha256"] or str(pl.get("doc_sha256") or "")
        if nxt is None:
            break
    return pages


def _scrape_once(page, url: str) -> tuple[str, str]:
    """한 번 읽는다. 토글을 **더 안 열릴 때까지** 펼치고 lazy-load 를 끝까지 굴린다."""
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    try:
        page.wait_for_selector(".notion-page-content", timeout=20000)
    except Exception:
        return "", ""      # 로그인 벽·비공개 — 본문 없음으로 다룬다

    # lazy-load: 스크롤 → 토글 펼치기를 번갈아 반복한다. 토글 안에 또 토글이 있어
    # 한 번으로는 안 끝나고, 펼치면 새 내용이 붙어 다시 스크롤이 필요하다.
    for _ in range(_EXPAND_ROUNDS):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1200)
        try:
            opened = page.evaluate(_EXPAND_TOGGLES_JS) or 0
        except Exception:
            opened = 0
        if not opened:
            break
        page.wait_for_timeout(1500)

    title = (page.title() or "").strip()
    body = re.sub(r"\s+", " ", page.evaluate(_EXTRACT_CONTENT_JS) or "")
    for junk in _JUNK:
        body = body.replace(junk, " ")
    return title, body.strip()


def scrape(browser, url: str, retries: int = 2) -> tuple[str, str]:
    """(제목, 본문) — 가장 길게 읽힌 결과를 쓴다.

    ⚠️ 같은 페이지가 한 번은 2,990자, 다음엔 0자로 읽혔다 (2026-08-25 실측).
       렌더가 늦거나 노션이 잠시 막는다. **가장 많이 읽힌 판**을 채택한다 —
       짧게 읽힌 것으로 색인을 덮는 것이 이 작업에서 가장 위험한 실패다.
    ⚠️ 시도마다 **컨텍스트를 새로 연다.** 한 컨텍스트를 계속 쓰면 뒤쪽 페이지가
       빈 채로 온다 (미리보기 18/18 성공 직후 실행에서 4개가 "본문 없음").
    """
    best = ("", "")
    for attempt in range(retries + 1):
        ctx = browser.new_context()
        try:
            page = ctx.new_page()
            title, body = _scrape_once(page, url)
        except Exception:
            title, body = "", ""
        finally:
            try:
                ctx.close()
            except Exception:
                pass
        if len(body) > len(best[1]):
            best = (title or best[0], body)
        if body and attempt >= 1:
            break          # 두 번 이상 읽어 봤고 내용이 있으면 그만
        if attempt < retries:
            time.sleep(_PAGE_GAP_SEC)
    return best


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(PROJ / ".env")
    from qdrant_client.models import (FieldCondition, Filter, FilterSelector,
                                      MatchValue, PointStruct)

    from scripts.notion_qdrant_pipeline import COLLECTION, chunk_text, embed_texts

    apply = "--apply" in sys.argv
    force = "--force" in sys.argv
    q = _qdrant()

    print("===== 공개 노션 페이지 동기화 ({}) =====\n".format("적용" if apply else "미리보기"))
    pages = find_public_pages(q, COLLECTION)
    print("대상 페이지 {}개 (색인에서 자동 판별: source=notion · 수정일 없음)\n".format(len(pages)))
    if not pages:
        return 0

    stats = {"unchanged": 0, "updated": 0, "empty": 0, "shrunk": 0, "error": 0}
    t0 = time.time()

    from playwright.sync_api import sync_playwright

    # ⚠️ 브라우저를 페이지마다 새로 띄우지 않는다 — 21개면 그것만으로 몇 분이 든다
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            for pid, info in sorted(pages.items(), key=lambda kv: -kv[1]["chunks"]):
                label = "[{}] {:<26}".format(info["team"], info["title"][:26])
                try:
                    title, body = scrape(browser, info["url"])
                except Exception as e:
                    print("  ERROR   {} {}: {}".format(label, type(e).__name__, str(e)[:60]))
                    stats["error"] += 1
                    continue
                if not body:
                    # ⚠️ 본문이 비면 **덮어쓰지 않는다.** 스크래핑 실패로 멀쩡한 색인을
                    #    지우면 검색에서 조용히 사라진다 — 낡은 사본이 남는 편이 낫다.
                    print("  EMPTY   {} 본문 없음 — 기존 색인 유지".format(label))
                    stats["empty"] += 1
                    continue
                # ⛔ 짧게 읽힌 것으로 덮으면 문서를 잘라 먹는다. 기존보다 크게 줄면 거부.
                if info["indexed_len"] and len(body) < info["indexed_len"] * _SHRINK_GUARD:
                    print("  줄어듦  {} {}자 → {}자 — 거부 (기존 색인 유지)".format(
                        label, info["indexed_len"], len(body)))
                    stats["shrunk"] += 1
                    continue

                _handle(q, COLLECTION, pid, info, title, body, label, apply, force,
                        stats, chunk_text, embed_texts, PointStruct,
                        FieldCondition, Filter, FilterSelector, MatchValue)
                time.sleep(_PAGE_GAP_SEC)   # 노션이 조이지 않게 사이를 띄운다
        finally:
            browser.close()

    print("\n{}: 갱신 {} · 같음 {} · 본문없음 {} · 줄어듦 {} · 오류 {} ({:.0f}초)".format(
        "적용" if apply else "미리보기", stats["updated"], stats["unchanged"],
        stats["empty"], stats["shrunk"], stats["error"], time.time() - t0))
    if not apply and stats["updated"]:
        print("--apply 를 붙이면 반영한다.")
    return 1 if stats["error"] else 0


def _handle(q, COLLECTION, pid, info, title, body, label, apply, force, stats,
            chunk_text, embed_texts, PointStruct,
            FieldCondition, Filter, FilterSelector, MatchValue) -> None:
    """한 페이지를 판정하고, 바뀌었으면 다시 색인한다."""
    doc_hash = hashlib.sha256(body.encode()).hexdigest()
    if not force and info["doc_sha256"] and info["doc_sha256"] == doc_hash:
        print("  같음    {} ({}자)".format(label, len(body)))
        stats["unchanged"] += 1
        return
    if not apply:
        print("  변경됨  {} ({}자, 색인 {}자) — --apply 로 반영".format(
            label, len(body), info["indexed_len"]))
        stats["updated"] += 1
        return

    raw = chunk_text(body)
    vecs = embed_texts(raw)
    points = []
    for i, (chunk, vec) in enumerate(zip(raw, vecs)):
        points.append(PointStruct(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, "{}:{}".format(pid, i))),
            vector=vec,
            payload={
                "source": "notion",
                "team": info["team"],
                "page_id": pid,
                "page_title": title or info["title"],
                "page_url": info["url"],
                "breadcrumb": "{} > {}".format(info["team"], title or info["title"]),
                "chunk_index": i,
                # ⚠️ 공개 페이지는 수정일을 알 수 없다. 빈 값이 **표식**이라
                #    `_vintage_note()` 가 "수정 시점을 알 수 없습니다" 를 붙인다.
                "last_edited_time": "",
                "doc_sha256": doc_hash,
                "content_sha256": hashlib.sha256(chunk.encode()).hexdigest(),
                "text": chunk,
            },
        ))
    # 조각 수가 줄면 옛 조각이 남는다 — 먼저 지운다
    q.delete(collection_name=COLLECTION, points_selector=FilterSelector(
        filter=Filter(must=[FieldCondition(key="page_id",
                                           match=MatchValue(value=pid))])))
    q.upsert(collection_name=COLLECTION, points=points)
    print("  갱신    {} {} → {} chunks ({}자)".format(
        label, info["chunks"], len(points), len(body)))
    stats["updated"] += 1


if __name__ == "__main__":
    raise SystemExit(main())
