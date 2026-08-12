# -*- coding: utf-8 -*-
"""질문 → 보고서 계획.

**LLM 이 여기서 하는 일은 조합 선택뿐이다.** 어떤 블록을, 어떤 지표·축으로, 어떤 순서로.
숫자도 SQL 도 쓰지 않는다. 계획은 검증된 어휘(semantic.vocabulary)로만 표현되고,
어휘 밖의 값은 **조용히 버리지 않고 로그를 남기고** 버린다.

계획이 비면 규칙 기반 기본 계획으로 떨어진다 — LLM 이 죽어도 보고서는 나와야 한다.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import structlog

from app.reports import blocks as B
from app.reports import semantic as S

logger = structlog.get_logger(__name__)

MAX_SECTIONS = 8


PROMPT = """당신은 데이터 분석 보고서의 **목차를 설계**합니다. 숫자는 쓰지 않습니다.

사용자 질문:
{question}

기간: 중점 {focus_label} ({focus_start} ~ {focus_end}) / 비교 {compare_label}

## 사용 가능한 분석 블록
{blocks}

## 사용 가능한 지표 (metric)
{metrics}

## 사용 가능한 축 (dim)
{dims}

## 규칙
- 아래 JSON 만 출력합니다. 설명·주석 금지.
- metric·dim·block 은 **위 목록의 키를 그대로** 씁니다. 없는 말을 지어내지 마세요.
- 섹션은 3~{max_sections}개. 질문에 답하는 순서로 배열하세요.
- 보통 좋은 순서: 총량(total) → 추세(trend) → 구성(breakdown) → 전년비(compare) → 순위(ranking)
- 질문이 특정 국가·팀·채널로 좁혀져 있으면 filters 에 넣으세요. 예: {{"국가": ["일본"]}}
- 질문이 특정 축을 강조하면(예: "채널별") 그 축 섹션을 앞에 두세요.
- cross 블록은 dim 과 dim2 를 모두 지정합니다.
- title 은 한국어 명사구로 짧게. **숫자를 넣지 마세요.**

