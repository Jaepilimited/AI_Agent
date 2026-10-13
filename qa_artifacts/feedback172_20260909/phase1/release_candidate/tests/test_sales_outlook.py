"""Feedback 172: occupied dates and future bookings cannot define elapsed sales."""

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core import sales_outlook as outlook


QUESTION = "이번달 전사 예상실적이 얼마로 마감할거 같아?"
AS_OF = date(2026, 9, 8)


@pytest.fixture
def split_row():
    return {
        "outlook_period_start": "2026-09-01",
        "outlook_period_end": "2026-09-30",
        "outlook_as_of": "2026-09-08",
        "outlook_row_count": 16000,
        "revenue_through_as_of": Decimal("12198894347.2"),
        "revenue_after_as_of": Decimal("53456435789.2"),
        "revenue_in_period": Decimal("65655330136.4"),
    }


@pytest.mark.parametrize("query", [
    QUESTION,
    "이번 달 매출 마감 예상 알려줘",
    "전사 2026년 9월 매출 전망은?",
    "9월 일본사업팀 예상매출 알려줘",
    "인도네시아 이번달 매출이 얼마로 마감할 것 같아?",
    "월말 전사 매출이 얼마 될까?",
    "What is the company-wide revenue forecast for this month?",
    "What will total sales close at this month?",
    "monthly revenue forecast",
])
def test_month_close_intent_includes_scoped_and_original_queries(query):
    assert outlook.is_month_close_query(query)


@pytest.mark.parametrize("query", [
    "이번달 전사 매출 알려줘",
    "2025년 9월 마감 매출 얼마였어?",
    "오늘자 누적매출 알려줘",
    "지난달 실적을 알려줘",
    "이번달 전사 예상 발주 수량",
    "9월 매출과 수량 마감 예상",
    "이번달 재고 예상",
    "이번달 광고 실적 전망",
    "프로모션 캘린더 이번달 예상 실적",
    "이번달 매출 예상이 적힌 메일 찾아줘",
    "회사 전체 올해 매출 마감 예상",
    "이번 분기 매출 마감 예상",
    "company-wide annual revenue forecast",
    "monthly orders forecast",
    "sales forecast email for this month",
    "What's the weather forecast for this month?",
])
def test_other_questions_are_not_replaced_with_a_sales_close_answer(query):
    assert not outlook.is_month_close_query(query)
    assert outlook.render_answer(query, "", [{"sales": 100}], today=AS_OF) is None


def test_original_builds_full_month_sql_with_independent_dated_sums(monkeypatch):
    from app import config

    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(
        sales_table_full_path="test-project.Sales_Integration.SALES_ALL_Backup",
    ))
    sql = outlook.build_company_sql(QUESTION, today=AS_OF)
    assert "FROM `test-project.Sales_Integration.SALES_ALL_Backup`" in sql
    assert "DATE '2026-09-01' AS outlook_period_start" in sql
    assert "DATE '2026-09-30' AS outlook_period_end" in sql
    assert "DATE '2026-09-08' AS outlook_as_of" in sql
    assert "CASE WHEN DATE(Date) <= DATE '2026-09-08'" in sql
    assert "CASE WHEN DATE(Date) > DATE '2026-09-08'" in sql
    where = sql.split("WHERE", 1)[1]
    assert "Date >= DATETIME '2026-09-01 00:00:00'" in where
    assert "Date < DATETIME '2026-10-01 00:00:00'" in where
    assert "2026-09-08" not in where
    assert "COUNT(*) AS outlook_row_count" in sql
    assert "COUNT(DISTINCT" not in sql
    assert "SAFE_DIVIDE" not in sql and " / " not in sql
    assert sql.endswith("LIMIT 1000")


@pytest.mark.parametrize("query,start,end,next_start", [
    ("전사 2026년 2월 매출 마감 예상", "2026-02-01", "2026-02-28", "2026-03-01"),
    ("전사 2024년 2월 매출 마감 예상", "2024-02-01", "2024-02-29", "2024-03-01"),
    ("12월 전사 매출 예상", "2026-12-01", "2026-12-31", "2027-01-01"),
    ("월말 전사 매출 예상", "2026-09-01", "2026-09-30", "2026-10-01"),
    ("What is the company-wide revenue forecast for this month?", "2026-09-01", "2026-09-30", "2026-10-01"),
])
def test_calendar_month_boundaries_are_literal_and_complete(query, start, end, next_start):
    sql = outlook.build_company_sql(query, today=AS_OF)
    assert f"DATE '{start}' AS outlook_period_start" in sql
    assert f"DATE '{end}' AS outlook_period_end" in sql
    assert f"DATETIME '{next_start} 00:00:00'" in sql


