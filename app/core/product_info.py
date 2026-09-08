# -*- coding: utf-8 -*-
"""제품정보(라인·제품 스펙) — 노션 `스킨1004 전제품 한 눈에 파악하기` → MariaDB.

⛔ **제품 Q&A 와 다른 것이다** (2026-09-04 사용자 지시: *"제품 q&A는 따로이고,
   제품정보는 cs데이터에 들어가야함"*).

    제품 Q&A  = `[BD_BP] CS제품문의_모음집` 시트 — **문의 대응 기록** (914건)
    제품정보  = 이 모듈 — **제품 스펙**(사용법·성분·PPM·피부타입·제형)

   둘을 한 덩어리로 섞으면 출처가 사라진다. 답변에서도 나눠 보여준다.

⛔ **내용은 4단 깊이에 있다** (실측 구조):

    스킨1004 전제품 한 눈에 파악하기        (page)
      └ [DB] New database                depth 1
          └ 히알루-테카 (New)              depth 2  ← 라인
              └ [DB] 히알루 테카            depth 3
                  └ 마다가스카르 히알루-테카 퍼밍 크림  depth 4  ← **진짜 내용**

   ⚠️ 얕게 긁으면 **제품명 목록만** 나온다. 실제로 파이프라인이 그렇게 색인해서
      "히알루테카 제품 정보" 질문에 경로와 링크만 답한 사고가 있었다.

실측(2026-09-04): 페이지 63 · DB 9 · 텍스트 28,944자.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Dict, List, Optional

import httpx
import structlog

logger = structlog.get_logger(__name__)

# 노션 루트 — `스킨1004 전제품 한 눈에 파악하기`
ROOT_PAGE_ID = "3362b428-3b00-8050-b4b6-c75fe4f7f54a"
NOTION_VERSION = "2022-06-28"

# ⚠️ 깊이는 **실측 구조(4단)** 에서 왔다. 넉넉히 6 으로 두되 무한히 열지는 않는다
_MAX_DEPTH = 6
# ⚠️ 한 DB 에서 읽을 최대 행 수 — 폭주 방지 (제품 라인은 최대 11개였다)
_MAX_ROWS = 100

_DDL = """
CREATE TABLE IF NOT EXISTS product_info (
    id INT AUTO_INCREMENT PRIMARY KEY,
    page_id VARCHAR(40) NOT NULL,
    line VARCHAR(120) NOT NULL DEFAULT '',
    product VARCHAR(300) NOT NULL DEFAULT '',
    body MEDIUMTEXT NOT NULL,
    url VARCHAR(400) NOT NULL DEFAULT '',
    synced_at DATETIME NOT NULL,
    UNIQUE KEY uq_page (page_id),
    KEY idx_line (line),
    KEY idx_synced (synced_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def ensure_table() -> None:
    from app.db.mariadb import execute

    try:
        execute(_DDL)
    except Exception as e:
        logger.warning("product_info_table_error", error=str(e)[:160])


def _headers() -> dict:
    token = os.getenv("NOTION_MCP_TOKEN", "")
    return {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION}


def _title_of(obj: dict) -> str:
    if obj.get("object") == "database":
        return "".join(a.get("plain_text", "") for a in obj.get("title", []))
    for v in (obj.get("properties") or {}).values():
        if v.get("type") == "title":
            return "".join(a.get("plain_text", "") for a in v.get("title", []))
    return ""


def _block_text(client: httpx.Client, block_id: str, depth: int = 0) -> str:
    """블록 본문 텍스트 (자식 블록 재귀). 하위 DB 는 여기서 건드리지 않는다."""
    if depth > 3:
        return ""
    out: List[str] = []
    cursor = None
    while True:
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        r = client.get(f"https://api.notion.com/v1/blocks/{block_id}/children",
                       headers=_headers(), params=params)
        if r.status_code != 200:
            break
        data = r.json()
        for b in data.get("results", []):
            btype = b.get("type", "")
            if btype in ("child_page", "child_database"):
                continue
            val = b.get(btype) or {}
            txt = "".join(x.get("plain_text", "") for x in (val.get("rich_text") or []))
            if txt.strip():
                out.append(txt.strip())
            if b.get("has_children"):
                sub = _block_text(client, b["id"], depth + 1)
                if sub:
                    out.append(sub)
        cursor = data.get("next_cursor")
        if not data.get("has_more"):
            break
    return "\n".join(out)


def _child_databases(client: httpx.Client, page_id: str) -> List[tuple]:
    """페이지 안의 child_database (id, title) 목록."""
    out, cursor = [], None
    while True:
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        r = client.get(f"https://api.notion.com/v1/blocks/{page_id}/children",
                       headers=_headers(), params=params)
        if r.status_code != 200:
            break
        data = r.json()
        for b in data.get("results", []):
            if b.get("type") == "child_database":
                out.append((b["id"], (b.get("child_database") or {}).get("title", "")))
        cursor = data.get("next_cursor")
        if not data.get("has_more"):
            break
    return out


def _db_rows(client: httpx.Client, db_id: str) -> List[dict]:
    rows, cursor = [], None
    while True:
        body: Dict[str, object] = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = client.post(f"https://api.notion.com/v1/databases/{db_id}/query",
                        headers=_headers(), json=body)
        if r.status_code != 200:
            break
        data = r.json()
        rows.extend(data.get("results", []))
        if len(rows) >= _MAX_ROWS or not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return rows[:_MAX_ROWS]


def collect() -> List[dict]:
    """노션 트리를 훑어 제품별 레코드를 만든다.

    ⚠️ 라인 이름은 **한 단계 위 행 제목**에서 온다 (`히알루-테카 (New)`).
       제품 페이지 자신은 라인을 모른다.
    """
    records: List[dict] = []
    with httpx.Client(timeout=40) as client:
        for db_id, _ in _child_databases(client, ROOT_PAGE_ID):
            for line_row in _db_rows(client, db_id):          # depth 2 — 라인
                line_name = _title_of(line_row).strip()
                for inner_id, _t in _child_databases(client, line_row["id"]):
                    for prod in _db_rows(client, inner_id):    # depth 4 — 제품
                        name = _title_of(prod).strip()
                        body = _block_text(client, prod["id"])
                        if not body.strip():
                            continue
                        records.append({
                            "page_id": prod["id"].replace("-", ""),
                            "line": line_name,
                            "product": name,
                            "body": body,
                            "url": prod.get("url", ""),
                        })
    return records


def sync(dry_run: bool = False) -> dict:
    """노션 → MariaDB 적재.

    ⛔ **0건이면 기존 데이터를 지우지 않는다.** 권한이 끊기거나 구조가 바뀌면 0건이
       오는데, 그걸로 덮으면 제품정보가 **조용히 사라진다** (OP 재고·CS 캐시와 같은 규칙).
    """
    ensure_table()
    records = collect()
    stats = {"collected": len(records), "written": 0, "skipped_empty": 0}
    if not records:
        logger.warning("product_info_empty_keep_old")
        stats["kept_old"] = True
        return stats
    if dry_run:
        return stats

    from app.db.mariadb import execute
    now = datetime.now().replace(microsecond=0)
    for r in records:
        execute(
            "INSERT INTO product_info (page_id, line, product, body, url, synced_at) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE line=VALUES(line), product=VALUES(product), "
            "body=VALUES(body), url=VALUES(url), synced_at=VALUES(synced_at)",
            (r["page_id"], r["line"][:120], r["product"][:300], r["body"],
             r["url"][:400], now))
        stats["written"] += 1
    # ⚠️ 이번 적재에 없던 행은 지운다 — 다만 **이번 적재가 성공했을 때만** 여기 온다.
    #    `microsecond=0` 은 MariaDB DATETIME 이 초 단위라서다 (OP 재고에서 겪은 함정).
    execute("DELETE FROM product_info WHERE synced_at < %s", (now,))
    logger.info("product_info_synced", **stats)
    return stats


def search(query: str, limit: int = 6) -> List[dict]:
    """제품정보 검색 — 라인/제품명 우선, 없으면 본문 낱말.

    ⚠️ 라인 판정은 `product_lines` 를 쓴다 (프롬프트 표가 단일 소스).
    """
    from app.db.mariadb import fetch_all

    q = (query or "").strip()
    if not q:
        return []
    rows: List[dict] = []
    try:
        from app.core import product_lines
        asked = product_lines.mentioned(q)
        if asked:
            all_rows = fetch_all("SELECT line, product, body, url FROM product_info") or []
            rows = [r for r in all_rows
                    if product_lines.mentioned(f"{r['line']} {r['product']}") & asked]
    except Exception as e:
        logger.info("product_info_line_filter_failed", error=str(e)[:120])

    if not rows:
        from app.core.query_keywords import extract
        words = [w for w in extract(q) if len(w) >= 2][:4]
        if words:
            where = " AND ".join(["(product LIKE %s OR body LIKE %s)"] * len(words))
            params = []
            for w in words:
                params += [f"%{w}%", f"%{w}%"]
            rows = fetch_all(
                f"SELECT line, product, body, url FROM product_info WHERE {where} "
                f"LIMIT {int(limit) * 3}", tuple(params)) or []
    return rows[:limit]


def status() -> dict:
    from app.db.mariadb import fetch_one

    try:
        # ⚠️ `lines` 는 MariaDB **예약어**다 — 별칭으로 쓰면 1064 문법 오류가 나고
        #    `except` 가 삼켜 **count 0** 으로 보인다 (조용한 오작동, 2026-09-04 실측)
        row = fetch_one("SELECT COUNT(*) c, MAX(synced_at) t, "
                        "COUNT(DISTINCT line) line_count FROM product_info") or {}
    except Exception as e:
        # ⛔ 조용히 0 을 주면 "데이터가 없다" 로 읽힌다 — 흔적을 남긴다
        logger.warning("product_info_status_failed", error=str(e)[:160])
        return {"count": 0, "synced_at": None, "lines": 0, "error": str(e)[:120]}
    return {"count": int(row.get("c") or 0), "synced_at": row.get("t"),
            "lines": int(row.get("line_count") or 0)}
