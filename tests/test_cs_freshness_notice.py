# -*- coding: utf-8 -*-
"""CS 자료가 낡았으면 표보다 먼저 말한다 — 붐따 #184 (2026-10-07).

제보는 링크 한 줄과 한 문장이었다:

    http://34.64.99.254:8061/dashboard?startDate=2026-09-01&endDate=2026-09-30
    대시보드내 수치와 다른 결과값이 나옵니다

실측(2026-10-08)으로 원인을 갈랐다 — **날짜 상한 문제가 아니었다**
(`received_at` 은 DATE 라 어떤 표기로 걸어도 같은 28을 준다). 적재가 멈춰 있었다:

    대시보드(시트 직접 읽기)  9월 54건 · 마지막 2026-09-30
    셀라 테이블               9월 28건 · 마지막 **2026-09-16**  ← 3주 멈춤

⛔ ETL 은 우리 것이 아니다. 우리가 할 수 있는 것은 **낡은 것을 낡았다고 말하는 것**
   이고, 조용히 낡은 숫자를 자신 있게 답한 것이 제보자가 본 바로 그 실패다.
"""

import datetime as dt

from app.core import cs_freshness
from app.core.cs_metrics import DOMESTIC_TABLE, OVERSEAS_TABLE, notice

TODAY = dt.date(2026, 10, 8)
COVERAGE = dt.date(2026, 9, 16)          # 실측된 그 날짜

# 제보된 기간을 묻는 SQL (프로덕션이 만든 것과 같은 모양)
SQL_SEPT = f"""
SELECT claim_type, COUNT(*) AS record_count
FROM `{DOMESTIC_TABLE}`
WHERE received_at BETWEEN '2026-09-01' AND '2026-09-30'
GROUP BY claim_type
"""


def _fetcher(value):
    return lambda expr: value


def setup_function(_):
    cs_freshness._cache.clear()


def teardown_function(_):
    cs_freshness._cache.clear()


# ── 제보된 그 질문 ───────────────────────────────────────────────────────────

def test_the_reported_question_is_told_where_the_data_ends():
    out = cs_freshness.notice_for_sql(SQL_SEPT, today=TODAY, fetcher=_fetcher(COVERAGE))

    assert "2026-09-16" in out, "적재 끝일을 말하지 않았다 — 제보된 그 침묵이다"
    assert "14일" in out, "빠진 구간의 크기를 말하지 않았다"
    assert "대시보드" in out, "왜 대시보드와 다른지 밝히지 않았다"


def test_it_rides_the_hook_every_answer_path_already_uses():
    """⛔ 경로마다 따로 붙이면 한쪽만 고쳐져 답이 갈린다 — `notice()` 하나에 싣는다."""
    cs_freshness._cache[DOMESTIC_TABLE] = (float("inf"), COVERAGE)

    out = notice(SQL_SEPT)

    assert "2026-09-16" in out
    # 기존 안내도 그대로 남는다 (덮어쓰지 않는다)
    assert "CS 처리 기록" in out
    # 적재 공시가 **맨 앞**이다 — 숫자가 왜 다른지부터 답한다
    assert out.index("2026-09-16") < out.index("CS 처리 기록")


# ── 스스로 꺼진다 (매일 뜨는 경고는 곧 아무도 안 읽는다) ──────────────────────

def test_it_goes_quiet_once_the_etl_catches_up():
    out = cs_freshness.notice_for_sql(
        SQL_SEPT, today=TODAY, fetcher=_fetcher(dt.date(2026, 10, 7)))
    assert out == ""


def test_a_period_fully_inside_the_coverage_says_nothing():
    sql = SQL_SEPT.replace("'2026-09-30'", "'2026-09-10'")
    out = cs_freshness.notice_for_sql(sql, today=TODAY, fetcher=_fetcher(COVERAGE))
    assert out == ""


def test_a_future_upper_bound_alone_does_not_fire():
    """⚠️ 아직 오지 않은 기간이 비어 있는 것은 적재 결손이 아니다
    (`_future_period_note` 가 맡는 자리다 — 두 번 말하면 둘 다 안 읽힌다)."""
    sql = SQL_SEPT.replace("'2026-09-30'", "'2026-12-31'")
    out = cs_freshness.notice_for_sql(
        sql, today=TODAY, fetcher=_fetcher(dt.date(2026, 10, 8)))
    assert out == ""