@pytest.mark.parametrize("query", [
    "이번달 예상매출 알려줘",
    "그럼 이번달 예상매출은?",
    "이번달 인도네시아 전체 매출 마감 예상",
    "이번달 전사 매출 국가별 마감 예상",
    "9월 일본사업팀 예상매출 알려줘",
    "전사 이번달 미국 제외 매출 마감 예상",
    "이번달 전사 스킨천사 매출 마감 예상",
    "이번달 전사 앰플 매출 마감 예상",
    "이번달 전사 B2B 매출 마감 예상",
    "이번달 전사 순매출 마감 예상",
    "이번달 전사 매출 마감 예상 9월 7일 기준",
    "2026년 9월 1일부터 16일까지 전사 매출 예상",
    "전사 9월 10월 매출 전망",
    "전사 이번달 9월 매출 전망",
    "2026년 13월 전사 매출 마감 예상",
    "company-wide US revenue forecast this month",
    "company-wide revenue forecast this month by country",
    "this month total revenue forecast excluding returns",
])
def test_unknown_scope_and_modifiers_never_disappear_into_company_sql(query):
    assert outlook.build_company_sql(query, today=AS_OF) is None


def test_access_brand_restriction_is_preserved_even_for_explicit_company():
    sql = outlook.build_company_sql(QUESTION, brand_filter="SK, CL,CBT,SK", today=AS_OF)
    assert "AND Brand IN ('SK', 'CL', 'CBT')" in sql


@pytest.mark.parametrize("restriction", ["SK'); DROP TABLE x; --", "SK,", ",", " ", "SK,UM OR 1=1", "스킨천사", "SK_1", ["SK"]])
def test_invalid_access_restriction_never_creates_unrestricted_sql(restriction):
    with pytest.raises(ValueError, match="brand restriction"):
        outlook.build_company_sql(QUESTION, brand_filter=restriction, today=AS_OF)


