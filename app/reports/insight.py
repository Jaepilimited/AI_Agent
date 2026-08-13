# -*- coding: utf-8 -*-
"""판단 절 — Key Insights / Action Items.

**이 파일이 이 파이프라인에서 유일하게 LLM 이 문장을 쓰는 곳이다.** 나머지 서술은
전부 규칙이 숫자에서 뽑는다 (`blocks.py`). 그래도 여기를 연 이유는, 규칙으로는
"그래서 무엇을 해야 하나"를 쓸 수 없기 때문이다 (2026-08-13 사용자 요청).

⛔ **대신 숫자는 LLM 이 만들지 못하게 한다.** 판단·해석·액션은 LLM 이 쓰고,
   문장 속 수치는 **이미 조회로 나온 값과 한 글자도 다르면 그 문장을 버린다.**
   이 자리는 보고서에서 가장 먼저 읽히는 곳이라, 여기 틀린 숫자가 들어가면
   나머지를 아무리 결정적으로 만들어도 소용이 없다.

검증은 프롬프트 지시가 아니라 후처리다 — 국가·팀 리터럴을 교정할 때와 같은 이유로,
지시는 확률을 높일 뿐이고 보증은 코드가 한다.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Set

import structlog

logger = structlog.get_logger(__name__)

MAX_INSIGHTS = 8
MAX_ACTIONS = 3

# 문장에서 뽑을 수치 토큰. 1,234.5 / 35.4 / -12 형태를 모두 잡는다
_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _norm(tok: str) -> str:
    """'1,031.8' 과 '1031.80' 을 같은 수로 본다 (표기 차이로 버리지 않기 위해)."""
    t = tok.replace(",", "").lstrip("-")
    try:
        f = float(t)
    except ValueError:
        return t
    return f"{f:.4f}".rstrip("0").rstrip(".")


def _facts_from_sections(sections: List[Dict[str, Any]], limit_rows: int = 12) -> List[Dict]:
    """LLM 에 줄 사실 묶음. **이미 계산된 값만** 넣는다."""
    out = []
    for s in sections:
        if s["block"] == "conclusion":
            continue
        cols = [c for c in (s.get("columns") or [])]
        rows = []
        for r in (s.get("rows") or [])[:limit_rows]:
            rows.append({c["label"]: r.get(c["key"]) for c in cols if c["key"] in r})
        out.append({"절": s.get("title"), "종류": s["block"], "단위": s.get("unit"),
                    "발견": s.get("findings") or [], "행": rows})
    return out


def _allowed_numbers(facts: List[Dict], ctx: Dict[str, Any]) -> Set[str]:
    """문장에 써도 되는 수치 집합 — 조회 결과와 발견 문장에 실제로 있는 값."""
    ok: Set[str] = set()

    def eat(v):
        if isinstance(v, (int, float)):
            ok.add(_norm(str(v)))
        elif isinstance(v, str):
            for m in _NUM.findall(v):
                ok.add(_norm(m))

    for f in facts:
        for line in f.get("발견") or []:
            eat(line)
        for row in f.get("행") or []:
            for v in row.values():
                eat(v)
    # 기간 표기(2026·2025 등)는 사실이므로 허용한다
    for k in ("focus_label", "compare_label", "window_label", "focus_start", "focus_end"):
        eat(str(ctx.get(k) or ""))
    return ok


def _verify(text: str, allowed: Set[str]) -> Optional[str]:
    """문장에 **조회에 없는 수치**가 있으면 버린다. 없으면 그대로 통과."""
    for tok in _NUM.findall(text or ""):
        if _norm(tok) not in allowed:
            return tok
    return None


def _complete(text: str) -> bool:
    """문장이 끝까지 왔는가.

    토큰 상한에 걸려 잘려도 `repair_json` 이 괄호를 닫아 파싱은 성공한다 —
    그래서 **중간에서 끊긴 문장이 그대로 실렸다** (2026-08-13). 끝맺음으로 거른다.
    """
    t = (text or "").rstrip()
    return bool(t) and t[-1] in ".。!?…)”\"'」』%"


def _echoes(text: str, already: List[str]) -> bool:
    """이미 적힌 요약을 수치까지 그대로 되풀이하는 문장인가.

    프롬프트로 "되풀이하지 마라"고 해도 확률적이다. 문장의 수치 집합이 요약 한 줄에
    **완전히 포함되고 새 수치가 하나도 없으면** 새로 말하는 것이 없다고 본다.
    """
    mine = {_norm(t) for t in _NUM.findall(text or "")}
    if not mine:
        return False
    for line in already or []:
        theirs = {_norm(t) for t in _NUM.findall(line)}
        if theirs and mine <= theirs:
            return True
    return False


PROMPT = """당신은 데이터 분석 보고서의 **해석과 실행 제안**을 씁니다.

