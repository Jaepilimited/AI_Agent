# -*- coding: utf-8 -*-
"""SQL 이 안내문 한 줄만 돌려줬으면, 그 안내문이 곧 답이다.

붐따 #153 (2026-08-26, 이형섭):

    [질문] "Ecomm팀 쇼피파이(자사몰) 글로벌e 전환 관련해서 문서 찾아줘"
    [SQL]  SELECT '요청하신 … 문서는 시스템에 연동되어 있지 않습니다. …'
             AS notice LIMIT 1
    [답변] "…연동되어 있지 않아 직접적인 문서 확인이 어렵습니다."
           그리고 **그 아래에 표가 붙었다**:
             총 반품 수량 6,998개 · 반품 주문 797건 · 1건당 8.78개
             글로벌 자사몰 연간 매출 약 321.2억원 · 월평균 26.8억원
             마케팅 예산 약 136.7억원 · 5월 최고 56.2억원
           머리말: *"현재 내부 데이터베이스에서 확인할 수 있는 … 주요 지표"*

⛔ **그 숫자들은 조회 결과에 없다.** 결과는 안내문 한 줄이 전부였다. LLM 이
   지어냈거나 앞선 대화에서 끌어온 것인데, 답변은 그것을 "내부 데이터베이스에서
   확인할 수 있는" 지표라고 소개했다. 문서를 찾던 사람에게 **묻지도 않은,
   출처도 없는 수치**가 사실처럼 나갔다.

⚠️ 프롬프트는 이 경로를 이미 알고 있었다 — 재고·미연동 질문에 이 SQL 을
   생성하라고 적으면서 *"답변 포맷터가 이 notice 를 보고 안내함"* 이라고
   써 두었다. **그런 코드는 없었다.** 프롬프트가 코드에 없는 동작을 약속하고
   있었던 것이다.

⛔ 그래서 LLM 을 아예 태우지 않는다. 안내문만 돌아왔으면 **그 문자열이 답**이다
   — 지어낼 여지를 남기지 않는 것이 이 설계의 전부다 (보고서에서 숫자를 LLM 이
   쓰는 경로를 아예 두지 않은 것과 같은 사상). 덤으로 빠르다.
"""
from __future__ import annotations

from typing import Optional, Sequence

import structlog

logger = structlog.get_logger(__name__)

# 이 이름의 컬럼이면 "사람에게 보여줄 안내문" 이다. 프롬프트가 쓰는 이름과 같아야
# 한다 — 다르면 조용히 안 걸리고, 안 걸리면 #153 이 그대로 재현된다.
NOTICE_COLUMNS = ("notice", "message", "안내")

_MIN_LEN = 10          # 한두 글자짜리 값은 안내문이 아니다


def extract(results: Optional[Sequence[dict]]) -> Optional[str]:
    """안내문 한 줄짜리 결과면 그 문자열, 아니면 None.

    ⛔ **한 행이고, 안내 컬럼에 글이 있고, 나머지 칸이 비어 있을 때만**이다.
       조건을 넓히면 진짜 데이터가 든 결과를 안내문으로 오인해 **표를 통째로
       삼킨다** — 못 찾는 것보다 나쁜 실패다.
    """
    if not results or len(results) != 1:
        return None
    row = results[0]
    if not isinstance(row, dict) or not row:
        return None

    text = None
    for key, value in row.items():
        if str(key).strip().casefold() in NOTICE_COLUMNS:
            if isinstance(value, str) and len(value.strip()) >= _MIN_LEN:
                text = value.strip()
            break
    if text is None:
        return None

    # 다른 칸에 값이 있으면 이건 안내문이 아니라 데이터다
    for key, value in row.items():
        if str(key).strip().casefold() in NOTICE_COLUMNS:
            continue
        if value not in (None, "", 0):
            return None

    logger.info("sql_notice_only_result", length=len(text))
    return text


def render(text: str) -> str:
    """화면에 나갈 모양. 안내문 그대로 두되 제목만 얹는다.

    ⛔ 여기서 문장을 덧붙이지 마라 — 무엇이 없는지는 안내문이 이미 말하고 있고,
       더 쓰면 그게 지어낼 자리가 된다.
    """
    return "### 안내\n\n" + text
