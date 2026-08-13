# -*- coding: utf-8 -*-
"""외부 맥락 절 — 그 기간 그 시장에 무슨 일이 있었나 (구글 검색 기반).

매출이 튄 달 옆에 "그때 이런 일이 있었다"를 놓는다. **판단은 사람이 한다.**

⛔ **이 절은 상관도 인과도 주장하지 않는다.** 검색은 문장을 줄 뿐 시계열을 주지
   않으므로 상관계수를 낼 수 없다. LLM 이 "기온과 매출 상관 0.72" 를 쓰면 계산이
   아니라 창작이고, 하필 통계처럼 생겨서 가장 잘 믿긴다 (2026-08-13 결정).
   진짜 날씨 상관이 필요하면 기온 시계열을 적재해야 한다 — 그때는 `correlation`
   블록이 그대로 쓴다.

그래서 세 가지를 **코드가** 막는다 (프롬프트 지시로는 확률일 뿐이다):
   1. 숫자 금지 — 연월(YYYY-MM)만 허용. 나머지 숫자가 있으면 그 항목을 버린다
   2. 인과 표현 금지 — "때문에·덕분에·영향으로·원인" 이 있으면 버린다
   3. 우리 실적 언급 금지 — 매출·성장률 등을 말하면 버린다 (조회하지 않은 값이다)

검증되지 않은 외부 정보라는 사실을 절 제목과 note 에 항상 적는다.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)

MAX_EVENTS = 8

# 질문이 외부 요인을 물었을 때만 붙인다 — 평소 보고서를 느리게 만들지 않는다
_WANTS = re.compile(
    r"외부|날씨|기후|기온|폭염|한파|장마|계절|환율|경쟁사|경쟁|시장\s*상황|이슈|"
    r"트렌드|사회|정책|규제|연휴|명절|이벤트|무슨\s*일")

_MONTH = re.compile(r"^20\d{2}-(0[1-9]|1[0-2])$")
_DIGIT = re.compile(r"\d")
# 인과를 말하면 이 절의 전제가 깨진다
_CAUSAL = re.compile(r"때문|덕분|영향으로|영향을\s*(주|미)|원인|유발|견인|기여|덕에|"
                     r"때문이|로\s*인해|탓")
# 우리 실적을 말하면 조회하지 않은 값을 말하는 것이다
_OURS = re.compile(r"매출|실적|성장률|판매량|점유율|객단가|ROAS|광고비")


def wants_external(question: str) -> bool:
    return bool(_WANTS.search(question or ""))


PROMPT = """{country} 시장에서 {start} ~ {end} 사이에 있었던, 화장품·뷰티 소비에
영향을 줄 수 있는 **공개적으로 알려진 사건**을 찾아 정리하세요.

예: 이상 기후(폭염·한파·장마), 장기 연휴·명절, 대형 쇼핑 행사, 환율 급변,
    유통·광고 규제 변화, 사회적 이슈

## 반드시 지킬 것
- **숫자를 쓰지 마세요.** 연월(예: 2026-03)만 허용됩니다. 기온·금액·퍼센트 모두 금지입니다.
- **인과를 말하지 마세요.** "때문에·덕분에·영향으로·원인" 같은 표현을 쓰면 버려집니다.
  무슨 일이 있었는지 사실만 적습니다.
- **매출·실적·성장률을 언급하지 마세요.** 당신은 그 회사의 실적을 모릅니다.
- 확인되지 않은 것은 넣지 마세요. 없으면 빈 배열로 두세요.
- `source` 에는 출처(언론사·기관 이름)를 적습니다.

아래 JSON 만 출력합니다.

{{"events": [{{"month": "YYYY-MM", "text": "무슨 일이 있었는지 한 문장",
  "source": "출처 이름"}}]}}"""


def _clean(ev: Dict[str, Any], lo: str, hi: str, dropped: List[str]) -> Optional[Dict[str, str]]:
    month = str(ev.get("month") or "").strip()
    text = str(ev.get("text") or "").strip()
    src = str(ev.get("source") or "").strip()
    if not _MONTH.match(month) or not (lo <= month <= hi):
        dropped.append(f"기간밖[{month}]")
        return None
    if not text:
        return None
    if _DIGIT.search(text):
        dropped.append(f"숫자[{text[:40]}]")
        return None
    if _CAUSAL.search(text):
        dropped.append(f"인과주장[{text[:40]}]")
        return None
    if _OURS.search(text):
        dropped.append(f"실적언급[{text[:40]}]")
        return None
    return {"dim": month, "event": text[:160], "source": (src or "출처 미상")[:40]}


def build(question: str, ctx: Dict[str, Any], llm=None) -> Optional[Dict[str, Any]]:
    """외부 맥락 절. 질문이 외부 요인을 묻지 않으면 만들지 않는다."""
    if not wants_external(question):
        return None

    countries = (ctx.get("base_filters") or {}).get("국가") or []
    country = " · ".join(str(c) for c in countries) if countries else "글로벌"
    lo = str(ctx.get("focus_start", ""))[:7]
    hi = str(ctx.get("focus_end", ""))[:7]
    if not (lo and hi):
        return None

    if llm is None:
        try:
            from app.core.llm import get_flash_client
            llm = get_flash_client()      # 검색 그라운딩은 Flash 로 (Pro 는 4배 느리다)
        except Exception as e:
            logger.warning("external_no_llm", error=str(e)[:120])
            return None

    prompt = PROMPT.format(country=country, start=ctx.get("focus_start"),
                           end=ctx.get("focus_end"))
    try:
        from app.core.llm import repair_json
        raw = llm.generate_with_search(prompt, temperature=0.2, max_output_tokens=4096)
        m = re.search(r"\{.*\}", raw or "", re.S)      # 검색 응답은 산문이 섞여 온다
        data = json.loads(repair_json(m.group(0) if m else "{}"))
    except Exception as e:
        logger.warning("external_failed", error=str(e)[:200])
        return None

    dropped: List[str] = []
    rows: List[Dict[str, str]] = []
    for ev in (data.get("events") or [])[: MAX_EVENTS * 2]:
        if not isinstance(ev, dict):
            continue
        c = _clean(ev, lo, hi, dropped)
        if c:
            rows.append(c)
        if len(rows) >= MAX_EVENTS:
            break
    if dropped:
        logger.warning("external_dropped", count=len(dropped), samples=dropped[:4])
    if not rows:
        return None

    rows.sort(key=lambda r: r["dim"])
    s = {
        "block": "external", "title": f"외부 맥락 — {country} (검증되지 않은 참고 정보)",
        "metric": "", "dim": "월", "unit": "", "rows": rows, "chart": "none",
        "chart_key": "value", "columns": [
            {"key": "dim", "label": "월"},
            {"key": "event", "label": "무슨 일이 있었나"},
            {"key": "source", "label": "출처"},
        ],
        "findings": [
            f"{lo} ~ {hi} 사이 {country} 시장의 공개 정보 {len(rows)}건을 검색해 모았다",
            "⚠️ 이 절은 조회 결과가 아니라 **웹 검색 결과**다 — 사실 여부를 확인하지 않았다",
            "⛔ 시점만 나란히 놓았을 뿐 상관·인과를 주장하지 않는다. "
            "매출 변동과 이어 읽을지는 사람이 판단할 몫이다",
        ],
        "note": ("숫자·인과 표현·실적 언급이 든 항목은 버렸다. 기온처럼 수치로 견주려면 "
                 "해당 시계열을 데이터로 적재해야 한다 — 그때는 상관 절이 계산한다."),
        "dropped": len(dropped),
    }
    return s
