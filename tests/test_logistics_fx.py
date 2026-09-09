# -*- coding: utf-8 -*-
"""수출 물류 금액의 한화 환산 = 사내 **월말환율** (붐따 #162 후속).

2026-09-06 에 `Sales_Integration.Exchange_Rate` 가 올라와 환산이 가능해졌다.
여기서 지키는 것은 "환산이 되는가" 가 아니라 **조용히 틀리지 않는가** 다:
빠진 통화·잠정 환율·단위 혼동은 전부 에러 없이 지나간다.
"""
import re
from datetime import date
from tests._answer_paths import assert_every_answer_path_has

from app.core import logistics_fx as FX

LOG_T = "skin1004-319714.Export_control.export_logistics"


# ── 조인식은 한 곳에서만 만든다 ──────────────────────────────────────────

def test_the_prompt_gets_the_expression_from_code():
    """⛔ 프롬프트에 같은 식을 손으로 또 적으면 사본이 갈린다."""
    from app.agents.sql_agent import _load_prompt

    prompt = _load_prompt("sql_generator.txt")
    assert "{{LOG_FX_SECTION}}" not in prompt, "자리표시자가 렌더되지 않았다"
    assert FX.KRW_EXPR in prompt
    assert FX.JOIN_EXPR in prompt


def test_the_join_is_a_left_join():
    """⛔ `JOIN` 으로 하면 통화가 비어 있는 25% 가 **행째로** 사라진다 —
    금액뿐 아니라 건수까지 조용히 줄어든다."""
    assert FX.JOIN_EXPR.strip().upper().startswith("LEFT JOIN")


def test_won_rows_are_not_dropped():
    """환율표에 `KRW` 행이 없다 — 1 로 두지 않으면 원화 건이 통째로 빠진다."""
    assert "'KRW'" in FX.RATE_EXPR and "1" in FX.RATE_EXPR
    assert FX.RATE_EXPR in FX.KRW_EXPR


def test_the_unconverted_rows_are_counted():
    """합계가 조용히 작아지면 안 된다 — 빠진 건수를 셀 식이 함께 있어야 한다."""
    assert "COUNTIF" in FX.UNCONVERTED_EXPR
    assert "unconverted_rows" in FX.build_prompt_section()


def test_the_amount_is_paid_plus_free():
    """⛔ 붐따 #159 의 규칙이 환산식에도 살아 있어야 한다 (유상만 쓰면 무상이 빠진다)."""
    from app.core.logistics_amount import TOTAL_EXPR

    bare = re.sub(r"\bl\.", "", FX.KRW_EXPR)
    assert TOTAL_EXPR in bare


# ── 단위·확정 여부 ───────────────────────────────────────────────────────

def test_the_briefing_fx_table_is_not_reused():
    """⛔ `fx_rates`(출근 브리핑)는 JPY 를 **100엔** 단위로 담는다. 이 표는 1엔이다 —
    섞으면 **에러 없이 100배** 틀린다."""
    from app.core import fx_rates

    assert fx_rates.UNITS.get("JPY") == 100
    src = open("app/core/logistics_fx.py", encoding="utf-8").read()
    assert "fx_rates" not in src.split('"""', 2)[2], "환산 경로가 브리핑 환율을 끌어다 쓴다"


def test_only_finished_months_are_confirmed():
    """월말환율은 그 달이 끝나야 정해진다 — 이번 달은 확정이 아니다."""
    assert FX.confirmed_through(date(2026, 9, 7)) == "2026-08"
    assert FX.confirmed_through(date(2026, 1, 3)) == "2025-12"


def test_the_confirmed_month_is_decided_by_the_calendar_not_the_table():
    """⛔ 표에 값이 있다고 확정이 아니다 — 실측: 2026-09·10 은 8월 값 복사본이다.
    조회가 죽어도 공시는 살아야 하므로 날짜로 판정한다."""
    import inspect

    assert "execute_query" not in inspect.getsource(FX.confirmed_through)


# ── 공시 ────────────────────────────────────────────────────────────────

CONVERTED_SQL = (
    f"SELECT SUM(x) krw FROM `{LOG_T}` l "
    "LEFT JOIN `skin1004-319714.Sales_Integration.Exchange_Rate` fx ON fx.Base = l.unit"
)


def test_a_converted_answer_states_its_basis():
    out = FX.notice(CONVERTED_SQL, "한화로 다 바꿔줘")
    assert "월말환율" in out and FX.BASIS_LABEL in out
    assert FX.confirmed_through() in out          # 어디까지가 확정인지
    assert out.startswith(">")                    # 표보다 먼저 오는 인용 블록


def test_a_plain_currency_query_gets_no_fx_notice():
    """⚠️ 환산하지 않은 답변에 붙으면 매번 뜨는 경고가 된다."""
    assert FX.notice(f"SELECT unit, SUM(amount) FROM `{LOG_T}` l GROUP BY unit", "") == ""


def test_the_cannot_convert_notice_yields_once_conversion_works():
    """⛔ 실제로 환산했는데 「환산해 드리지 못했습니다」가 함께 나가면 안 된다."""
    from app.core.logistics_amount import krw_notice

    assert krw_notice(CONVERTED_SQL, "한화로 다 바꿔줘") == ""
    not_converted = f"SELECT SUM(amount) FROM `{LOG_T}` l"
    assert "환산해 드리지 못했습니다" in krw_notice(not_converted, "한화로 다 바꿔줘")


# ── 배선 ────────────────────────────────────────────────────────────────

def test_the_rate_table_is_allowed_and_scoped():
    """⛔ 화이트리스트·소스맵 어느 한쪽이 빠지면 조회가 「허용되지 않은 테이블」로
    막히는데, 사용자 눈에는 **데이터가 없는 것**으로 보인다."""
    from app.agents.sql_agent import _allowed_tables_from_sources
    from app.config import get_settings

    assert FX.FX_TABLE in get_settings().allowed_tables
    assert FX.FX_TABLE in _allowed_tables_from_sources(["물류"])


def test_both_answer_paths_publish_the_fx_notice():
    """⚠️ 한쪽만 걸면 스트리밍이냐 아니냐로 답이 갈린다."""
    src = open("app/agents/sql_agent.py", encoding="utf-8").read()
    assert_every_answer_path_has(
        "from app.core.logistics_fx import notice as _log_fx_notice",
        "세는 단언이었다 — 경로가 늘면 뜻을 안 보고 숫자만 올리게 된다")
    assert_every_answer_path_has(
        "_log_fx_notice(sql, query)",
        "세는 단언이었다 — 경로가 늘면 뜻을 안 보고 숫자만 올리게 된다")


def test_the_rate_table_is_watched_for_freshness():
    """환율이 안 들어오면 환산이 조용히 낡는다."""
    from app.core.safety import _MONITORED_TABLES

    assert ("Sales_Integration", "Exchange_Rate") in _MONITORED_TABLES.values()


def test_no_stray_control_characters():
    """⛔ 정규식의 백스페이스 사고와 같은 부류 — 에러 없이 죽는다."""
    src = open("app/core/logistics_fx.py", encoding="utf-8").read()
    assert not [c for c in src if ord(c) < 32 and c not in "\n\r\t"]
