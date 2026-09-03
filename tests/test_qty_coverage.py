# -*- coding: utf-8 -*-
"""수량을 셀 수 없는 브랜드에 `0` 을 답으로 내보내지 않는다 (붐따 #156·#157).

여기서 잡는 것은 **에러 없이 잘못 보이는** 실패다. `Total_Qty` 가 NULL 이 아니라
숫자 0 이라 `SUM()` 도 빈 결과 정규화도 걸리지 않는다 — 표에 `0`, 요약에
"총 판매수량 0개" 가 그대로 나가고, 사용자는 "안 팔렸다" 로 읽는다.
"""
import pytest

from app.core import qty_coverage as QC


def _rows(brand, qty_key="Total_Qty", n=2):
    return [{"Brand": brand, "SKU": f"S{i}", qty_key: 0, "Sales1_R": 1000}
            for i in range(n)]


SQL_ORDER = (
    "SELECT Date, Brand, SKU, Total_Qty, Sales1_R "
    "FROM `skin1004-319714.Sales_Integration.SALES_ALL_Backup` "
    "WHERE Order_Number = '202607080001'")


# ── 붙어야 하는 경우 ────────────────────────────────────────────────────

def test_um_rows_with_a_quantity_column_get_a_notice():
    """붐따의 그 답변 — UM 27행 · 수량 전부 0 · 요약은 '총 판매수량 0개'."""
    text = QC.notice(SQL_ORDER, _rows("UM"))
    assert "우마(UM)" in text
    assert "집계할 수 없습니다" in text
    # ⛔ 무엇이 사실인지 말해야 한다 — '0' 이 무슨 뜻인지 밝히지 않으면
    #    경고만 붙고 오해는 그대로다
    assert "팔리지 않았다는 뜻이 아니라" in text


def test_cbt_is_covered_too():
    """CBT 도 Product 에 행이 없다 (2026년 970,509행 · Total_Qty 합계 0)."""
    assert "CBT" in QC.notice(SQL_ORDER, _rows("CBT"))


def test_an_aggregate_without_a_brand_column_still_gets_the_notice():
    """결과에 Brand 컬럼이 없어도 SQL 이 그 브랜드로 좁혔으면 대상은 확실하다."""
    sql = ("SELECT SUM(Total_Qty) AS qty FROM `x.y.SALES_ALL_Backup` "
           "WHERE Brand = 'UM' AND Date >= '2026-01-01'")
    assert "우마(UM)" in QC.notice(sql, [{"qty": 0}])


def test_an_llm_aliased_quantity_column_is_recognised():
    """SQL 이 `Total_Qty AS 판매수량` 으로 별칭을 붙여도 수량 답변이다."""
    rows = [{"Brand": "UM", "판매수량": 0}]
    assert QC.notice("SELECT Brand, Total_Qty AS 판매수량 FROM t", rows)


def test_a_mixed_result_names_only_the_brands_that_cannot_be_counted():
    """SK 는 수량이 정상이다 — 함께 나왔다고 SK 까지 못 센다고 하면 거짓이다."""
    rows = _rows("SK") + _rows("UM")
    text = QC.notice(SQL_ORDER, rows)
    assert "우마(UM)" in text
    assert "스킨천사" not in text and "SK" not in text.replace("SKU", "")


# ── 붙으면 안 되는 경우 (매번 뜨는 경고는 곧 아무도 안 읽는다) ──────────

def test_a_revenue_only_answer_gets_no_notice():
    """⛔ UM 매출만 물었는데 '수량을 셀 수 없습니다' 가 나가면 그건 소음이다."""
    sql = ("SELECT Brand, Sales1_R FROM `x.y.SALES_ALL_Backup` "
           "WHERE Brand = 'UM'")
    assert QC.notice(sql, [{"Brand": "UM", "Sales1_R": 1000}]) == ""


def test_countable_brands_get_no_notice():
    assert QC.notice(SQL_ORDER, _rows("SK")) == ""
    assert QC.notice(SQL_ORDER, _rows("CL")) == ""


def test_logistics_quantity_never_triggers_this_notice():
    """⛔ 물류는 **발주·출고** 수량이고 매출은 **판매** 수량이라 다른 것이다
    (2026-09-03 사용자 확정). 물류 답변에 이 공시가 붙으면 사실이 아닌 말을 한다.

    ⚠️ `\\bquantity\\b` 가 `quantity_ea` 에 매치되지 않는 성질에 기대고 있다 —
       정규식을 손보면 여기서 걸린다.
    """
    sql = ("SELECT order_number, quantity_ea, country "
           "FROM `skin1004-319714.Export_control.export_logistics` "
           "WHERE NOT is_deleted")
    rows = [{"order_number": "202607080001", "quantity_ea": 5370,
             "country": "우루과이"}]
    assert QC.notice(sql, rows) == ""


def test_empty_inputs_are_safe():
    assert QC.notice("", None) == ""
    assert QC.notice(SQL_ORDER, []) == ""


# ── 프롬프트 사실 ───────────────────────────────────────────────────────

def test_the_prompt_fact_forbids_the_exact_sentence_that_went_out():
    """⛔ 공시만 붙이고 LLM 이 '총 판매수량 0개' 를 쓰면, 한 답변이 스스로와
    어긋난다. 보증은 공시가 하지만 문장도 줄여야 한다."""
    fact = QC.prompt_fact(SQL_ORDER, _rows("UM"))
    assert "0개" in fact and "쓰지 마세요" in fact
    assert "집계 불가" in fact


def test_the_prompt_fact_is_silent_when_the_notice_is():
    assert QC.prompt_fact(SQL_ORDER, _rows("SK")) == ""


# ── 배선 — 한쪽만 걸면 경로에 따라 답이 갈린다 ──────────────────────────

def _agent_src():
    with open("app/agents/sql_agent.py", encoding="utf-8") as fh:
        return fh.read()


def test_both_answer_paths_publish_the_notice():
    """⛔ 스트리밍·비스트리밍 **양쪽**에 걸어야 한다 — 채팅은 스트리밍으로
    나가므로 한쪽만 고치면 실사용 경로에서 조용히 빠진다 (`answer_check` 가
    비스트리밍에만 배선돼 계측조차 안 되던 그 사고와 같은 부류)."""
    src = _agent_src()
    assert src.count("from app.core.qty_coverage import notice as _qty_cov_notice") == 2
    assert "_qty_cov_notice(sql, results)" in src


def test_both_prompts_carry_the_fact():
    assert _agent_src().count("_qty_coverage_fact(sql, results)") == 2


def test_the_notice_leads_the_answer():
    """⛔ 표보다 먼저 말한다 — 뒤에 붙이면 후속 질문 제안·실행된 쿼리 뒤로
    밀려 숫자를 읽는 사람 눈에 닿지 않는다 (물류 공시가 실측으로 겪은 것)."""
    src = _agent_src()
    # ⚠️ 줄 전체를 박아 두지 마라 — 공시가 하나 늘 때마다 이 검사가 깨진다.
    #    지켜야 하는 것은 **순서**뿐이다
    i = src.index("_qty_cov_notice(sql, results)")
    j = src.index("_mask_internal_paths(answer)", i)
    assert i < j


def test_the_threshold_lives_in_one_place():
    """브랜드 목록의 사본을 만들지 마라 — 사본은 반드시 낡는다."""
    src = _agent_src()
    assert "UNCOUNTABLE_BRANDS" not in src
    assert set(QC.UNCOUNTABLE_BRANDS) == {"UM", "CBT"}
