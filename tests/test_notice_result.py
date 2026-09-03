# -*- coding: utf-8 -*-
"""안내문 한 줄짜리 결과에 표를 지어내지 않는다 (붐따 #153).

2026-08-26 실제 답변. SQL 은 안내문 한 줄이 전부였는데 —

    SELECT '요청하신 … 문서는 시스템에 연동되어 있지 않습니다. …' AS notice LIMIT 1

답변에는 표가 붙었다: 반품 6,998개 · 자사몰 매출 321.2억 · 마케팅 예산 136.7억.
**조회 결과에 없는 숫자**를 "내부 데이터베이스에서 확인할 수 있는 지표" 라고
소개했다. 문서를 찾던 사람에게 출처 없는 수치가 사실처럼 나갔다.
"""
from app.core import notice_result as NR

NOTICE = ("요청하신 쇼피파이(자사몰) 글로벌e 전환 관련 문서는 시스템에 연동되어 "
          "있지 않습니다. 쇼피파이 매출이나 주문 수량 데이터가 필요하시면 "
          '"쇼피파이 매출" 등으로 다시 질문해 주세요.')


# ── 안내문으로 봐야 하는 것 ─────────────────────────────────────────────

def test_the_exact_row_from_the_incident_is_recognised():
    assert NR.extract([{"notice": NOTICE}]) == NOTICE


def test_other_notice_column_names_work():
    for col in ("message", "안내", "NOTICE", "Notice"):
        assert NR.extract([{col: NOTICE}]) == NOTICE


def test_empty_sibling_columns_do_not_block_it():
    """`SELECT '...' AS notice, NULL AS x` 같은 모양도 안내문이다."""
    assert NR.extract([{"notice": NOTICE, "x": None, "y": ""}]) == NOTICE


def test_the_rendered_answer_is_the_notice_itself():
    """⛔ 문장을 덧붙이지 않는다 — 덧붙이는 자리가 곧 지어내는 자리다."""
    out = NR.render(NOTICE)
    assert NOTICE in out
    # 안내문 밖의 내용이 붙지 않았다 (제목 한 줄만 허용)
    assert len(out) - len(NOTICE) < 20


# ── 안내문이 아닌 것 (여기가 더 위험하다) ───────────────────────────────

def test_a_row_with_real_data_is_not_swallowed():
    """⛔ 조건을 넓히면 진짜 데이터가 든 결과를 안내문으로 오인해 **표를 통째로
    삼킨다** — 못 찾는 것보다 나쁜 실패다."""
    assert NR.extract([{"notice": "참고", "매출": 100}]) is None


def test_multiple_rows_are_never_a_notice():
    assert NR.extract([{"notice": "x" * 20}, {"notice": "y" * 20}]) is None


def test_an_ordinary_result_is_untouched():
    assert NR.extract([{"국가": "일본", "매출": 100}]) is None
    assert NR.extract([{"month": "2026-01", "notice_count": 3}]) is None


def test_a_tiny_string_is_not_a_notice():
    assert NR.extract([{"notice": "N/A"}]) is None


def test_empty_and_none_are_safe():
    assert NR.extract(None) is None
    assert NR.extract([]) is None
    assert NR.extract([{}]) is None


# ── 배선 ────────────────────────────────────────────────────────────────

def _agent_src():
    with open("app/agents/sql_agent.py", encoding="utf-8") as fh:
        return fh.read()


def test_both_answer_paths_short_circuit():
    """⛔ 채팅은 스트리밍으로 나간다 — 한쪽만 고치면 실사용 경로에서 빠진다."""
    src = _agent_src()
    # ⚠️ 정의부(`def _notice_result_answer(results)`)도 같은 문자열을 담는다 —
    #    호출부만 세려면 대입 형태로 본다
    assert src.count("_notice_only = _notice_result_answer(results)") == 2


def test_the_llm_is_not_invoked_for_a_notice():
    """⛔ 이 처리의 전부는 **지어낼 여지를 없애는 것**이다. LLM 을 태우고
    「표는 만들지 마라」라고 부탁하는 것으로는 확률만 높아진다."""
    src = _agent_src()
    # 비스트리밍: 프롬프트를 만들기 전에 돌아간다
    i = src.index("_notice_only = _notice_result_answer(results)")
    j = src.index("prompt = f\"\"\"", i)
    assert i < j
    # 스트리밍: 답변 청크 루프보다 앞에서 끝낸다
    k = src.rindex("_notice_only = _notice_result_answer(results)")
    m = src.index("for chunk in _stream_with_table_totals(", k)
    assert k < m


def test_the_prompt_still_asks_for_this_sql_shape():
    """⚠️ 프롬프트가 이 SQL 을 생성하라고 시키는 한 이 처리가 있어야 한다.
    (프롬프트는 «포맷터가 이 notice 를 보고 안내함» 이라고 적어 두었는데,
    #153 당시 **그런 코드가 없었다** — 약속만 있고 구현이 없었다.)"""
    with open("prompts/sql_generator.txt", encoding="utf-8") as fh:
        prompt = fh.read()
    assert "AS notice" in prompt

# ── #153 의 나머지 절반 — "문서 찾아줘" 가 조회로 갔다 ──────────────────

def _route(q):
    from app.agents.orchestrator import OrchestratorAgent as O
    return O.__new__(O)._keyword_classify_ex(q)[0]


def test_a_document_request_goes_to_documents():
    """⛔ `_DOC_WORD` 에 「문서 어디 있어」는 있는데 **「문서 찾아줘」가 없었다.**
    그래서 붐따 #153 질문이 **확신을 갖고** bigquery 로 갔고, 확신 분류라
    LLM 재판정도 못 탔다."""
    assert _route("Ecomm팀 쇼피파이(자사몰) 글로벌e 전환 관련해서 문서 찾아줘") == "notion"
    for q in ("시딩 가이드라인 문서 찾아줘",
              "물류 프로세스 자료 공유해줘",
              "신규 입사자 교육 자료 있나요"):
        assert _route(q) == "notion", q


def test_a_number_question_still_goes_to_data():
    """⛔ 반대 방향이 더 중요하다 — 넓히다가 조회 질문을 문서로 보내면
    "시딩 비용 얼마" 에 문서를 찾아 온다 (`_QTY_INTENT` 가드가 지킨다)."""
    for q in ("시딩 비용 얼마야", "쇼피파이 매출 알려줘", "매출 자료 보여줘",
              "일본 월별 매출 추이 보여줘", "9월 수출건 알려줘"):
        assert _route(q) == "bigquery", q


def test_a_passing_mention_of_data_is_not_a_document_request():
    """⚠️ `문서`·`자료` 만 보면 "자료가 부족하다" 같은 말이 걸린다 —
    달라는 동사가 붙어야 요청이다."""
    assert _route("자료가 부족해서 8월 매출 알려줘") == "bigquery"
