"""붐따 #181·#183 회귀 (2026-10-06).

#181 — 주제어 없는 후속("정확한 일자 알려줘")이 노션 검색에서 앞 질문을 잃었다.
#183 — 전사·국가별·팀별 행을 한 표에 쌓은 결과를 요약·합계가 더해 약 3배로 냈고,
       6일치 이번 달을 30일치 지난달과 그대로 견줬다.
"""
import os
from datetime import date

from app.agents.orchestrator import _is_contextless_followup, _notion_search_query
from app.agents.sql_agent import (
    _additive_totals,
    _build_table_totals_markdown,
    _fast_summary_line,
    _partial_month_note,
    _period_notes,
    _stacked_breakdown_axis,
)
from app.core.feedback_inbox import DEPLOYED_FEEDBACK_RESOLUTIONS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------- #181
def test_contextless_followups_are_detected():
    for q in ("정확한 일자 알려줘", "정확한 날짜는?", "자세히 알려줘", "그거 링크 줘"):
        assert _is_contextless_followup(q), q


def test_questions_with_a_subject_are_left_alone():
    for q in ("회사 창립일 언제야?", "연차 규정 알려줘", "휴가 신청은?", "시딩 가이드라인 알려줘"):
        assert not _is_contextless_followup(q), q


def test_search_query_carries_previous_question():
    msgs = [
        {"role": "user", "content": "회사 창립일 언제야?"},
        {"role": "assistant", "content": "2014년 8월입니다."},
        {"role": "user", "content": "정확한 일자 알려줘"},
    ]
    assert _notion_search_query("정확한 일자 알려줘", msgs) == "회사 창립일 언제야? 정확한 일자 알려줘"


def test_independent_question_is_not_contaminated():
    msgs = [
        {"role": "user", "content": "회사 창립일 언제야?"},
        {"role": "assistant", "content": "2014년 8월입니다."},
        {"role": "user", "content": "연차 규정 알려줘"},
    ]
    assert _notion_search_query("연차 규정 알려줘", msgs) == "연차 규정 알려줘"


def test_qdrant_handler_uses_the_search_query_helper():
    # 스트리밍·비스트리밍 모두 _handle_qdrant 를 지나므로 이 한 곳이 두 경로를 덮는다
    src = open(os.path.join(ROOT, "app", "agents", "orchestrator.py"), encoding="utf-8").read()
    body = src.split("async def _handle_qdrant", 1)[1].split("\n    async def ", 1)[0]
    assert "_notion_search_query(query, messages)" in body


# ---------------------------------------------------------------- #183
STACKED = [
    {"구분": "전사 요약", "항목": "전체", "sales_krw": 705_742_573_745},
    {"구분": "국가별 비교", "항목": "미국", "sales_krw": 400_000_000_000},
    {"구분": "국가별 비교", "항목": "일본", "sales_krw": 305_742_573_745},
    {"구분": "팀별 비교", "항목": "KBT", "sales_krw": 705_742_573_745},
]


def test_stacked_breakdown_is_detected():
    assert _stacked_breakdown_axis(STACKED) == "구분"


def test_stacked_table_gets_no_totals_and_says_why():
    _, totals = _additive_totals(STACKED)
    assert totals == {}
    block = _build_table_totals_markdown(STACKED)
    assert "합계를 내지 않았습니다" in block
    assert "2,117" not in block


def test_stacked_table_gets_no_summed_summary():
    assert _fast_summary_line(STACKED) == ""


def test_plain_breakdown_still_sums():
    rows = [{"country": "미국", "sales_krw": 60_000}, {"country": "일본", "sales_krw": 40_000}]
    assert _stacked_breakdown_axis(rows) is None
    assert "100,000" in _fast_summary_line(rows)
    _, totals = _additive_totals(rows)
    assert totals == {"sales_krw": 100_000}


def test_single_breakdown_label_is_not_stacked():
    # 「국가별」 한 종류뿐이면 같은 단위다
    rows = [{"구분": "국가별", "항목": "미국", "v": 1}, {"구분": "국가별", "항목": "일본", "v": 2}]
    assert _stacked_breakdown_axis(rows) is None


TODAY = date(2026, 10, 6)
SQL = "SELECT 1 FROM t WHERE Date BETWEEN '2026-09-01' AND '2026-10-31'"


def test_partial_month_note_fires_on_month_comparison():
    note = _partial_month_note("이번 달 매출 전월 대비 어때?", SQL, TODAY)
    assert "6일치" in note and "같은 기간(1~6일)" in note
    assert _partial_month_note("10월 매출 9월이랑 비교해줘", SQL, TODAY)


def test_partial_month_note_stays_quiet_otherwise():
    assert not _partial_month_note("이번 달 매출 알려줘", SQL, TODAY)          # 비교가 아니다
    assert not _partial_month_note("2025년 10월 매출 전년 대비", SQL, TODAY)    # 다른 해
    past = "SELECT 1 FROM t WHERE Date BETWEEN '2026-08-01' AND '2026-09-30'"
    assert not _partial_month_note("이번 달 매출 전월 대비", past, TODAY)       # 이번 달을 안 봤다


def test_every_path_uses_period_notes():
    src = open(os.path.join(ROOT, "app", "agents", "sql_agent.py"), encoding="utf-8").read()
    assert "_future_period_note(sql)" not in src
    assert src.count("_period_notes(query, sql)") >= 5
    assert _period_notes("이번 달 전월 대비", SQL, TODAY).count("⚠️") == 2


# ---------------------------------------------------------------- 처리 표시
def test_resolutions_registered():
    got = {r["id"]: r["created_on"] for r in DEPLOYED_FEEDBACK_RESOLUTIONS}
    assert got.get(181) == "2026-09-30"
    assert got.get(183) == "2026-10-06"
