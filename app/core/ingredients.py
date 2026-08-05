"""제품 전성분 — 스프레드시트 적재 + 성분 기준 제품 조회.

배경:
    "나이아신아마이드가 안 들어간 제품 판매량 순위" 질문에 **해당 성분이 든 제품이
    1위**로 나오는 오답이 있었다 (노션 AI Tester 제보). 원인은 SQL 이 제품명 문자열
    매칭(`LIKE '%RETINOL%'`)으로 성분을 판단한 것 — 제품명에 성분이 안 적힌 제품이
    전부 "미포함"으로 분류됐다.

    전성분 데이터는 BigQuery 에는 없고 **사내 스프레드시트**에 있다
    (`01. 제품정보_내수통합용(품목기준)` 탭, AG=전성분 KR / AH=전성분 EN).

핵심 원칙 — **"성분 미상"과 "성분 미포함"을 절대 섞지 않는다**:
    시트에 없는 제품(Sachet·기획세트 등)은 성분을 *모르는* 것이지 *안 들어간* 것이
    아니다. 이 둘을 뭉개면 처음 사고가 그대로 재현된다. 그래서 조회 결과를 항상
    matched / unknown 으로 나눠서 돌려주고, 답변에 커버리지를 명시한다.

커버리지 (2026-08-05 실측):
    제품 종수 기준 113/241 (46.9%) — 미매칭은 대부분 Sachet·Kit·Set 등 파생 SKU
    **판매수량 기준 89.7%** — 실사용 관점에서는 대부분을 덮는다
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

import structlog

from app.db.mariadb import execute, execute_lastid, fetch_all, fetch_one

logger = structlog.get_logger(__name__)

SPREADSHEET_ID = "11gX_Gg7JkGQ2GoLeeU5MXZ-qTieZPL_Fu3w7lPuHW7A"
SHEET_TAB = "01. 제품정보_내수통합용(품목기준)"
HEADER_ROW = 5          # 실제 헤더 (No./Brand/Series/구분/Line/제품명…)
DATA_START_ROW = 7      # 데이터 시작

# 시트 열 인덱스 (0-based)
_COL = {
    "brand": 1, "line": 4, "name_kr": 5, "name_en": 6, "size": 11,
    "active_kr": 14, "active_en": 15, "key_ing_kr": 16, "key_ing_en": 17,
    "ing_kr": 32,   # AG — 2025년 7월 이후 전성분 (최신)
    "ing_en": 33,   # AH
}

_DDL_INGREDIENTS = """
CREATE TABLE IF NOT EXISTS product_ingredients (
    id INT AUTO_INCREMENT PRIMARY KEY,
    norm_key VARCHAR(190) NOT NULL,
    name_en VARCHAR(400),
    name_kr VARCHAR(400),
    brand VARCHAR(100),
    line VARCHAR(100),
    size VARCHAR(60),
    ingredients_kr MEDIUMTEXT,
    ingredients_en MEDIUMTEXT,
    active_kr VARCHAR(600),
    key_ingredients TEXT,
    synced_at DATETIME NOT NULL,
    INDEX idx_norm (norm_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_DDL_MAP = """
CREATE TABLE IF NOT EXISTS product_ingredient_map (
    bq_product VARCHAR(190) NOT NULL PRIMARY KEY,
    ingredient_id INT NOT NULL,
    synced_at DATETIME NOT NULL,
    INDEX idx_ing (ingredient_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def ensure_ingredient_tables() -> None:
    for ddl in (_DDL_INGREDIENTS, _DDL_MAP):
        try:
            execute(ddl)
        except Exception as e:
            logger.debug("ingredient_ddl_skip", error=str(e)[:120])


# ── 이름 정규화 ────────────────────────────────────────────────────────────────
# 시트는 "SKIN1004 Madagascar Centella Light Cleansing Oil",
# BigQuery 는 "SK_Centella_Light_Cleansing_Oil_300ml" 형태다. 브랜드 접두어와
# 용량을 걷어내고 영숫자만 남기면 같은 키가 된다.

_NOISE_TOKENS = {
    "skin1004", "skin", "1004", "madagascar", "commonlabs", "common", "labs",
    "sk", "cl", "cbt", "um", "the", "and", "line",
}
_SIZE_RE = re.compile(r"\d+\s*(ml|g|kg|ea|매|개|정|호|p)\b", re.IGNORECASE)


def normalize_name(s: str) -> str:
    s = (s or "").lower()
    s = _SIZE_RE.sub(" ", s)
    s = re.sub(r"[^a-z0-9가-힣]+", " ", s)
    return "".join(t for t in s.split() if t and t not in _NOISE_TOKENS)


# ── 적재 ──────────────────────────────────────────────────────────────────────


def _read_sheet() -> list[dict]:
    """스프레드시트에서 제품·전성분을 읽는다."""
    import os

    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_service_account_file(
        os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
    rows = (
        svc.spreadsheets()
        .values()
        .get(spreadsheetId=SPREADSHEET_ID,
             range=f"'{SHEET_TAB}'!A{DATA_START_ROW}:AH400")
        .execute()
        .get("values", [])
    )

    def cell(r, key):
        i = _COL[key]
        return str(r[i]).strip() if len(r) > i else ""

    out = []
    for r in rows:
        name_en, name_kr = cell(r, "name_en"), cell(r, "name_kr")
        if not (name_en or name_kr):
            continue
        key = normalize_name(name_en) or normalize_name(name_kr)
        if not key:
            continue
        out.append({
            "norm_key": key[:190],
            "name_en": name_en[:400], "name_kr": name_kr[:400],
            "brand": cell(r, "brand")[:100], "line": cell(r, "line")[:100],
            "size": cell(r, "size")[:60],
            "ingredients_kr": cell(r, "ing_kr"), "ingredients_en": cell(r, "ing_en"),
            "active_kr": cell(r, "active_kr")[:600],
            "key_ingredients": cell(r, "key_ing_kr")[:2000],
        })
    return out


def _bq_products() -> list[str]:
    from app.core.bigquery import get_bigquery_client

    bq = get_bigquery_client()
    rows = bq.execute_query(
        "SELECT DISTINCT Product FROM `skin1004-319714.Sales_Integration.Product` "
        "WHERE Date >= '2025-01-01' AND Product IS NOT NULL",
        timeout=180.0, max_rows=5000,
    )
    return [r["Product"] for r in rows if r.get("Product")]


def sync_ingredients(dry_run: bool = False) -> dict:
    """시트 → MariaDB 적재 + BigQuery 제품 매칭. 하루 1회 실행."""
    ensure_ingredient_tables()
    items = _read_sheet()
    products = _bq_products()
    now = datetime.now()

    # 정규화 키 → 시트 항목
    index: dict[str, dict] = {}
    for it in items:
        index.setdefault(it["norm_key"], it)

    # BQ 제품 매칭: 완전일치 → 부분포함(짧은 쪽이 12자 이상일 때만)
    matches: list[tuple[str, str]] = []
    for p in products:
        k = normalize_name(p)
        if not k:
            continue
        hit_key = k if k in index else None
        if not hit_key:
            for sk in index:
                if len(sk) >= 12 and (sk in k or k in sk):
                    hit_key = sk
                    break
        if hit_key:
            matches.append((p, hit_key))

    stats = {
        "sheet_products": len(items),
        "with_ingredients": sum(1 for i in items if i["ingredients_kr"]),
        "bq_products": len(products),
        "matched": len(matches),
        "unmatched": len(products) - len(matches),
    }
    if dry_run:
        return stats

    execute("DELETE FROM product_ingredient_map")
    execute("DELETE FROM product_ingredients")
    key_to_id: dict[str, int] = {}
    for it in items:
        rid = execute_lastid(
            "INSERT INTO product_ingredients (norm_key, name_en, name_kr, brand, line, size, "
            "ingredients_kr, ingredients_en, active_kr, key_ingredients, synced_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (it["norm_key"], it["name_en"], it["name_kr"], it["brand"], it["line"],
             it["size"], it["ingredients_kr"], it["ingredients_en"],
             it["active_kr"], it["key_ingredients"], now),
        )
        key_to_id.setdefault(it["norm_key"], rid)

    for p, k in matches:
        rid = key_to_id.get(k)
        if rid:
            try:
                execute(
                    "INSERT INTO product_ingredient_map (bq_product, ingredient_id, synced_at) "
                    "VALUES (%s,%s,%s) ON DUPLICATE KEY UPDATE ingredient_id=VALUES(ingredient_id), "
                    "synced_at=VALUES(synced_at)",
                    (p[:190], rid, now),
                )
            except Exception as e:
                logger.warning("ingredient_map_insert_failed", product=p[:60], error=str(e)[:100])

    logger.info("ingredients_synced", **stats)
    return stats


# ── 조회 ──────────────────────────────────────────────────────────────────────


def resolve_products_by_ingredient(ingredient: str, contains: bool) -> dict:
    """성분 기준으로 BigQuery 제품명을 갈라낸다.

    Returns:
        {"products": [...], "unknown_count": int, "total_known": int, "coverage_note": str}

        `products` 는 조건을 만족하는 **성분이 확인된** 제품만이다.
        성분을 모르는 제품(시트 미등록 SKU)은 `products` 에 절대 넣지 않는다 —
        미상을 미포함으로 취급하는 순간 원래 오답이 재현되기 때문이다.
    """
    ensure_ingredient_tables()
    term = (ingredient or "").strip()
    if not term:
        return {"products": [], "unknown_count": 0, "total_known": 0, "coverage_note": ""}

    like = f"%{term}%"
    if contains:
        rows = fetch_all(
            "SELECT m.bq_product FROM product_ingredient_map m "
            "JOIN product_ingredients p ON p.id = m.ingredient_id "
            "WHERE p.ingredients_kr LIKE %s OR p.ingredients_en LIKE %s",
            (like, like),
        )
    else:
        rows = fetch_all(
            "SELECT m.bq_product FROM product_ingredient_map m "
            "JOIN product_ingredients p ON p.id = m.ingredient_id "
            "WHERE COALESCE(p.ingredients_kr,'') <> '' "
            "  AND p.ingredients_kr NOT LIKE %s AND COALESCE(p.ingredients_en,'') NOT LIKE %s",
            (like, like),
        )
    products = [r["bq_product"] for r in rows]

    known = fetch_one("SELECT COUNT(*) c FROM product_ingredient_map") or {}
    total_known = known.get("c", 0)
    return {
        "products": products,
        "total_known": total_known,
        "coverage_note": (
            f"전성분이 확인된 {total_known}개 제품 기준입니다. "
            "샘플(Sachet)·기획세트 등 성분 정보가 없는 품목은 제외했습니다 — "
            "성분을 *모르는* 것이지 들어있지 않다는 뜻이 아닙니다."
        ),
    }


def get_ingredient_status() -> dict:
    """적재 현황 (자가 점검·관리 화면용)."""
    ensure_ingredient_tables()
    a = fetch_one("SELECT COUNT(*) c, MAX(synced_at) m FROM product_ingredients") or {}
    b = fetch_one("SELECT COUNT(*) c FROM product_ingredient_map") or {}
    return {
        "products": a.get("c", 0),
        "mapped_bq_products": b.get("c", 0),
        "synced_at": a.get("m"),
    }