{{
  "title": "보고서 제목 (숫자 없이)",
  "lede": "이 보고서가 무엇을 보는지 한 문장. 숫자 없이.",
  "sections": [
    {{"block": "total", "metric": "매출", "title": "..."}},
    {{"block": "trend", "metric": "매출", "dim": "월", "title": "..."}},
    {{"block": "breakdown", "metric": "매출", "dim": "국가", "title": "..."}}
  ]
}}"""


def _vocab_text() -> Dict[str, str]:
    v = S.vocabulary()
    return {
        "blocks": "\n".join(f"- {k}: {d}" for k, d in B.available().items()),
        "metrics": "\n".join(
            f"- {k} ({m['label']}, 단위 {m['unit']})" + (f" — {m['desc']}" if m["desc"] else "")
            for k, m in v["metrics"].items()),
        "dims": "\n".join(
            f"- {k} ({d['label']})" + (f" — {d['desc']}" if d["desc"] else "")
            for k, d in v["dimensions"].items()),
    }


def default_plan(question: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """LLM 없이도 나오는 기본 매출 보고서."""
    return {
        "title": f"{ctx['focus_label']} 매출 보고서",
        "lede": "기간 전체 규모와 방향을 보고, 어디에서 왔는지 축별로 나눠 본다.",
        "sections": [
            {"block": "total", "metric": "매출", "title": "매출 총량"},
            {"block": "trend", "metric": "매출", "dim": "월", "title": "월별 추세"},
            {"block": "breakdown", "metric": "매출", "dim": "국가", "title": "국가별 구성"},
            {"block": "compare", "metric": "매출", "dim": "국가", "title": "국가별 전년 대비"},
            {"block": "breakdown", "metric": "매출", "dim": "채널", "title": "채널별 구성"},
            {"block": "breakdown", "metric": "매출", "dim": "팀", "title": "팀별 구성"},
            {"block": "ranking", "metric": "매출", "dim": "제품", "limit": 15,
             "title": "제품 상위"},
        ],
    }


def _clean_section(s: Any, problems: List[str]) -> Optional[Dict[str, Any]]:
    if not isinstance(s, dict):
        return None
    blk = s.get("block")
    if blk not in B.BLOCKS:
        problems.append(f"모르는 블록 '{blk}'")
        return None
    metric = s.get("metric")
    if metric not in S.METRICS:
        problems.append(f"모르는 지표 '{metric}'")
        return None

    out: Dict[str, Any] = {"block": blk, "metric": metric}

    dim = s.get("dim")
    if B.BLOCKS[blk]["needs_dim"]:
        if dim not in S.DIMENSIONS:
            problems.append(f"모르는 축 '{dim}' ({blk})")
            return None
        if S.METRICS[metric].table not in S.DIMENSIONS[dim].tables:
            problems.append(f"'{dim}' 축은 '{metric}' 지표와 못 쓴다")
            return None
        out["dim"] = dim
    elif dim in S.DIMENSIONS:
        out["dim"] = dim

    if blk == "cross":
        d2 = s.get("dim2")
        if d2 not in S.DIMENSIONS or d2 == out.get("dim"):
            problems.append(f"cross 에 쓸 수 없는 dim2 '{d2}'")
            return None
        out["dim2"] = d2

    filters = {}
    for k, v in (s.get("filters") or {}).items():
        if k in S.FILTERABLE:
            filters[k] = v if isinstance(v, list) else [v]
        else:
            problems.append(f"필터로 못 쓰는 축 '{k}'")
    if filters:
        out["filters"] = filters

    title = (s.get("title") or "").strip()
    # 제목에 숫자가 있으면 버린다 — 계획 단계에서 수치를 지어낸 것이다
    if title and not any(c.isdigit() for c in title):
        out["title"] = title[:60]
    elif title:
        problems.append(f"제목에 숫자가 들어가 버림: '{title[:40]}'")

    if isinstance(s.get("limit"), int):
        out["limit"] = max(3, min(50, s["limit"]))
    return out


def plan(question: str, ctx: Dict[str, Any], llm=None) -> Dict[str, Any]:
    """질문 → 계획. 실패하면 기본 계획."""
    if llm is None:
        try:
            from app.core.llm import get_llm_client
            llm = get_llm_client()
        except Exception as e:
            logger.warning("planner_no_llm", error=str(e)[:120])
            return default_plan(question, ctx)

    v = _vocab_text()
    prompt = PROMPT.format(question=question, max_sections=MAX_SECTIONS,
                           focus_label=ctx["focus_label"], focus_start=ctx["focus_start"],
                           focus_end=ctx["focus_end"], compare_label=ctx["compare_label"],
                           **v)
    try:
        raw = llm.generate_json(prompt)
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as e:
        logger.warning("planner_llm_failed", error=str(e)[:200])
        return default_plan(question, ctx)

    problems: List[str] = []
    sections = []
    for s in (data.get("sections") or [])[:MAX_SECTIONS]:
        c = _clean_section(s, problems)
        if c:
            sections.append(c)

    if problems:
        # 조용히 버리지 않는다 — 계획 품질이 나빠지면 알 수 있어야 한다
        logger.warning("planner_dropped", count=len(problems), problems=problems[:6])

    if len(sections) < 2:
        logger.warning("planner_too_thin", kept=len(sections))
        return default_plan(question, ctx)

    title = (data.get("title") or "").strip()
    if not title or any(c.isdigit() for c in title):
        title = f"{ctx['focus_label']} 분석 보고서"
    lede = (data.get("lede") or "").strip()
    if any(c.isdigit() for c in lede):
        lede = ""   # 도입부에 숫자를 지어낸 경우 버린다

    return {"title": title[:80], "lede": lede[:200], "sections": sections,
            "dropped": problems}
