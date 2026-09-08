# -*- coding: utf-8 -*-
"""대표 제품 목록을 **실측으로** 만든다 — 손으로 적은 목록은 반드시 낡는다.

⛔ **왜 만들었나** (2026-09-03 사용자 제보: *"왜 최신데이터가 반영이 안되어있지?
   센텔라 테카같은거 말이야"*):

       질문: "센텔라 테카 앰플이 뭐야?"
       답변: "'센텔라 테카 앰플'이라는 정확한 제품명은 **공식 제품 목록에서
              확인되지 않습니다.** 아마 마다가스카르 센텔라 100 앰플을 …"

   `SK_Centella_Teca_Ampoule_50ml` 은 **2026-01-16 출시 · 361,315개 판매**된
   주력 제품이다. BigQuery·전성분(8건)·매핑(15건)·CS 자료(39건)에 **전부 있었다.**
   없는 것은 direct 프롬프트에 손으로 적어 둔 `## 대표 제품` 목록뿐이었고,
   거기에 *"⛔ 제품명 창작 금지 — 위 목록에 없는 제품명을 임의로 조합하지 마세요"*
   가 붙어 있어 **없는 제품이라고 더 강하게 단정했다.**

   ⚠️ 데이터가 낡은 것이 아니라 **프롬프트가 낡았다.** 그런데 사용자에게는
      "최신 데이터가 반영 안 됨" 으로 보인다 — 구분이 안 되는 형태의 조용한 오답이다.

**같은 계열**: `{{VALUES:...}}`(값 목록) · `build_team_section()`(팀 표) ·
`product_lines`(라인 어휘). 목록을 손으로 적지 않는 것이 이 프로젝트의 규칙이다.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import List, Optional

import structlog

logger = structlog.get_logger(__name__)

_PRODUCT_TABLE = "skin1004-319714.Sales_Integration.Product"
_CACHE_NAME = "TopProducts"      # bq_value_cache 를 재사용한다
_TTL_HOURS = 26                  # 하루 한 번 + 여유
_MONTHS = 12                     # 최근 1년 판매 기준
_PER_LINE = 4                    # 라인당 상위 N 종 — 넓히면 프롬프트가 붓는다

# ⚠️ 세트·키트·샘플은 대표 제품이 아니다. 이름으로 거른다 (실측: `Travel_Kit`,
#    `Double_Cleansing_Duo`, `*_Sachet`, `*_twin_pack` 이 상위에 섞여 온다)
_EXCLUDE_TOKENS = ("_kit", "_duo", "sachet", "twin_pack", "_set", "sample", "tester",
                   # 부자재·굿즈는 대표 제품이 아니다 (실측: 파우치·코튼패드·쇼핑백)
                   "pouch", "cotton_pad", "shopping", "brochure", "sticker")
# ⚠️ 라인이 아니라 **잔여 버킷**인 값 — 여기 담긴 것은 대표 제품으로 소개할 수 없다
_EXCLUDE_LINES = {"others", "set", "etc", "기타", ""}


def _looks_like_a_set(product: str) -> bool:
    low = (product or "").lower()
    # ⚠️ 제품명 자체가 `Others` 인 행이 실제로 있다 (실측) — 라인 이름이 그대로 들어온 것
    if low in ("others", "etc", "기타", "-"):
        return True
    return any(tok in low for tok in _EXCLUDE_TOKENS)


def _fetch_live() -> List[dict]:
    from app.core.bigquery import get_bigquery_client

    sql = f"""
    SELECT Line AS line, Product AS product, SUM(Total_Qty) AS qty,
           MIN(Date) AS first_sold
    FROM `{_PRODUCT_TABLE}`
    WHERE Date >= DATE_SUB(CURRENT_DATE(), INTERVAL {_MONTHS} MONTH)
      AND Product IS NOT NULL AND TRIM(Product) != ''
      AND Line IS NOT NULL AND TRIM(Line) != ''
    GROUP BY line, product
    ORDER BY qty DESC
    LIMIT 400
    """
    rows = get_bigquery_client().execute_query(sql, timeout=90.0, max_rows=400) or []
    by_line: dict = {}
    for r in rows:
        product = str(r.get("product") or "")
        if _looks_like_a_set(product):
            continue
        line = str(r.get("line") or "")
        if line.strip().lower() in _EXCLUDE_LINES:
            continue
        bucket = by_line.setdefault(line, [])
        if len(bucket) < _PER_LINE:
            bucket.append({
                "product": product,
                "qty": int(r.get("qty") or 0),
                "first_sold": str(r.get("first_sold") or "")[:10],
            })
    # 라인은 그 라인 최상위 제품의 판매량 순으로 세운다
    out = []
    for line, items in by_line.items():
        out.append({"line": line, "items": items,
                    "qty": max((i["qty"] for i in items), default=0)})
    out.sort(key=lambda x: -x["qty"])
    return out


def _cached() -> Optional[List[dict]]:
    from app.db.mariadb import fetch_one

    try:
        row = fetch_one("SELECT payload, updated_at FROM bq_value_cache WHERE name = %s",
                        (_CACHE_NAME,))
    except Exception as e:
        logger.warning("product_catalog_cache_read_failed", error=str(e)[:140])
        return None
    if not row or not row.get("payload"):
        return None
    try:
        return json.loads(row["payload"])
    except (TypeError, ValueError):
        return None


def refresh() -> int:
    """실측으로 다시 만들어 캐시에 넣는다. 잡이 부른다."""
    from app.core.value_lists import ensure_value_cache_table
    from app.db.mariadb import execute

    data = _fetch_live()
    if not data:
        # ⚠️ 0건은 성공이 아니다 — 빈 목록으로 덮으면 프롬프트에서 제품이 통째로 사라진다
        logger.warning("product_catalog_empty")
        return 0
    ensure_value_cache_table()
    n = sum(len(x["items"]) for x in data)
    execute(
        "INSERT INTO bq_value_cache (name, payload, n) VALUES (%s, %s, %s) "
        "ON DUPLICATE KEY UPDATE payload = VALUES(payload), n = VALUES(n), "
        "updated_at = CURRENT_TIMESTAMP",
        (_CACHE_NAME, json.dumps(data, ensure_ascii=False), n))
    logger.info("product_catalog_refreshed", lines=len(data), products=n)
    return n


def is_stale() -> bool:
    from app.db.mariadb import fetch_one

    try:
        row = fetch_one("SELECT updated_at FROM bq_value_cache WHERE name = %s",
                        (_CACHE_NAME,))
    except Exception:
        return True
    if not row or not row.get("updated_at"):
        return True
    return (datetime.now() - row["updated_at"]) > timedelta(hours=_TTL_HOURS)


def section() -> str:
    """direct 프롬프트에 넣을 `## 대표 제품` 블록.

    ⛔ **캐시가 없으면 목록을 지어내지 않고, 목록이 없다는 사실을 알린다.**
       빈 목록에 "이 목록에 없으면 없는 제품" 규칙을 붙이면 **모든 제품을
       부정하게 된다** — 원래 사고보다 나쁘다.
    """
    data = _cached()
    if not data:
        logger.warning("product_catalog_missing")
        return """## 대표 제품
