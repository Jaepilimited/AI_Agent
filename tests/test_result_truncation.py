# -*- coding: utf-8 -*-
"""표가 전체가 아니면 그렇다고 말한다 (붐따 #158·#160).

두 번 다 프롬프트는 이미 "일부 프리뷰"라고 알려주고 있었다. 그런데도 답변은
"총 8건" 이라고 **단정**했고, 영국 7건이 통째로 사라졌다. 프롬프트는 확률이다.
"""
from app.core import result_truncation as RT


def _rows(n):
    return [{"country": f"C{i}"} for i in range(n)]


# ── 붙어야 하는 경우 ────────────────────────────────────────────────────

def test_a_truncated_table_is_announced_with_the_true_row_count():
    """#160 — 실제 16행인데 표에는 8행이 실렸다."""
    text = RT.notice(_rows(16), rows_withheld=True)
    assert "16행" in text
    assert "전체가 아닙니다" in text


def test_the_notice_warns_against_the_exact_phrasing_that_went_out():
    """⛔ '총 N건' 단정이 이 결함의 얼굴이다 — 그 표현을 콕 집어 말한다."""
    text = RT.notice(_rows(86), rows_withheld=True)
    assert "총 N건" in text
    assert "86행" in text


def test_the_notice_points_at_the_csv():
    """화면 밖 행을 받을 실제 방법을 함께 준다 — 경고만 하면 할 게 없다."""
    assert "CSV" in RT.notice(_rows(16), rows_withheld=True)


def test_a_large_row_count_is_grouped_for_reading():
    assert "1,234행" in RT.notice(_rows(1234), rows_withheld=True)


# ── 붙으면 안 되는 경우 ─────────────────────────────────────────────────

def test_a_complete_table_gets_no_notice():
    """⛔ 피벗처럼 전부 담긴 결과에까지 붙으면 매번 뜨는 경고가 된다."""
    assert RT.notice(_rows(146), rows_withheld=False) == ""


def test_a_tiny_result_gets_no_notice():
    assert RT.notice(_rows(1), rows_withheld=True) == ""
    assert RT.notice([], rows_withheld=True) == ""


# ── 프롬프트 사실 ───────────────────────────────────────────────────────

def test_the_prompt_fact_forbids_asserting_a_total():
    fact = RT.prompt_fact(_rows(16), rows_withheld=True)
    assert "총 N건" in fact and "단정하지 마라" in fact
    # ⛔ 건수뿐 아니라 **목록**도 전부라고 말하면 안 된다 — #160 에서 사라진 것은
    #    숫자가 아니라 '영국' 이라는 값이었다
    assert "목록이 전부라고 말하지 마라" in fact
    assert "16행" in fact


def test_the_prompt_fact_is_silent_when_nothing_was_withheld():
    assert RT.prompt_fact(_rows(146), rows_withheld=False) == ""


# ── 배선 ────────────────────────────────────────────────────────────────

def _agent_src():
    with open("app/agents/sql_agent.py", encoding="utf-8") as fh:
        return fh.read()


def test_both_answer_paths_publish_the_notice():
    """⛔ 채팅은 스트리밍으로 나간다 — 한쪽만 걸면 실사용 경로에서 빠진다."""
    src = _agent_src()
    assert src.count("from app.core.result_truncation import notice as _trunc_notice") == 2
    assert src.count("_trunc_notice(results, _rows_withheld)") == 2


def test_both_prompts_carry_the_fact():
    assert _agent_src().count("_truncation_fact(results, _rows_withheld)") == 2


def test_the_notice_leads_the_answer():
    """표보다 먼저 말한다 — 뒤에 붙으면 실행된 쿼리 뒤로 밀려 안 읽힌다."""
    src = _agent_src()
    i = src.index("+ _trunc_notice(results, _rows_withheld)")
    j = src.index("_mask_internal_paths(answer)", i)
    assert i < j
