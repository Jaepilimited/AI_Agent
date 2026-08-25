# -*- coding: utf-8 -*-
"""컬럼 DISTINCT 값을 **데이터에서 직접** 가져와 프롬프트에 넣는다.

⛔ 프롬프트에 값 목록을 손으로 적으면 **반드시 낡는다.** 오늘 하루에만 세 번 겪었다:

    에콰도르   191개 중 12개만 나열하고 "등" → LLM 이 전체로 읽고 "없는 국가" 라 답함
    메가와리   2026 Q2 가 표에 없어 날짜를 지어냄 (40.2억 / 실제 62.2억)
    Continent1 `남미`·`중미` 가 **`중남미`로 통합**됐는데 프롬프트만 옛 값을 들고 있다
               → "남미 매출" 은 0건이 난다 (2026-08-18 실측: 남미·중미 0행)

   `prompts/sql_generator.txt` 1,573줄 중 **23%가 이런 값 목록과 스키마 표**다.
   전부 BigQuery 에 이미 있는 사실을 베껴 적은 것이다.

**방식**: 프롬프트에 `{{VALUES:이름}}` 자리표시자를 두고, 로드할 때 실측 목록으로
채운다. 조회는 매일 한 번 캐시한다(질문마다 BigQuery 를 때리지 않는다).

⚠️ 조회가 실패하면 **자리표시자를 비우지 않고** 마지막 캐시를 쓴다. 캐시도 없으면
   그 줄만 빠진다 — 목록이 통째로 사라져 LLM 이 값을 지어내는 것보다 낫다.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import structlog

from app.db.mariadb import execute, fetch_one

logger = structlog.get_logger(__name__)

_SALES = "skin1004-319714.Sales_Integration.SALES_ALL_Backup"
_AD = "skin1004-319714.marketing_analysis.integrated_ad"

# 자리표시자 이름 → (테이블, 컬럼, 최대 개수, 설명)
# ⚠️ **고카디널리티 컬럼은 넣지 마라** — 제품명·거래처는 수천 개라 프롬프트가 터진다
REGISTRY: Dict[str, tuple] = {
    "Country":     (_SALES, "Country", 300, "한국어 국가명"),
    "Continent1":  (_SALES, "Continent1", 50, "광역 대륙 (우선 사용)"),
    "Continent2":  (_SALES, "Continent2", 50, "세부 권역"),
    "Line":        (_SALES, "Line", 60, "제품 라인"),
    "Category":    (_SALES, "Category", 40, "제품 카테고리"),
    "Team_NEW":    (_SALES, "Team_NEW", 40, "팀 코드"),
    "Brand":       (_SALES, "Brand", 20, "브랜드 코드"),
    "Sales_Type":  (_SALES, "Sales_Type", 10, "B2B / B2C"),
    "AdMedia":     (_AD, "media", 60, "광고 매체"),
    "AdCountry":   (_AD, "country", 300, "광고 국가 (매출과 같은 한글명)"),
}

_TTL_HOURS = 26          # 하루 한 번 + 여유
_DDL = """
CREATE TABLE IF NOT EXISTS bq_value_cache (
    name VARCHAR(64) PRIMARY KEY,
    payload MEDIUMTEXT NOT NULL,
    n INT NOT NULL DEFAULT 0,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def ensure_value_cache_table() -> None:
    try:
        execute(_DDL)
    except Exception as e:
        logger.warning("value_cache_table_error", error=str(e)[:160])


def _fetch_live(name: str) -> Optional[List[str]]:
    table, col, cap, _ = REGISTRY[name]
    from app.core.bigquery import get_bigquery_client
    rows = get_bigquery_client().execute_query(
        f"SELECT DISTINCT `{col}` AS v FROM `{table}` "
        f"WHERE `{col}` IS NOT NULL AND CAST(`{col}` AS STRING) != '' "
        f"ORDER BY v LIMIT {int(cap) + 1}") or []
    vals = [str(r["v"]) for r in rows if r.get("v") is not None]
    if len(vals) > cap:
        # 상한을 넘으면 목록을 싣지 않는다 — 잘린 목록은 "전체" 로 오해된다
        logger.warning("value_list_too_many", name=name, count=len(vals), cap=cap)
        return None
    return vals


# 캐시가 아직 없을 때 쓰는 최소 씨앗. ⚠️ **전체 목록이 아니다** — 갱신 전까지
# 판정이 통째로 죽는 것만 막는 용도다 (테스트·첫 기동).
_SEED: Dict[str, List[str]] = {
    "Continent1": ["CIS", "글로벌", "기타", "북미", "아시아", "아프리카",
                   "오세아니아", "유럽", "중남미", "중동"],
}


def _cached(name: str) -> Optional[List[str]]:
    try:
        row = fetch_one("SELECT payload FROM bq_value_cache WHERE name = %s", (name,))
    except Exception:
        row = None
    if row:
        try:
            return json.loads(row["payload"])
        except Exception:
            pass
    return _SEED.get(name)


def values(name: str) -> Optional[List[str]]:
    """실측된 값 목록 (캐시 → 씨앗). 모르면 None — 추측하지 않는다."""
    return _cached(name)


def refresh(name: Optional[str] = None) -> Dict[str, int]:
    """실측 → 캐시. 이름을 주면 그것만, 없으면 전체."""
    ensure_value_cache_table()
    names = [name] if name else list(REGISTRY)
    out: Dict[str, int] = {}
    for n in names:
        try:
            vals = _fetch_live(n)
        except Exception as e:
            logger.warning("value_list_fetch_failed", name=n, error=str(e)[:140])
            continue
        if vals is None:
            continue
        execute(
            "INSERT INTO bq_value_cache (name, payload, n) VALUES (%s, %s, %s) "
            "ON DUPLICATE KEY UPDATE payload = VALUES(payload), n = VALUES(n), "
            "updated_at = NOW()",
            (n, json.dumps(vals, ensure_ascii=False), len(vals)))
        out[n] = len(vals)
    logger.info("value_lists_refreshed", **out)
    return out


def _is_stale() -> bool:
    row = fetch_one("SELECT MAX(updated_at) t FROM bq_value_cache")
    t = (row or {}).get("t")
    return (not t) or (datetime.now() - t > timedelta(hours=_TTL_HOURS))


def render(name: str) -> str:
    """자리표시자 하나를 실측 목록 문장으로. 실패하면 빈 문자열."""
    vals = _cached(name)
    if not vals:
        return ""
    _, col, _, desc = REGISTRY[name]
    return (f"**{col} 실제 값 ({desc} · {len(vals)}개, 매일 자동 갱신)**: "
            + ", ".join(vals))


def fill(text: str) -> str:
    """프롬프트의 `{{VALUES:이름}}` 을 실측 목록으로 채운다.

    ⚠️ 캐시가 없거나 낡아도 **여기서 BigQuery 를 부르지 않는다** — 질문 경로가
       느려지고, 실패하면 프롬프트가 통째로 흔들린다. 갱신은 배치가 한다.
    """
    import re

    def _sub(m):
        name = m.group(1)
        if name not in REGISTRY:
            logger.warning("value_list_unknown_placeholder", name=name)
            return ""
        out = render(name)
        if not out:
            logger.warning("value_list_missing", name=name)
        return out

    return re.sub(r"\{\{VALUES:(\w+)\}\}", _sub, text or "")


def status() -> Dict[str, object]:
    """자가 점검용 — 캐시가 최신인가, 빠진 항목은 없는가."""
    ensure_value_cache_table()
    missing = [n for n in REGISTRY if not _cached(n)]
    row = fetch_one("SELECT MAX(updated_at) t, COUNT(*) c FROM bq_value_cache") or {}
    return {"total": len(REGISTRY), "cached": int(row.get("c") or 0),
            "missing": missing, "updated_at": str(row.get("t") or ""),
            "stale": _is_stale()}
