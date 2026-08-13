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


# 질문이 요구하는 주제 → 그걸 답하려면 반드시 있어야 하는 지표.
# ⛔ **어휘에 없으면 플래너는 가장 비슷한 지표로 바꿔 계획을 세운다.** 실제로 광고비·ROAS 를
#    물었는데 할인액·할인율 보고서가 나왔고, 버려진 계획도 0건이라 아무도 몰랐다
#    (2026-08-13 실측). 에러보다 나쁘다 — **질문에 답하지 않았다는 사실을 본문에 적는다.**
_TOPIC_NEEDS: List[tuple] = [
    (r"광고|ad\b|roas|cpc|cpm|ctr|acos|매체|캠페인", {"광고비", "노출", "클릭", "전환", "전환매출"}),
    (r"재고|입고|출고량|stock", set()),
    (r"리뷰|평점|별점|후기", set()),
    (r"인플루언서|인플|시딩", set()),
]


def _uncovered(question: str, sections: List[Dict[str, Any]]) -> List[str]:
    """질문이 요구했는데 **보고서가 다루지 못한** 주제."""
    import re as _re
    used = {s.get("metric") for s in sections}
    out = []
    for pat, needed in _TOPIC_NEEDS:
        if not _re.search(pat, question, _re.I):
            continue
        if needed and (used & needed):
            continue          # 요구한 주제를 실제로 다뤘다
        out.append(pat.split("|")[0])
    return out


def _headline(sections: List[Dict[str, Any]], ctx: Dict[str, Any],
              quality: List[Dict[str, str]]) -> List[str]:
    """맨 앞에 놓을 **사실 요약** 두세 줄.

    ⛔ LLM 을 쓰지 않는다. 규모·성장·비교는 틀리면 안 되는 자리라 규칙이 쓴다.

    ⚠️ **짧게 유지할 것.** 처음엔 추세·기여도·프로모션까지 여섯 줄을 담았더니
       바로 아래 해석 절과 같은 얘기를 두 번 하게 됐다 (2026-08-13 사용자 지적).
       여기는 '무엇이 얼마인가'까지만 말하고, '왜·그래서'는 해석이 맡는다.
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

    # 데이터 결함으로 뺀 것이 있으면 결론에서도 말한다 — 맨 아래 주석만으로는 안 읽힌다
    if quality:
        out.append("이 수치는 " + " · ".join(n["label"] for n in quality[:3]) +
                   " 를 제외하고 읽어야 한다 (아래 '산출 기준' 참조)")

    return out[:3]


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

    # 맨 앞 절 하나로 합친다. **요약과 해석을 따로 두면 같은 숫자를 두 번 말한다**
    # (2026-08-13 사용자 지적) — 사실은 규칙이, 해석·액션은 LLM 이 쓰되 한 자리에 놓는다
    notes = _quality_notes(ctx)

    # 외부 맥락 — 질문이 외부 요인을 물었을 때만. 조회 결과가 아니라 검색 결과라
    # **맨 뒤에** 두고 라벨을 붙인다. 판단 절보다 앞에 두면 근거처럼 읽힌다
    try:
        from app.reports import external as _ext
        ext = _ext.build(question, ctx)
        if ext:
            sections.append(ext)
    except Exception as e:
        logger.warning("external_section_failed", error=str(e)[:200])

    # 질문에 답하지 못한 것이 있으면 **가장 먼저** 밝힌다. 비슷한 지표로 바꿔치기한 채
    # 그럴듯한 문서를 내놓는 것이 이 파이프라인에서 가장 나쁜 실패다
    miss = _uncovered(question, sections)
    if miss:
        logger.warning("report_uncovered_topic", topics=miss, question=question[:100])
        notes.insert(0, {
            "label": "질문에 답하지 못한 부분",
            "text": f"질문의 '{', '.join(miss)}' 관련 요구는 이 보고서가 다루지 못했다. "
                    f"해당 데이터가 보고서 어휘에 없어 비슷한 지표로 대체하지 않고 비워 뒀다. "
                    f"채팅에서 직접 물으면 조회할 수 있다."})

    # 판정 계층 — 절마다 결론 한 줄, 행마다 판정. **해석보다 먼저** 돈다:
    # 판정이 붙은 절을 LLM 이 읽어야 해석이 같은 결론 위에서 쓰인다
    try:
        from app.reports import judge as _judge
        _judge.apply(sections)
        foc = _judge.focus(sections, ctx, notes, skipped)
        if foc:
            sections.append(foc)
    except Exception as e:
        logger.warning("judge_stage_failed", error=str(e)[:200])
        foc = None

    head = _headline(sections, ctx, notes)

    lead: Dict[str, Any] = {}
    try:
        from app.reports import insight as _ins
        # 이미 적힌 요약을 넘겨 되풀이를 막는다 (프롬프트 + 후처리 양쪽).
        # 실행안 버킷도 함께 넘긴다 — 규칙이 이미 지목한 곳을 다시 지목하지 않도록
        said = list(head) + [f"{r['bucket']}: {r['dim']} {r['why']}"
                             for r in ((foc or {}).get("rows") or [])]
        lead = _ins.build(question, sections, ctx, already=said) or {}
    except Exception as e:
        logger.warning("insight_failed", error=str(e)[:200])

    if head or lead:
        sections.insert(0, {
            "block": "lead", "title": "요약과 해석", "metric": "", "dim": None,
            "unit": "", "rows": [], "chart": "none", "chart_key": "value",
            "columns": [], "note": "",
            "findings": head,                       # 사실 — 규칙이 씀
            "insights": lead.get("findings") or [],  # 해석 — LLM 이 씀 (수치 검증됨)
            "actions": lead.get("actions") or [],
            "dropped": lead.get("dropped", 0),
            # 데이터 결함은 **맨 앞에서** 보여준다. 맨 뒤 '산출 기준'에만 두면
            # 표를 다 읽고 결론까지 낸 다음에야 눈에 들어온다 (2026-08-13)
            "notes": notes,
        })

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