아래는 이미 조회가 끝난 사실입니다. 여기 없는 수치는 절대 쓰지 마세요.

## 질문
{question}

## 기간
중점 {focus_label} / 비교 {compare_label}

## 이미 보고서 맨 앞에 적힌 요약 — **되풀이하지 마세요**
{already}

## 조회된 사실
{facts}

## 쓸 것
1. `insights` — 관점별 해석 {n_ins}개 이내. 각 문장은 **여러 절을 엮어** 무엇이
   일어났는지 말합니다. 한 절만 다시 읽는 문장은 쓰지 마세요.
   관점 예: 국가·채널·제품·카테고리·영업유형·리스크
2. `actions` — 다음에 할 일 {n_act}개. 각각 `title`(짧은 명사구)과
   `text`(한두 문장, 무엇을 왜 확인/실행할지).

## 규칙
- **위 사실에 있는 수치만** 쓰세요. 없는 숫자를 쓰면 그 문장은 버려집니다.
- **이미 적힌 요약을 다시 말하지 마세요.** 총량·성장률만 되풀이하는 문장은 버려집니다.
  요약이 "무엇이 얼마인가"를 말했으니, 여기서는 "왜 그런가·무엇이 걸리는가"를 쓰세요.
- 수치를 쓸 때는 사실에 적힌 표기 그대로 쓰세요 (예: 191.3억, +35.4%).
- 추측·일반론 금지. "~로 보인다" 대신 사실이 말하는 것만.
- 데이터가 빠져 있다는 사실도 리스크로 쓸 수 있습니다.
- 아래 JSON 만 출력합니다.

{{
  "insights": ["문장1", "문장2"],
  "actions": [{{"title": "제목", "text": "설명"}}]
}}"""


def build(question: str, sections: List[Dict[str, Any]], ctx: Dict[str, Any],
          llm=None, already: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """판단 절을 만든다. 실패하면 None — 보고서는 이것 없이도 완성된다."""
    facts = _facts_from_sections(sections)
    if len(facts) < 2:
        return None

    if llm is None:
        try:
            from app.core.llm import get_llm_client
            llm = get_llm_client()
        except Exception as e:
            logger.warning("insight_no_llm", error=str(e)[:120])
            return None

    already = [a for a in (already or []) if a]
    prompt = PROMPT.format(
        question=question, focus_label=ctx.get("focus_label", ""),
        compare_label=ctx.get("compare_label", ""),
        already=("\n".join(f"- {a}" for a in already) or "(없음)"),
        facts=json.dumps(facts, ensure_ascii=False, default=str)[:9000],
        n_ins=MAX_INSIGHTS, n_act=MAX_ACTIONS)
    try:
        # ⚠️ 기본 4096 이면 잘린다. 잘린 응답은 repair_json 이 괄호를 닫아
        #    **정상 JSON 처럼 보이므로** 호출부에서 알아챌 수 없다 (2026-08-13 실측)
        raw = llm.generate_json(prompt, max_output_tokens=8192)
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as e:
        logger.warning("insight_llm_failed", error=str(e)[:200])
        return None

    allowed = _allowed_numbers(facts, ctx)
    dropped: List[str] = []

    ins: List[str] = []
    for t in (data.get("insights") or [])[:MAX_INSIGHTS]:
        t = (t or "").strip()
        if not t:
            continue
        if not _complete(t):
            dropped.append(f"insight[잘림] {t[-40:]}")
            continue
        bad = _verify(t, allowed)
        if bad:
            dropped.append(f"insight[{bad}] {t[:60]}")
            continue
        if _echoes(t, already):
            dropped.append(f"insight[요약반복] {t[:60]}")
            continue
        ins.append(t)

    acts: List[Dict[str, str]] = []
    for a in (data.get("actions") or [])[:MAX_ACTIONS]:
        title = (a.get("title") or "").strip()
        text = (a.get("text") or "").strip()
        if not title and not text:
            continue
        bad = _verify(f"{title} {text}", allowed)
        if bad:
            dropped.append(f"action[{bad}] {title[:40]}")
            continue
        acts.append({"title": title[:60], "text": text[:400]})

    if dropped:
        # 조용히 버리면 "왜 짧지"를 알 수 없다 — 무엇이 왜 빠졌는지 남긴다
        logger.warning("insight_dropped_unverified", count=len(dropped),
                       samples=dropped[:4])
    if not ins and not acts:
        return None

    return {"block": "insight", "title": "해석과 다음 할 일", "metric": "", "dim": None,
            "unit": "", "rows": [], "findings": ins, "chart": "none",
            "chart_key": "value", "columns": [], "note": "",
            "actions": acts, "dropped": len(dropped)}
