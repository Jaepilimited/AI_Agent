# -*- coding: utf-8 -*-
"""수출 물류 금액 → 한화 환산. **월말환율**로 하고, 무엇을 썼는지 반드시 밝힌다.

붐따 #162 (2026-09-04, 정다운 제보): *"수출 자료 다운로드시, 한화로 변환이
안되는것같습니다!"* — 그때는 환율 데이터를 못 찾아 "환산할 수 없다" 고 안내하는
것까지만 했다. 2026-09-06 사내 환율표가 BigQuery 에 올라와 실제 환산이 가능해졌다
(사용자 확인: *"환율은 여기서 가져와 근데 이건 월말환율이야"*).

실측(2026-09-07)으로 확인한 것:

    구성        13통화 x 82개월 (2020-01 ~ 2026-10) = 1,066행 · 결측 없음
    커버리지    물류의 모든 통화 100% (USD 1,693 · EUR 96 · JPY 19 · CNY 9)
    JPY 기준    **1엔**(8.5927)

⛔ **`app/core/fx_rates.py`(출근 브리핑용)와 섞지 마라.** 저쪽은 JPY 를 **100엔**
   단위로 담는다 (`UNITS = {"JPY": 100, ...}`). 같은 통화의 숫자가 100배 다르므로,
   한 화면에서 두 소스를 함께 쓰면 **에러 없이 100배 틀린다.** 이 파일은 사내
   확정 월말환율만 본다 — 브리핑의 오늘 고시와는 목적도 값도 다르다.

⛔ **이번 달과 그 이후는 확정 환율이 아니다.** 월말환율은 그 달이 끝나야 정해진다.
   실측: 82개월 중 직전 달과 값이 완전히 같은 달은 2026-09·10 **둘뿐**이고,
   그 값은 2026-08 과 같다 — 아직 안 끝난 달을 직전 확정치로 채워 둔 것이다.
   ⚠️ 하필 제보자가 물은 것이 **9월 ETD 수출 건**이었다. 말하지 않으면 8월 환율로
      환산된 9월 금액을 확정치로 읽는다.

⛔ **통화가 비어 있는 건은 환산할 수 없다** — 환율이 아니라 **통화**를 모르는 것이다
   (2025-12 이후 2,518건 중 626건 = 24.9%). 그 건들은 환산 합계에서 빠지므로
   **빠졌다는 사실을 답변에 적는다.** 조용히 작아진 합계가 이 프로젝트의 단골 오답이다.

방어는 두 겹이다 (`logistics_amount` 와 같은 구조):
  1. 프롬프트가 조인식을 준다 (`{{LOG_FX_SECTION}}`) — 생성은 LLM 이 한다
  2. `notice()` 가 **무엇을 기준으로 환산했는지 공시**한다. 환산을 요구받고도
     조인하지 않았으면 `logistics_amount.krw_notice()` 가 못 했다고 말한다
"""
from __future__ import annotations

import re
from datetime import date

import structlog

logger = structlog.get_logger(__name__)

FX_TABLE = "skin1004-319714.Sales_Integration.Exchange_Rate"

#: 기준월. ⛔ **조회가 기간을 잡는 날짜와 같은 날짜여야 한다** — 발주일로 걸러 놓고
#: 출항일 환율로 환산하면 표 안에서 숫자가 어긋나고, 그 어긋남은 티가 나지 않는다.
BASIS_COLUMN = "order_date"
BASIS_LABEL = "발주일"

#: ⚠️ 프롬프트·회귀가 **이 문자열들을 그대로** 본다. 사본을 만들지 마라.
JOIN_EXPR = (
    f"LEFT JOIN `{FX_TABLE}` fx\n"
    f"  ON fx.Base = TRIM(l.unit)\n"
    f" AND fx.Year_month = DATE_TRUNC(l.{BASIS_COLUMN}, MONTH)"
)
#: 환율표에 `KRW` 행은 없다 (원화는 환산할 것이 없다) — 1 로 둔다.
#: 통화 미상은 `fx.rate` 가 NULL 이라 자연히 빠진다 (그래서 빠진 건수를 공시한다).
RATE_EXPR = "IF(TRIM(l.unit) = 'KRW', 1, fx.rate)"
KRW_EXPR = (
    f"IFNULL(l.total_amount, l.amount + IFNULL(l.free_amount, 0)) * {RATE_EXPR}"
)
#: 환산하지 못한 행을 세는 식 — 답변이 "몇 건이 빠졌는지" 를 말할 수 있어야 한다
UNCONVERTED_EXPR = f"COUNTIF({RATE_EXPR} IS NULL)"

