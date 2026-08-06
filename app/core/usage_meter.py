"""LLM·BigQuery 사용량 계측 — "운영 비용이 얼마인가"에 숫자로 답하기 위한 것.

배경 (2026-08-06 운영본부 월간회의):
    "AI Agent 운영에 필요한 비용은 얼마이며, 그 비용 대비 어떤 가치를 제공하는가?"
    — 지금까지는 토큰 사용량을 기록하지 않아 이 질문에 추정으로만 답할 수 있었다.

무엇을 재는가:
    - LLM 호출마다: provider/model/메서드, 입력·출력 토큰, 캐시 읽기/쓰기 토큰
    - BigQuery 쿼리마다: 청구 바이트(total_bytes_billed)
    기록은 백그라운드 스레드 — 사용자 응답 경로를 절대 늦추지 않고,
    기록 실패는 서비스에 영향을 주지 않는다(조용히 warning 로그만).

비용 계산:
    토큰은 원본 그대로 저장하고, 비용은 조회 시점에 요율표로 계산한다 —
    요율이 바뀌어도 과거 기록에 소급 적용된다.
    ⚠️ _PRICING 은 참고 요율이다. 실제 청구서와 대조해 보정할 것.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Optional

import structlog

from app.db.mariadb import execute, fetch_all

logger = structlog.get_logger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS llm_usage (
    id INT AUTO_INCREMENT PRIMARY KEY,
    ts DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    provider VARCHAR(20) NOT NULL,
    model VARCHAR(100) NOT NULL DEFAULT '',
    method VARCHAR(50) NOT NULL DEFAULT '',
    input_tokens BIGINT NOT NULL DEFAULT 0,
    output_tokens BIGINT NOT NULL DEFAULT 0,
    cache_read_tokens BIGINT NOT NULL DEFAULT 0,
    cache_write_tokens BIGINT NOT NULL DEFAULT 0,
    extra_units BIGINT NOT NULL DEFAULT 0,
    INDEX idx_ts (ts),
    INDEX idx_provider (provider, ts)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

# USD / 1M tokens. ⚠️ 참고 요율 — 실제 청구서와 대조해 보정할 것.
# 키는 모델명 부분 문자열 매칭 (긴 것 우선).
_PRICING = [
    # (모델명 조각, 입력 $/1M, 출력 $/1M)
    ("opus", 15.0, 75.0),
    ("sonnet", 3.0, 15.0),
    ("haiku", 0.8, 4.0),
    ("fable", 15.0, 75.0),
    ("gemini-3.5-flash", 0.30, 2.50),
    ("flash", 0.30, 2.50),
    ("gemini-3.1-pro", 1.25, 10.0),
    ("pro", 1.25, 10.0),
]
_CACHE_READ_FACTOR = 0.1    # 캐시 읽기 = 입력 요율의 10%
_CACHE_WRITE_FACTOR = 1.25  # 캐시 쓰기 = 입력 요율의 125%
_BQ_USD_PER_TIB = 6.25      # BigQuery on-demand
_USD_KRW = 1380.0           # ⚠️ 대략 환율 — 리포트 표시용


def ensure_usage_table() -> None:
    try:
        execute(_DDL)
    except Exception as e:
        logger.debug("usage_ddl_skip", error=str(e)[:120])


def _insert(provider: str, model: str, method: str,
            in_t: int, out_t: int, cr: int, cw: int, extra: int) -> None:
    try:
        execute(
            "INSERT INTO llm_usage (provider, model, method, input_tokens, output_tokens, "
            "cache_read_tokens, cache_write_tokens, extra_units) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (provider[:20], (model or "")[:100], (method or "")[:50],
             int(in_t or 0), int(out_t or 0), int(cr or 0), int(cw or 0), int(extra or 0)),
        )
    except Exception as e:
        logger.warning("usage_record_failed", provider=provider, error=str(e)[:120])


def _record_async(*args) -> None:
    threading.Thread(target=_insert, args=args, daemon=True).start()


def record_claude(model: str, method: str, usage) -> None:
    """Anthropic SDK 의 response.usage / stream 최종 메시지 usage 를 기록."""
    if usage is None:
        return
    _record_async(
        "claude", model, method,
        getattr(usage, "input_tokens", 0), getattr(usage, "output_tokens", 0),
        getattr(usage, "cache_read_input_tokens", 0) or 0,
        getattr(usage, "cache_creation_input_tokens", 0) or 0, 0,
    )


def record_gemini(model: str, method: str, usage_metadata) -> None:
    """google-genai SDK 의 response.usage_metadata 를 기록."""
    if usage_metadata is None:
        return
    _record_async(
        "gemini", model, method,
        getattr(usage_metadata, "prompt_token_count", 0) or 0,
        (getattr(usage_metadata, "candidates_token_count", 0) or 0)
        + (getattr(usage_metadata, "thoughts_token_count", 0) or 0),
        getattr(usage_metadata, "cached_content_token_count", 0) or 0, 0, 0,
    )


def record_bigquery(bytes_billed: Optional[int]) -> None:
    if not bytes_billed:
        return
    _record_async("bigquery", "on-demand", "query", 0, 0, 0, 0, int(bytes_billed))


# ── 리포트 ────────────────────────────────────────────────────────────────────


def _rate(model: str) -> tuple[float, float]:
    m = (model or "").lower()
    for frag, rin, rout in _PRICING:
        if frag in m:
            return rin, rout
    return 3.0, 15.0  # 미상 모델은 중간 요율로 추정


def _row_cost_usd(r: dict) -> float:
    if r["provider"] == "bigquery":
        return float(r["extra_units"] or 0) / (1024 ** 4) * _BQ_USD_PER_TIB
    rin, rout = _rate(r["model"])
    return (
        float(r["input_tokens"] or 0) / 1e6 * rin
        + float(r["output_tokens"] or 0) / 1e6 * rout
        + float(r["cache_read_tokens"] or 0) / 1e6 * rin * _CACHE_READ_FACTOR
        + float(r["cache_write_tokens"] or 0) / 1e6 * rin * _CACHE_WRITE_FACTOR
    )


def get_usage_report(days: int = 30) -> dict:
    """일별·모델별 사용량과 추정 비용. 요율은 조회 시점 기준으로 소급 계산."""
    ensure_usage_table()
    rows = fetch_all(
        "SELECT DATE(ts) d, provider, model, COUNT(*) calls, "
        "SUM(input_tokens) input_tokens, SUM(output_tokens) output_tokens, "
        "SUM(cache_read_tokens) cache_read_tokens, SUM(cache_write_tokens) cache_write_tokens, "
        "SUM(extra_units) extra_units "
        "FROM llm_usage WHERE ts >= DATE_SUB(NOW(), INTERVAL %s DAY) "
        "GROUP BY d, provider, model ORDER BY d DESC, provider, model",
        (days,),
    )
    daily: dict = {}
    by_model: dict = {}
    total_usd = 0.0
    for r in rows:
        cost = _row_cost_usd(r)
        total_usd += cost
        d = str(r["d"])
        daily.setdefault(d, {"calls": 0, "cost_usd": 0.0})
        daily[d]["calls"] += int(r["calls"])
        daily[d]["cost_usd"] += cost
        key = f"{r['provider']}/{r['model']}" if r["provider"] != "bigquery" else "bigquery"
        agg = by_model.setdefault(key, {
            "calls": 0, "input_tokens": 0, "output_tokens": 0,
            "cache_read_tokens": 0, "bytes_billed": 0, "cost_usd": 0.0,
        })
        agg["calls"] += int(r["calls"])
        agg["input_tokens"] += int(r["input_tokens"] or 0)
        agg["output_tokens"] += int(r["output_tokens"] or 0)
        agg["cache_read_tokens"] += int(r["cache_read_tokens"] or 0)
        agg["bytes_billed"] += int(r["extra_units"] or 0)
        agg["cost_usd"] += cost

    for v in by_model.values():
        v["cost_usd"] = round(v["cost_usd"], 4)
    return {
        "days": days,
        "total_cost_usd": round(total_usd, 2),
        "total_cost_krw": round(total_usd * _USD_KRW),
        "usd_krw": _USD_KRW,
        "pricing_note": "요율은 참고값 — 실제 청구서와 대조해 _PRICING 을 보정할 것",
        "by_model": by_model,
        "daily": [{"date": d, **v, "cost_usd": round(v["cost_usd"], 4)}
                  for d, v in sorted(daily.items(), reverse=True)],
    }