# ── 기간을 안 적은 질문 ──────────────────────────────────────────────────────

def test_a_question_without_dates_fires_only_when_well_behind():
    sql = f"SELECT COUNT(*) AS record_count FROM `{DOMESTIC_TABLE}`"

    quiet = cs_freshness.notice_for_sql(
        sql, today=TODAY, fetcher=_fetcher(dt.date(2026, 10, 7)))
    assert quiet == "", "하루 지연은 정상 적재 지연이다"

    cs_freshness._cache.clear()
    loud = cs_freshness.notice_for_sql(sql, today=TODAY, fetcher=_fetcher(COVERAGE))
    assert "2026-09-16" in loud and "22일 전" in loud


def test_current_date_counts_as_asking_through_today():
    sql = (f"SELECT COUNT(*) AS record_count FROM `{DOMESTIC_TABLE}` "
           "WHERE received_at >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)")
    out = cs_freshness.notice_for_sql(sql, today=TODAY, fetcher=_fetcher(COVERAGE))
    assert "2026-09-16" in out


# ── 안전한 쪽으로 실패한다 ───────────────────────────────────────────────────

def test_a_failed_probe_says_nothing():
    """⚠️ 조회가 죽었다고 멀쩡한 답변에 경고를 붙이지 않는다."""
    def boom(expr):
        raise RuntimeError("bigquery down")

    assert cs_freshness.notice_for_sql(SQL_SEPT, today=TODAY, fetcher=boom) == ""
    assert cs_freshness.coverage_end(DOMESTIC_TABLE, fetcher=boom) is None


def test_an_unknown_coverage_says_nothing():
    assert cs_freshness.notice_for_sql(
        SQL_SEPT, today=TODAY, fetcher=_fetcher(None)) == ""


def test_a_non_cs_query_is_untouched():
    sql = "SELECT SUM(Sales1_R) FROM `p.Sales_Integration.SALES_ALL_Backup`"
    assert cs_freshness.notice_for_sql(sql, today=TODAY, fetcher=_fetcher(COVERAGE)) == ""
    assert notice(sql) == ""


# ── 기준 컬럼은 프롬프트가 쓰라고 한 것과 같아야 한다 ────────────────────────

def test_the_basis_column_matches_the_prompt():
    """⛔ 다르면 조회가 고른 행과 공시가 센 행이 어긋나고 티가 안 난다."""
    assert cs_freshness._BASIS[DOMESTIC_TABLE][0] == "received_at"
    # 해외는 `received_at` 이 NULL 가능이라 환불 생성시각이 기본 기준이다
    assert "refund_created_at" in cs_freshness._BASIS[OVERSEAS_TABLE][0]
    assert "Asia/Seoul" in cs_freshness._BASIS[OVERSEAS_TABLE][0]


def test_the_probe_is_cached_so_answers_do_not_pay_for_it_twice():
    calls = []

    def counting(expr):
        calls.append(expr)
        return COVERAGE

    cs_freshness.coverage_end(DOMESTIC_TABLE, fetcher=counting)
    cs_freshness.coverage_end(DOMESTIC_TABLE, fetcher=counting)
    assert len(calls) == 1


# ── 자가 점검 ───────────────────────────────────────────────────────────────

def test_the_daily_check_reports_the_stalled_table():
    stale = cs_freshness.stale_tables(today=TODAY, fetcher=_fetcher(COVERAGE))

    assert {r["table"] for r in stale} == {DOMESTIC_TABLE, OVERSEAS_TABLE}
    assert all(r["days_behind"] == 22 for r in stale)


def test_the_daily_check_is_quiet_when_fresh():
    assert cs_freshness.stale_tables(
        today=TODAY, fetcher=_fetcher(dt.date(2026, 10, 7))) == []


def test_a_failed_probe_is_not_counted_as_stale():
    def boom(expr):
        raise RuntimeError("down")

    assert cs_freshness.stale_tables(today=TODAY, fetcher=boom) == []


def test_the_check_is_registered():
    from app.core.self_check import CHECKS

    ids = {c.id for c in CHECKS}
    assert "cs_data_freshness" in ids, "매일 돌지 않으면 또 3주를 모른다"
