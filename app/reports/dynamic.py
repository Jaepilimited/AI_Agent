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
    # ⛔ UM·CBT 는 제품명이 통째로 비어 있다 (2026-08-13 실측). 이걸 말하지 않으면
    #    제품 절이 "조회 결과가 없어 뺐습니다" 로만 사라져 **왜 없는지를 아무도 모른다.**
    #    없는 것과 못 잡은 것을 가르는 것은 프로모션·성분에서와 같은 원칙이다.
    _brands = set((ctx.get("base_filters") or {}).get("브랜드") or [])
    if _brands & {"UM", "CBT"}:
        notes.append(
            {"label": "제품명 미적재 브랜드",
             "text": "UM·CBT 는 제품명(SET)이 100% 비어 있고 Product 테이블에도 행이 없다. "
                     "제품별·수량 절은 조회가 0건이라 실릴 수 없다 — 제품이 안 팔린 것이 "
                     "아니라 이름이 적재되지 않은 것이다. 국가·채널·월 단위는 정상 집계된다."})
    if ctx.get("has_discount_metric"):
        notes.append(
            {"label": "할인 미적재 채널",
             "text": "Tiktok 채널들은 Discount_Coupon 이 전 구간 0원이다. "
                     "채널 할인율 비교에서 이들을 포함하면 비중이 실제보다 낮게 보인다."})
    return notes


def _conclusion(sections: List[Dict[str, Any]], ctx: Dict[str, Any],
                quality: List[Dict[str, str]]) -> Dict[str, Any] | None:
    """이미 만들어진 절들의 **숫자에서** 결론 문단을 뽑는다.

    ⛔ 여기서도 LLM 을 쓰지 않는다. 절마다 발견 문장은 있는데 그걸 종합하는 문단이
       없어서 보고서가 '표 모음'으로 읽혔다 (2026-08-13). 종합을 LLM 에 맡기면
       그럴듯한데 틀린 문장이 맨 앞에 오게 된다 — 가장 위험한 자리다.

    ⚠️ 없는 절은 건너뛴다. 문장이 하나뿐이면 결론을 만들지 않는다 — 총량 절을
       한 번 더 읽는 꼴이라 자리만 차지한다.
    """
    from app.reports.blocks import _fmt, _josa, _pct

    by = {}
    for s in sections:
        by.setdefault(s["block"], s)
    out: List[str] = []

    tot = by.get("total")
    if tot and len(tot["rows"]) >= 2:
        c = float(tot["rows"][0].get("value") or 0)
        q = float(tot["rows"][1].get("value") or 0)
        u = tot["unit"]
        out.append(f"{ctx['focus_label']} {_fmt(c, u)}" +
                   (f", 전년 동기 대비 {_pct(c - q, q):+.1f}%" if q else ""))

    vs = by.get("versus")
    if vs and len(vs["rows"]) == 2:
        a, b = vs["rows"]
        ga, gb = a.get("growth"), b.get("growth")
        if ga is not None and gb is not None and gb > 0 and ga > 0:
            out.append(f"{_josa(str(a['dim']), '은는')} {ga:+.1f}%로 "
                       f"{b['dim']}({gb:+.1f}%)보다 {abs(ga / gb):.1f}배 "
                       f"{'빠르다' if ga > gb else '느리다'}")
        da, db = float(a.get("delta") or 0), float(b.get("delta") or 0)
        if da + db:
            out.append(f"전체 증가분의 {_pct(da, da + db):.0f}%가 {a['dim']}에서 나왔다")

    con = by.get("contribution")
    if con and con["rows"]:
        top = [r for r in con["rows"] if float(r.get("delta") or 0) > 0][:3]
        if top:
            names = ", ".join(str(r["dim"]) for r in top)
            share = sum(float(r.get("share") or r.get("value") or 0) for r in top)
            out.append(f"증가를 이끈 곳은 {names}" +
                       (f" (증가분의 {share:.0f}%)" if 0 < share <= 100 else ""))

    tr = by.get("trend")
    if tr and len(tr["rows"]) >= 3:
        rs = tr["rows"]
        hi = max(rs, key=lambda r: float(r.get("value") or 0))
        lo = min(rs, key=lambda r: float(r.get("value") or 0))
        hv, lv = _fmt(hi["value"], tr["unit"]), _fmt(lo["value"], tr["unit"])
        out.append(f"월별로는 {hi['dim']} {_josa(hv, '이가')} 최고, "
                   f"{lo['dim']} {_josa(lv, '이가')} 최저다")

    pr = by.get("promotion")
    if pr and pr["findings"]:
        marked = [f for f in pr["findings"] if "행사가 기록된" in f]
        if marked:
            out.append(marked[0])

    # 데이터 결함으로 뺀 것이 있으면 결론에서도 말한다 — 맨 아래 주석만으로는 안 읽힌다
    if quality:
        out.append("이 수치는 " + " · ".join(n["label"] for n in quality[:3]) +
                   " 를 제외하고 읽어야 한다 (아래 '산출 기준' 참조)")

    if len(out) < 2:
        return None
    return {"block": "conclusion", "title": "결론", "metric": "", "dim": None,
            "unit": "", "rows": [], "findings": out[:6], "chart": "none",
            "chart_key": "value", "columns": [], "note": ""}


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

    # 결론은 **맨 앞**에 온다. 다 읽어야 알 수 있는 보고서는 안 읽힌다
    notes = _quality_notes(ctx)

    # 판단 절 — 이 파이프라인에서 유일하게 LLM 이 문장을 쓰는 곳. 숫자는 검증한다
    insight = None
    try:
        from app.reports import insight as _ins
        insight = _ins.build(question, sections, ctx)
    except Exception as e:
        logger.warning("insight_failed", error=str(e)[:200])
    if insight:
        sections.insert(0, insight)

    concl = _conclusion(sections, ctx, notes)
    if concl:
        sections.insert(0, concl)

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
        "quality_notes": notes,
    }
    logger.info("dynamic_report_built", sections=len(sections), queries=len(jobs),
                skipped=len(skipped), sec=payload["meta"]["elapsed_sec"])
    return payload
