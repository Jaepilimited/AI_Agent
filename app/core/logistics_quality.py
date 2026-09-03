# -*- coding: utf-8 -*-
"""수출 물류(`Export_control.export_logistics`) 데이터 품질 — 수량 이상치 공시.

⛔ **왜 코드인가** (2026-09-03 프로덕션 실측):

    프롬프트에 *"한 선적의 실측 최대는 1,332,166개다. 억 단위가 나오면 이상치가
    섞였다고 답변에 적어라"* 를 넣고 배포한 **뒤에도**, 같은 질문에 이렇게 답했다:

        "2026년 코스타리카 대상 수출 물류 수량은 총 **202,602,265,193개**입니다.
         압도적인 수출 규모: 코스타리카는 주요 수출 대상국으로서의 입지를 …"

    프롬프트는 확률이고 보증은 코드다 — FI 마스킹·내부 경로 마스킹과 같은 사상.

**무엇이 깨졌나**: `quantity_ea` 칸에 주문번호(`YYYYMMDDnnnn`)를 적은 행이 있다.

    2026-01-14 · 코스타리카 · order_number=202601120126 · quantity_ea=202602190028
    · weight_kg=1,265.75  →  160,064,934 ea/kg (정상 중앙값 6.7)

    그 한 행이 2026년 총계를 **58,038,175 → 202,660,228,203 (3,492배)** 로 만든다.

⛔ **고치는 것은 우리가 아니다 — 원본(물류관리 시스템)의 셀**이다. 여기서는
   **값을 건드리지 않고**, 그 행이 살아 있는 동안 답변에 사실을 붙이기만 한다.
   ⚠️ 그래서 이 공시는 **스스로 꺼진다** — 원본을 고치면 조회 결과가 비고 공시도
      사라진다. 영원히 뜨는 경고가 아니다 (매일 뜨는 경고는 곧 아무도 안 읽는다).
"""
from __future__ import annotations

import re
import time
from typing import List, Optional

import structlog

logger = structlog.get_logger(__name__)

TABLE = "skin1004-319714.Export_control.export_logistics"

# ⚠️ 임계는 **실측 최대에서 크게 떨어뜨렸다** — 좁히면 정상 대량 선적이 걸린다.
#    한 선적 실측 최대 1,332,166ea (인도네시아, 242톤) → 1,000만은 7.5배 여유.
#    ea/kg 실측 최대 392 · 중앙값 6.7 → 10,000은 25배 여유.
_MAX_PLAUSIBLE_EA = 10_000_000
_MAX_PLAUSIBLE_EA_PER_KG = 10_000

_CACHE_TTL_SECONDS = 3600      # 질문 경로에서 BigQuery 를 매번 때리지 않는다
_cache: Optional[List[dict]] = None
_cache_at: float = 0.0


def outlier_rows(force: bool = False) -> List[dict]:
    """수량 칸이 물리적으로 불가능한 행. 조회는 1시간에 한 번만 한다.

    ⚠️ 조회가 실패하면 **빈 목록**을 돌려준다 — 공시를 못 붙이는 것이,
       조회 실패 때문에 멀쩡한 답변에 경고를 붙이는 것보다 낫다.
    """
    global _cache, _cache_at
    now = time.time()
    if not force and _cache is not None and (now - _cache_at) < _CACHE_TTL_SECONDS:
        return _cache
    try:
        from app.core.bigquery import get_bigquery_client
        rows = get_bigquery_client().execute_query(
            "SELECT order_date, country, order_number, quantity_ea, weight_kg "
            f"FROM `{TABLE}` "
            "WHERE NOT is_deleted AND ("
            f"  quantity_ea > {_MAX_PLAUSIBLE_EA}"
            "  OR (weight_kg > 0 AND SAFE_DIVIDE(quantity_ea, weight_kg) >"
            f"      {_MAX_PLAUSIBLE_EA_PER_KG})) "
            "ORDER BY quantity_ea DESC LIMIT 20",
            timeout=60.0, max_rows=20) or []
    except Exception as e:
        logger.warning("logistics_outlier_probe_failed", error=str(e)[:160])
        return _cache if _cache is not None else []
    _cache, _cache_at = list(rows), now
    return _cache


# SQL 이 이 테이블의 수량을 집계하는가. 컬럼 이름으로 본다 —
# ⚠️ `quantity`(옛 사본 컬럼)도 같은 값이라 함께 본다.
_re_qty = re.compile(r"\b(quantity_ea|quantity)\b", re.IGNORECASE)


def touches_logistics_quantity(sql: str) -> bool:
    if not sql:
        return False
    normalized = re.sub(r"[`\"]", "", sql).lower()
    return "export_control.export_logistics" in normalized and bool(_re_qty.search(normalized))


def notice_for_sql(sql: str) -> str:
    """수량 이상치가 살아 있는 동안만 **답변 맨 앞에** 붙는 한 줄.

    ⛔ **값을 고치거나 행을 빼지 않는다.** 조용히 바꾸면 사용자는 무엇이 달라졌는지
       영영 모른다 — 사실을 적고 판단을 사람에게 넘긴다.
    """
    if not touches_logistics_quantity(sql):
        return ""
    rows = outlier_rows()
    if not rows:
        return ""
    worst = rows[0]
    try:
        qty = f"{int(worst.get('quantity_ea') or 0):,}"
    except (TypeError, ValueError):
        qty = str(worst.get("quantity_ea"))
    where = " · ".join(
        str(worst.get(k)) for k in ("order_date", "country", "order_number")
        if worst.get(k) is not None)
    # ⛔ **표보다 먼저 말한다.** 각주로 달아 두는 것만으로는 부족하다 — 사람은
    #    표를 보지 각주를 안 본다 (OP 재고 신선도 공시와 같은 규칙). 실제로 이
    #    공시를 맨 뒤에 붙였더니 후속 질문 제안·실행된 쿼리 뒤로 밀려 났다.
    return (
        "> ⚠️ **아래 수량은 그대로 믿을 수 없습니다.** 수출 물류 데이터의 수량 칸에"
        f" 주문번호로 보이는 값이 들어간 행이 **{len(rows)}건** 있습니다"
        f" ({where} · {qty}ea). 한 선적의 실제 최대는 **1,332,166개**입니다 —"
        " 이 행이 포함된 합계는 크게 부풀려집니다."
        " 원본(물류관리 시스템)에서 해당 값을 고쳐야 하며, 그 전까지는 국가·기간을"
        " 좁혀 조회하시거나 건수 기준으로 보시는 편이 안전합니다.\n\n"
    )