⚠️ 제품 목록을 지금 불러오지 못했습니다. **제품명을 임의로 지어내지 말고**,
정확한 제품명·판매 현황은 데이터 조회(BigQuery)를 제안하세요.
⛔ 목록이 없다는 이유로 사용자가 말한 제품을 **"없는 제품"이라고 단정하지 마세요.**"""

    lines = ["## 대표 제품 (최근 12개월 판매 실측 — 매일 갱신)"]
    for entry in data:
        names = " · ".join(
            f"`{i['product']}`" + (" ⭐신제품" if i["first_sold"] >= _recent_cutoff() else "")
            for i in entry["items"])
        lines.append(f"- **{entry['line']}**: {names}")
    lines.append(
        "\n⛔ **제품명을 임의로 조합·생성하지 마세요.** 다만 위 목록은 **판매 상위만** "
        "담고 있습니다 — 여기 없다고 해서 **없는 제품이 아닙니다.** 사용자가 말한 "
        "제품이 목록에 없으면 *\"확인되지 않는다\"* 고 단정하지 말고, "
        "**데이터 조회로 확인하겠다고 안내**하세요.")
    return "\n".join(lines)


def _recent_cutoff() -> str:
    """신제품 표시 기준 — 첫 판매가 6개월 이내면 신제품 (프로젝트 정의와 같다)."""
    return (datetime.now() - timedelta(days=183)).strftime("%Y-%m-%d")