# SQL 이 실제로 환율표를 붙였는가. 프로젝트·데이터셋 표기가 흔들려도 잡히게
# 테이블 이름으로만 본다 (백틱·대소문자 무시).
_RE_FX_JOIN = re.compile(r"exchange_rate", re.IGNORECASE)


def converts_to_krw(sql: str) -> bool:
    """이 SQL 이 월말환율로 한화 환산을 하고 있는가."""
    return bool(_RE_FX_JOIN.search(re.sub(r"[`\"]", "", sql or "")))


def confirmed_through(today: date | None = None) -> str:
    """확정된 마지막 월(`YYYY-MM`). 월말환율은 **그 달이 끝나야** 정해진다.

    ⛔ 표에 값이 있다고 확정이 아니다 — 안 끝난 달은 직전 확정치로 채워져 있다.
       그래서 표를 조회하지 않고 **날짜로** 판정한다 (조회가 죽어도 공시는 산다).
    """
    d = today or date.today()
    year, month = (d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12)
    return f"{year}-{month:02d}"


def build_prompt_section() -> str:
    """프롬프트의 한화 환산 절. ⛔ 같은 식을 프롬프트에 손으로 또 적지 마라."""
    return "\n".join([
        "### 한화(KRW) 환산 — **요청받았을 때만** 한다",
        "",
        "기본은 통화별 원값이다. 사용자가 「한화로/원화로 바꿔·환산」이라고 하면"
        f" 사내 **월말환율**표로 환산한다 (기준월 = {BASIS_LABEL}의 달).",
        "",
        "```sql",
        "SELECT",
        f"  SUM({KRW_EXPR}) AS amount_krw,",
        f"  {UNCONVERTED_EXPR} AS unconverted_rows",
        "FROM `skin1004-319714.Export_control.export_logistics` l",
        JOIN_EXPR,
        "WHERE NOT l.is_deleted",
        "```",
        "",
        "- ⛔ **`LEFT JOIN` 이다.** `JOIN` 으로 하면 통화가 비어 있는 건(전체의 25%)이"
        " 행째로 사라진다 — 건수까지 조용히 줄어든다",
        "- ⛔ **환율표에 `KRW` 행은 없다.** 원화 건은 위 식처럼 1 로 둔다."
        " 안 그러면 원화 매출이 통째로 빠진다",
        "- ⛔ **환산하지 못한 건수(`unconverted_rows`)를 반드시 함께 조회하고"
        " 답변에 적어라** — 통화를 몰라 빠진 건이다. 합계가 조용히 작아지면 안 된다",
        "- ⛔ 환산했으면 **「월말환율 기준」이라고 답변에 밝혀라.** 거래일 환율이 아니다",
        "- ⚠️ 환산은 요청받았을 때만이다. 안 물었으면 통화별로 나눠서 낸다",
    ])


def notice(sql: str = "", question: str = "") -> str:
    """환산했으면 **무엇을 기준으로 했는지** 표보다 먼저 말한다.

    ⛔ 환산값은 원본에 없는 파생값이다. 원화 숫자가 표에 찍히는 순간 사람은 그것을
       정산 금액으로 읽는다 — 기준을 안 밝히면 그 오해를 막을 방법이 없다.
    """
    if not converts_to_krw(sql):
        return ""
    logger.info("logistics_fx_converted", basis=BASIS_COLUMN)
    nl = chr(10)
    return (
        "> ⚠️ **아래 원화 금액은 사내 「월말환율」로 환산한 값입니다** —"
        f" 거래일 환율이 아니라 **{BASIS_LABEL}이 속한 달의 월말환율**을 씁니다." + nl
        + f"> 확정된 환율은 **{confirmed_through()}까지**입니다 — 월말환율은 그 달이"
        " 끝나야 정해지므로, 그 이후 건은 직전 확정월 환율로 환산된 **잠정치**입니다." + nl
        + "> 통화가 기재되지 않은 건은 환산할 수 없어 **합계에서 빠집니다**"
        " (전체의 약 25%). 빠진 건수를 함께 확인해 주세요." + nl + nl)