def test_default_calendar_day_is_korea_time_at_utc_rollover(monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 8, 15, 1, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(outlook, "datetime", FrozenDatetime)
    sql = outlook.build_company_sql(QUESTION)
    assert "DATE '2026-09-09' AS outlook_as_of" in sql


def test_incident_amounts_are_reconciled_without_invented_sixteenth_or_forecast(split_row):
    split_row.update({
        "days_with_sales": 16,
        "estimated_revenue": 123103744006,
        "revenue_to_date": 65655330136.4,
    })
    answer = outlook.render_answer(QUESTION, "old schema sql", [split_row], today=AS_OF)
    assert "2026-09-01 ~ 2026-09-30" in answer
    assert "2026-09-08" in answer and "2026-09-09 ~ 2026-09-30" in answer
    assert "기준일까지 누적매출 | 약 122.0억 원 (12,198,894,347원)" in answer
    assert "이후 날짜 등록분 | 약 534.6억 원 (53,456,435,789원)" in answer
    assert "월 전체 등록 합계 | 약 656.6억 원 (65,655,330,136원)" in answer
    assert "확정된 마감 예상치가 아닙니다" in answer
    assert "아직 등록되지 않은 매출" in answer and "취소" in answer and "날짜 변경" in answer
    assert "2026-09-16" not in answer and "16일" not in answer
    assert "1,231" not in answer and "123,103" not in answer
    assert "revenue_" not in answer and "schema" not in answer


def test_a_complete_contract_identifies_a_rewritten_query(split_row):
    answer = outlook.render_answer("9월 등록분을 조회해줘", "", [split_row], today=AS_OF)
    assert "656.6억" in answer


def test_future_month_keeps_registered_total_without_dividing_by_elapsed_days(split_row):
    split_row.update({
        "outlook_period_start": "2026-10-01", "outlook_period_end": "2026-10-31",
        "revenue_through_as_of": 0, "revenue_after_as_of": 500000000, "revenue_in_period": 500000000,
    })
    answer = outlook.render_answer("전사 10월 매출 전망", "", [split_row], today=AS_OF)
    assert "조회 월은 기준일 이후" in answer
    assert "기준일까지 누적매출 | 0원" in answer
    assert "월 전체 등록 합계 | 약 5.0억 원" in answer
    assert "경과" not in answer


def test_negative_refunds_remain_negative_and_reconcile(split_row):
    split_row.update({"revenue_through_as_of": -120000000, "revenue_after_as_of": 30000000, "revenue_in_period": -90000000})
    answer = outlook.render_answer(QUESTION, "", [split_row], today=AS_OF)
    assert "약 -1.2억 원 (-120,000,000원)" in answer
    assert "30,000,000원" in answer and "-90,000,000원" in answer


def test_zero_sales_with_registered_rows_is_not_confused_with_missing_records(split_row):
    split_row.update({"revenue_through_as_of": 0, "revenue_after_as_of": 0, "revenue_in_period": 0})
    answer = outlook.render_answer(QUESTION, "", [split_row], today=AS_OF)
    assert "월 전체 등록 합계 | 0원" in answer
    assert "등록 데이터가 없습니다" not in answer


@pytest.mark.parametrize("empty_result", [True, False])
def test_no_records_is_reported_without_claiming_a_zero_close(split_row, empty_result):
    split_row.update({"outlook_row_count": 0, "revenue_through_as_of": 0, "revenue_after_as_of": 0, "revenue_in_period": 0})
    answer = outlook.render_answer(QUESTION, "", [] if empty_result else [split_row], today=AS_OF)
    assert "등록 데이터가 없습니다" in answer
    assert "0원" not in answer


@pytest.mark.parametrize("update", [
    {"outlook_period_start": "2026-09-02"},
    {"outlook_period_end": "2026-09-16"},
    {"outlook_period_end": "2026-10-31"},
    {"outlook_as_of": "2026-09-16"},
    {"outlook_as_of": "not a date"},
    {"outlook_row_count": -1},
    {"outlook_row_count": 1.5},
    {"outlook_row_count": True},
    {"outlook_row_count": 0},
    {"revenue_in_period": Decimal("NaN")},
    {"revenue_after_as_of": float("inf")},
    {"revenue_through_as_of": True},
    {"revenue_through_as_of": None},
    {"revenue_through_as_of": 65655330136.4},
    {"revenue_in_period": 123103744006},
    {"outlook_as_of": "2026-08-31"},
    {"outlook_as_of": "2026-09-30"},
])
def test_invalid_contract_suppresses_all_original_forecast_numbers(split_row, update):
    split_row.update(update)
    answer = outlook.render_answer(QUESTION, "", [split_row], today=date(2026, 10, 1) if update.get("outlook_as_of") == "2026-09-30" else AS_OF)
    assert "같은 기준으로 확인되지 않아" in answer
    assert not any(char.isdigit() for char in answer)
    assert "revenue_" not in answer


def test_faulty_original_result_without_contract_is_not_presented_as_a_forecast():
    bad = {"revenue_to_date": 65655330136.4, "days_with_sales": 16, "estimated_revenue": 123103744006}
    answer = outlook.render_answer(QUESTION, "", [bad], today=AS_OF)
    assert "같은 기준으로 확인되지 않아" in answer
    assert not any(char.isdigit() for char in answer)


def test_float_roundtrip_tolerance_is_small_but_not_zero(split_row):
    split_row["revenue_in_period"] = 65655330136.40001
    assert "656.6억" in outlook.render_answer(QUESTION, "", [split_row], today=AS_OF)
    split_row["revenue_in_period"] = Decimal("65655330146.4")
    assert "같은 기준으로 확인되지 않아" in outlook.render_answer(QUESTION, "", [split_row], today=AS_OF)


def test_group_labels_preserve_each_scope_and_ignore_numeric_estimates(split_row):
    us = dict(split_row, Country="미국", estimated_revenue=999999999999)
    au = dict(split_row, Country="호주", forecast=999999999999)
    answer = outlook.render_answer("이번달 국가별 매출 마감 예상", "", [us, au], today=AS_OF)
    assert "국가: 미국" in answer and "국가: 호주" in answer
    assert "999,999" not in answer and "forecast" not in answer
    assert answer.count("656.6억") == 2
    assert "1,313" not in answer


def test_duplicate_or_unlabelled_groups_are_not_silently_added(split_row):
    answer = outlook.render_answer(QUESTION, "", [split_row, dict(split_row)], today=AS_OF)
    assert "같은 기준으로 확인되지 않아" in answer


def test_group_labels_cannot_inject_tables_or_links(split_row):
    split_row["outlook_label"] = "A|B\n[open](https://example.com)<script>"
    answer = outlook.render_answer(QUESTION, "", [split_row], today=AS_OF)
    assert "A&#124;B" in answer
    assert "[open]" not in answer and "<script>" not in answer


def test_prompt_contract_preserves_scope_and_defines_the_same_required_fields():
    assert "조건" in outlook.SQL_PROMPT_RULE and "접근 가능한 브랜드 제한" in outlook.SQL_PROMPT_RULE
    for field in outlook._CONTRACT:
        assert field in outlook.SQL_PROMPT_RULE
    assert "COUNT(DISTINCT DATE(Date))" in outlook.SQL_PROMPT_RULE
    assert "경과 일수나 기준일이 아니다" in outlook.SQL_PROMPT_RULE
