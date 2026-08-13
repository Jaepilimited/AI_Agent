# -*- coding: utf-8 -*-
"""계획 → 보고서. 블록을 조립해 실행한다.

고정 스펙(`specs/*.py`)과 나란히 존재한다:
    - 고정 스펙 = 손으로 검증한 깊은 분석 (FOC 보고서처럼 집계 계약·시뮬레이션이 있는 것)
    - 동적 조립 = 질문에 맞춰 블록을 고르는 넓은 분석

같은 조회 엔진·게이트·권한·캐시를 쓴다.
"""
from __future__ import annotations

import concurrent.futures
import time
from dataclasses import asdict
from typing import Any, Dict, List

import structlog

from app.reports import blocks as B
from app.reports import planner as P
from app.reports import semantic as S

logger = structlog.get_logger(__name__)


def _run(sql: str, timeout: float = 120.0):
    from app.core.bigquery import get_bigquery_client
    from app.core.security import validate_sql
    ok, reason = validate_sql(sql)
    if not ok:
        raise ValueError(f"SQL 검증 실패: {reason}")
    return get_bigquery_client().execute_query(sql, timeout=timeout)


def _quality_notes(ctx: Dict[str, Any]) -> List[Dict[str, str]]:
    """이 데이터로 답할 때 반드시 함께 말해야 하는 것.

    고정 스펙의 품질 게이트를 넓은 보고서용으로 줄인 것. 조회가 아니라 **기지 사실**이라
    비용이 들지 않는다. 새 사실이 확인되면 여기에 추가한다.
    """
    notes = [
        {"label": "판매수수료·물류비 미반영",
         "text": "Service_Fee 가 음수로 적재돼 있어 수수료·물류비를 비용에서 제외했다. "
                 "원가가 들어간 수치는 수수료 차감 전이며 영업이익이 아니다."},
    ]
    if ctx.get("has_cost_metric"):
        notes.append(
            {"label": "원가 미적재 브랜드",
             "text": "UM(99.9%)·CBT(99.6%) 는 제품원가가 사실상 0원으로 적재돼 있다. "
                     "원가·수익성 수치는 SK 기준으로만 읽어야 한다."})
    if ctx.get("has_discount_metric"):
        notes.append(
            {"label": "할인 미적재 채널",
             "text": "Tiktok 채널들은 Discount_Coupon 이 전 구간 0원이다. "
                     "채널 할인율 비교에서 이들을 포함하면 비중이 실제보다 낮게 보인다."})
    return notes


def build(question: str, ctx: Dict[str, Any], *, plan: Dict[str, Any] | None = None,
          parallel: int = 8) -> Dict[str, Any]:
    """질문 → payload (섹션 목록 포함)."""
    t0 = time.time()
    the_plan = plan or P.plan(question, ctx)

    # 질문에서 결정적으로 뽑은 필터를 **모든 절에 강제한다.**
    # "일본 매출 보고서"인데 어느 절이 전사를 집계하면 그 절은 질문에 답하지 않는다.
    base_filters = ctx.get("base_filters") or {}
    if base_filters:
        for sec in the_plan["sections"]:
            merged = dict(base_filters)
            merged.update(sec.get("filters") or {})
            # 이미 그 축으로 쪼개는 절이면 필터를 걸지 않는다 (한 줄짜리 표가 된다)
            for d in (sec.get("dim"), sec.get("dim2")):
                merged.pop(d, None)
            if merged:
                sec["filters"] = merged

    metrics_used = {s["metric"] for s in the_plan["sections"]}
    ctx = dict(ctx)
    ctx["has_cost_metric"] = bool(metrics_used & {"유상원가", "FOC원가"})
    ctx["has_discount_metric"] = "할인" in metrics_used

    # 1) 모든 섹션의 조회를 한꺼번에 모아 병렬 실행 — 섹션 수만큼 왕복하지 않는다
    jobs: List[tuple] = []
    for idx, sec in enumerate(the_plan["sections"]):
        cls = B.BLOCKS[sec["block"]]["cls"]
        for name, q in cls.queries(sec, ctx).items():
            # 대부분의 블록은 Query 를 돌려주고 SQL 은 semantic 이 만든다. 프로모션
            # 캘린더처럼 지표·축 어휘로 표현할 수 없는 조회만 완성된 SQL 을 돌려준다
            # — 그것도 semantic 안에서 만들어지고 validate_sql 을 똑같이 통과한다.
            jobs.append((idx, name, q if isinstance(q, str) else S.build_sql(q)))

    results: Dict[tuple, Any] = {}
    errors: List[str] = []
    # ⛔ `with ThreadPoolExecutor` 금지 (CLAUDE.md) — 타임아웃이 무의미해진다
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, parallel))
    try:
        futs = {pool.submit(_run, sql): (idx, name, sql) for idx, name, sql in jobs}
        for fut, (idx, name, sql) in futs.items():
            try:
                results[(idx, name)] = fut.result(timeout=180)
            except Exception as e:
                results[(idx, name)] = []
                errors.append(f"{the_plan['sections'][idx]['block']}/{name}: {str(e)[:160]}")
    finally:
        pool.shutdown(wait=False)

    # 2) 섹션 조립. 조회가 빈 섹션은 **버리고 그 사실을 남긴다** (빈 표를 싣지 않는다)
    sections: List[Dict[str, Any]] = []
    skipped: List[str] = []
    for idx, sec in enumerate(the_plan["sections"]):
        facts = {name: results.get((idx, name), []) for name in
                 B.BLOCKS[sec["block"]]["cls"].queries(sec, ctx)}
        if not any(facts.values()):
            skipped.append(sec.get("title") or sec["block"])
            continue
        try:
            s = B.BLOCKS[sec["block"]]["cls"].build(sec, facts, ctx)
            sections.append(asdict(s))
        except Exception as e:
            errors.append(f"{sec['block']} 조립 실패: {str(e)[:160]}")
            skipped.append(sec.get("title") or sec["block"])

    if errors:
        logger.warning("dynamic_report_partial", errors=errors[:5], skipped=skipped)

    payload = {
        "meta": {
            "kind": "dynamic",
            "question": question,
            "title": the_plan["title"],
            "lede": the_plan.get("lede", ""),
            "params": ctx,
            "elapsed_sec": round(time.time() - t0, 1),
            "queries": len(jobs),
            "skipped": skipped,
            "planner_dropped": the_plan.get("dropped", []),
        },
        "sections": sections,
        "quality_notes": _quality_notes(ctx),
    }
    logger.info("dynamic_report_built", sections=len(sections), queries=len(jobs),
                skipped=len(skipped), sec=payload["meta"]["elapsed_sec"])
    return payload
