# -*- coding: utf-8 -*-
"""달을 지목한 질문의 기간은 결정적이어야 한다 — 붐따 #143·#144 (박혜진, 2026-08-24).

같은 질문을 3분 간격으로 두 번 했더니 **총량이 1,805 와 11,865 로 갈렸다.**
답변 원문에서 꺼낸 SQL 두 개는 딱 한 군데가 달랐다:

  #143 (14:37)  Date BETWEEN '2026-08-01' AND '2026-08-24 23:59:59'   → 1,805
  #144 (14:40)  Date BETWEEN '2026-08-01' AND '2026-08-31 23:59:59'   → 11,865

제품 필터는 오히려 #143 이 더 넓었는데(`LIKE '%Azelaic_Acid%'`) 결과는 1/6 이었다 —
차이를 만든 것은 **기간의 끝**이다. "8월" 이라고 못 박은 질문인데 한쪽이 조용히
'오늘까지' 로 잘랐고, 그 사실을 답변 어디에도 적지 않았다.

⛔ 어느 쪽을 고르든 **매번 같아야** 한다. 달을 지목했으면 그 달 전체다.
   프롬프트에 "오늘 이후를 제외하라" 는 규칙(15항)이 있어 LLM 이 확률적으로 잘랐다 —
   프롬프트는 확률을 높일 뿐이고 보증은 코드가 한다.

⚠️ 기간 **범위**를 직접 말한 질문("8월 1일부터 8월 24일까지")은 건드리지 않는다.
   사용자가 스스로 자른 것이라 늘리면 묻지 않은 것을 답하게 된다.
"""
import pytest

from app.agents.sql_agent import _normalize_named_period as norm


def test_clamped_month_is_restored_to_the_whole_month():
    """#143 그 자체 — '8월' 이라고 했으면 8/31 까지다."""
    sql = ("SELECT Country, SUM(Total_Qty) FROM `p.d.Product` "
           "WHERE Date BETWEEN '2026-08-01 00:00:00' AND '2026-08-24 23:59:59' "
           "GROUP BY Country")
    out = norm(sql, "8월 기준 동남유럽, 서유럽에서 아젤라익애씨드 발주 국가비중 알려줘")
    assert "'2026-08-31 23:59:59'" in out
    assert "2026-08-24" not in out


def test_a_full_month_query_is_left_alone():
    """#144 는 이미 옳다 — 바꿀 것이 없다."""
    sql = "SELECT 1 FROM t WHERE Date BETWEEN '2026-08-01' AND '2026-08-31 23:59:59'"
    assert norm(sql, "8월 발주 국가비중") == sql


@pytest.mark.parametrize("q", [
    "8월 1일부터 8월 24일까지 발주 국가비중",
    "2026-08-01 ~ 2026-08-24 발주",
    "8월 24일까지 누적 발주",
])
def test_an_explicit_range_is_never_widened(q):
    """⛔ 사용자가 스스로 자른 기간을 늘리면 묻지 않은 것을 답하게 된다."""
    sql = "SELECT 1 FROM t WHERE Date BETWEEN '2026-08-01' AND '2026-08-24 23:59:59'"
    assert norm(sql, q) == sql


def test_month_word_must_actually_be_a_month():
    """'최근 3개월' 은 달을 지목한 것이 아니다."""
    sql = "SELECT 1 FROM t WHERE Date BETWEEN '2026-08-01' AND '2026-08-24 23:59:59'"
    assert norm(sql, "최근 3개월 발주 추이") == sql


def test_a_range_spanning_two_months_is_left_alone():
    """시작과 끝이 다른 달이면 '그 달 전체' 라는 판단이 성립하지 않는다."""
    sql = "SELECT 1 FROM t WHERE Date BETWEEN '2026-07-01' AND '2026-08-24'"
    assert norm(sql, "8월 발주") == sql


def test_lower_bound_must_be_the_first_of_the_month():
    """달 중간부터 시작하는 질의는 '그 달 전체' 가 아니다."""
    sql = "SELECT 1 FROM t WHERE Date BETWEEN '2026-08-05' AND '2026-08-24'"
    assert norm(sql, "8월 발주") == sql


def test_every_sql_generation_path_applies_it():
    """⛔ 한 경로만 고치면 재생성 때 다시 갈린다 — 팀·국가 교정과 같은 4경로다.

    이 프로젝트에서 반복된 사고다: 팀 값 오기가 4곳 중 1곳만 고쳐져 있었다.
    """
    import inspect

    from app.agents import sql_agent

    src = inspect.getsource(sql_agent)
    assert src.count("_normalize_named_period(") == 1 + 4  # 정의 1 + 호출 4


# ── 미래 날짜는 포함한다 — 대신 포함했다고 말한다 (2026-08-25 사용자 확정) ──────
# 발주·예정 물량은 미래 날짜가 정상이고, 그것이 사용자의 대시보드(product metrics)
# 기준이다. 그래서 자르지 않는다. ⛔ 다만 **말없이 포함하면** 매출 추이에 아직
# 일어나지 않은 달이 섞여도 아무도 모른다 — 자르는 것과 같은 종류의 조용한 오답이다.
# 포함은 하되 공시는 코드가 보증한다 (LLM 프롬프트에 맡기지 않는다).

def test_future_period_is_disclosed():
    from datetime import date, timedelta

    from app.agents.sql_agent import _future_period_note
    future = (date.today() + timedelta(days=20)).strftime("%Y-%m-%d")
    note = _future_period_note(
        f"SELECT 1 FROM t WHERE Date BETWEEN '2026-01-01' AND '{future} 23:59:59'")
    assert note, "미래까지 조회했는데 아무 말이 없다"
    assert future[:7] in note or "이후" in note


def test_past_only_period_says_nothing():
    """지난 기간만 봤으면 군더더기를 붙이지 않는다."""
    from app.agents.sql_agent import _future_period_note
    assert _future_period_note(
        "SELECT 1 FROM t WHERE Date BETWEEN '2025-01-01' AND '2025-12-31'") == ""


def test_disclosure_is_appended_by_code_not_left_to_the_llm():
    """⛔ 프롬프트에 맡기면 확률이다 — 답변 문자열에 코드가 붙인다."""
    import inspect

    from app.agents import sql_agent
    src = inspect.getsource(sql_agent.format_answer)
    assert "_future_period_note(" in src
