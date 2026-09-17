"""Order CS lookup regressions: preserve dates, invoice arrays, and every claim."""
import re
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from google.cloud.bigquery.table import Row

from app.core import cs_order_details as cs


ORDER = "2026081423496661"


def _record(order_no=ORDER, claim_type="return"):
    return {"order_no": order_no, "channel": "스마트스토어", "claim_type": claim_type,
            "process_status": "completed", "received_at": date(2026, 8, 20),
            "completed_at": date(2026, 8, 24), "reason_normalized": "파손·불량",
            "reasons": ["파손 및 불량"], "reason_details": ["상품 박스 불량"],
            "outbound_invoice_nos": ["537418082563"], "return_invoice_nos": ["573867634314"],
            "compensation_amount": None,
            "items": [{"product_name": "히알루-시카 워터핏 선 세럼 50ml", "quantity": 1}],
            # A permissive fake provider must not cause accidental raw exports.
            "record_id": "PRIVATE-INTERNAL-RECORD", "order_key": "PRIVATE-INTERNAL-ORDER",
            "raw_rows": [{"values": ["PRIVATE-RAW"]}], "customer_email": "private@example.com"}


@pytest.fixture
def provider(monkeypatch):
    rows = [_record()]
    calls = []
    def execute(sql, **kwargs):
        calls.append((sql, kwargs))
        return [dict(row) for row in rows]
    monkeypatch.setattr(cs, "get_bigquery_client", lambda: SimpleNamespace(execute_query=execute))
    return rows, calls


@pytest.mark.parametrize("question,order_no,channel", [
    ("주문번호 2026082224631661 CS 정보좀", "2026082224631661", None),
    ("주문번호 139515930570616967 CS 정보 알려줘. (접수일 언제인지 ,출고송장 번호는 뭔지 등)", "139515930570616967", None),
    ("네이버 주문정보 2026081423496661 CS 데이터 알려줘.", ORDER, "스마트스토어"),
    ("2026081423496661 CS 정보 알려줘.", ORDER, None),
])
def test_reported_queries_read_exact_external_order_for_normal_account(question, order_no, channel, provider):
    rows, calls = provider
    rows[0]["order_no"] = order_no
    assert cs.is_order_detail_query(question)
    answer = cs.answer_query_sync(question, brand_filter="SK,CL,CBT")
    sql, options = calls[0]
    assert f"WHERE order_no = '{order_no}'" in sql
    assert options == {"timeout": 30, "max_rows": 101}
    assert "LIMIT 101" in sql and "LIKE" not in sql and "BETWEEN" not in sql
    assert ("AND channel = '스마트스토어'" in sql) is bool(channel)
    assert "출고 송장번호 | 537418082563" in answer
    assert "회수 송장번호 | 573867634314" in answer
    assert "접수일 | 2026-08-20" in answer and "완료일 | 2026-08-24" in answer
    assert "국내 CS 상세 기록" in answer and order_no in answer
    assert cs.DASHBOARD_URL in answer and cs.DASHBOARD_URL + "?" not in answer
    assert "PRIVATE" not in answer and "private@example.com" not in answer
    assert "record_id" not in sql and "order_key" not in sql and "raw_rows" not in sql
    assert "compensation_amount" not in sql and "원본 보상 기록액" not in answer
    assert "SELECT *" not in sql and "DISTINCT" not in sql


def test_collection_record_returns_real_invoice_dates_and_three_products(provider):
    rows, _ = provider
    record = rows[0]
    record.update(order_no="139515930570616967", channel="지그재그", claim_type="collection",
                  received_at="2025-12-16", completed_at="2025-12-22",
                  outbound_invoice_nos=["540658923226"], return_invoice_nos=["844325444023"],
                  items=[{"product_name": name, "quantity": 1} for name in ("캡슐 앰플", "부스팅 토너", "클렌징 젤 폼")])
    answer = cs.answer_query_sync("주문번호 139515930570616967 CS 정보 알려줘. (접수일 언제인지 ,출고송장 번호는 뭔지 등)")
    for text in ("540658923226", "844325444023", "2025-12-16", "2025-12-22", "캡슐 앰플", "부스팅 토너", "클렌징 젤 폼"):
        assert text in answer
    assert "접수 유형 | 수거" in answer
    assert "collection" not in answer and "completed" not in answer


def test_both_claims_and_null_product_quantity_are_preserved(provider):
    rows, calls = provider
    redelivery = _record(claim_type="redelivery")
    redelivery.update(items=[{"product_name": None, "quantity": None}], reason_details=[],
                      outbound_invoice_nos=[], return_invoice_nos=[])
    rows.append(redelivery)
    answer = cs.answer_query_sync(f"네이버 주문정보 {ORDER} CS 데이터 알려줘.")
    assert "처리 기록 **2건**" in answer
    assert "처리 기록 1 · 반품" in answer and "처리 기록 2 · 재배송" in answer
    assert "| 미기재 | 미기재 |" in answer and "상품 박스 불량" in answer
    assert "히알루-시카 워터핏 선 세럼 50ml" in answer
    assert "claim_type =" not in calls[0][0]


def test_nested_bigquery_row_and_multiple_invoices(provider):
    rows, _ = provider
    rows[0]["items"] = [Row(("두 번째 상품", None), {"product_name": 0, "quantity": 1})]
    rows[0]["outbound_invoice_nos"] = ["0123456789", "9876543210"]
    answer = cs.answer_query_sync(f"{ORDER} CS 정보")
    assert "0123456789 · 9876543210" in answer
    assert "| 두 번째 상품 | 미기재 |" in answer


@pytest.mark.parametrize("field", ["출고송장 번호", "출고 송장번호", "출고 송장 번호",
                                  "회수송장 번호", "회수 송장번호", "회수 송장 번호"])
def test_invoice_field_spacing_is_not_mistaken_for_an_extra_filter(field, provider):
    question = f"{ORDER} CS {field} 알려줘"
    assert cs.is_order_detail_query(question)
    answer = cs.answer_query_sync(question, brand_filter="SK,CL,CBT")
    assert "537418082563" in answer and "573867634314" in answer


@pytest.mark.parametrize("amount,expected", [(None, "미기재"), (Decimal("0"), "0원"),
                                            (Decimal("35000"), "35,000원"), ("1250.50", "1,250.50원")])
def test_requested_compensation_is_recorded_amount_and_null_is_not_zero(amount, expected, provider):
    provider[0][0]["compensation_amount"] = amount
    answer = cs.answer_query_sync(f"{ORDER} CS 정보와 보상액 알려줘")
    assert f"| 원본 보상 기록액 | {expected} |" in answer
    assert "실제 지급 확인액이 아닙니다" in answer
    assert "지급없음" not in answer
    assert "compensation_amount" in provider[1][0][0]


@pytest.mark.parametrize("sources", [[], ["BP"], ["매출"], ["해외CS"], ["CS"], ["국내리뷰", "제품"]])
def test_disallowed_source_does_not_query(sources, provider):
    assert cs.answer_query_sync(f"{ORDER} CS 정보", enabled_sources=sources) is None
    assert provider[1] == []


@pytest.mark.parametrize("sources", [None, ["국내CS"], ["국내 CS"], ["domestic_cs"], ["매출", "해외CS", "국내CS"]])
def test_allowed_domestic_scope_reads(sources, provider):
    assert cs.answer_query_sync(f"{ORDER} CS 정보", enabled_sources=sources)
    assert len(provider[1]) == 1


@pytest.mark.parametrize("question", [
    f"{ORDER} 해외 CS 정보", f"{ORDER} Shopify CS 정보", f"{ORDER} 글로벌 CS 정보",
    f"{ORDER} CS 환불 정책", f"{ORDER} CS 답변 작성해줘", f"{ORDER} CS 총계",
    f"{ORDER} CS 8월 정보", f"{ORDER} CS 2026-08-01부터 정보", f"{ORDER} CS 완료된 기록",
    f"{ORDER} CS 반품 기록", f"{ORDER} CS 스킨천사 제품 정보", f"UM 브랜드만 주문번호 {ORDER} CS정보",
    f"{ORDER} CS 보상액 USD 알려줘", f"{ORDER} CS 보상 지급액", f"{ORDER} CS 고객명과 이메일",
    f"{ORDER} CS 원본 전체 JSON", f"{ORDER} CS 개인정보 다운로드", f"{ORDER} CS 주문금액",
    f"{ORDER} CS 앰플 정보", f"{ORDER} CS 정보와 매출", f"{ORDER} CS 송장번호 없는 기록",
    f"{ORDER} CS 정보와 2026082224631661 CS정보", "20260814 CS정보",
    f"{ORDER} 주문정보", f"{ORDER}' OR 1=1 -- CS 정보", f"{ORDER}; DROP TABLE cs CS정보",
    f"<script>alert(1)</script> {ORDER} CS 정보", f"{ORDER} CS <img src=x onerror=alert(1)>",
])
def test_additional_constraints_and_injection_never_become_unfiltered_lookup(question, provider):
    assert not cs.is_order_detail_query(question)
    assert cs.answer_query_sync(question) is None
    assert not provider[1]


def test_empty_result_says_only_matching_domestic_record_not_found(provider):
    provider[0].clear()
    answer = cs.answer_query_sync(f"{ORDER} CS 정보")
    assert "해당하는 국내 CS 처리 기록을 찾지 못했습니다" in answer
    assert "조회하지 못했습니다" not in answer and ORDER in answer


def test_provider_failure_is_not_reported_as_no_record(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("private-token-and-internal-server")
    monkeypatch.setattr(cs, "get_bigquery_client", lambda: SimpleNamespace(execute_query=fail))
    answer = cs.answer_query_sync(f"{ORDER} CS 정보")
    assert "조회하지 못했습니다" in answer
    assert "찾지 못했습니다" not in answer and "private-token" not in answer


def test_invalid_validation_cannot_execute(provider, monkeypatch):
    monkeypatch.setattr(cs, "validate_sql", lambda *args, **kwargs: (False, "private-diagnostic"))
    answer = cs.answer_query_sync(f"{ORDER} CS 정보")
    assert "조회하지 못했습니다" in answer and "private-diagnostic" not in answer
    assert not provider[1]


def test_provider_cannot_return_another_order(provider):
    provider[0][0]["order_no"] = "9999999999999999"
    answer = cs.answer_query_sync(f"{ORDER} CS 정보")
    assert "조회하지 못했습니다" in answer and "9999999999999999" not in answer
    assert "537418082563" not in answer


def test_text_cells_escape_html_and_markdown_without_losing_items(provider):
    provider[0][0]["items"] = [{"product_name": '<img src=x onerror="alert(1)"> | [x](javascript:bad)', "quantity": 0}]
    provider[0][0]["reason_details"] = ["<script>bad()</script>\n다음 줄"]
    answer = cs.answer_query_sync(f"{ORDER} CS 정보")
    assert "<img" not in answer and "<script>" not in answer
    assert "&lt;img" in answer and "\\|" in answer and "\\[x\\]" in answer
    assert "0개" in answer and "다음 줄" in answer


def test_more_than_100_records_are_explicitly_truncated(provider):
    rows, _ = provider
    rows[:] = [_record() for _ in range(101)]
    answer = cs.answer_query_sync(f"{ORDER} CS 정보")
    assert "100건까지 표시했습니다" in answer and "추가 기록이 있습니다" in answer
    assert len(re.findall(r"\*\*처리 기록 \d+ ·", answer)) == 100


def test_only_latest_own_detail_answer_can_supply_followup_order_and_channel(provider):
    first = cs.answer_query_sync(f"네이버 {ORDER} CS 정보")
    followup = "그럼 송장은?"
    assert cs.is_order_detail_query(followup, first)
    answer = cs.answer_query_sync(followup, conversation_context="사용자: 원 질문\nAI: " + first)
    assert "537418082563" in answer
    assert "AND channel = '스마트스토어'" in provider[1][-1][0]
    other_context = f"사용자: 원 질문\nAI: {first}\n사용자: 매출?\nAI: 이번 달 매출입니다."
    count = len(provider[1])
    assert cs.answer_query_sync(followup, conversation_context=other_context) is None
    assert cs.answer_query_sync(followup, conversation_context="[CS 대시보드](http://34.64.99.254:8061/)") is None
    assert len(provider[1]) == count


def test_explicit_new_order_does_not_keep_previous_channel(provider):
    first = cs.answer_query_sync(f"네이버 {ORDER} CS 정보")
    provider[0][0].update(order_no="139515930570616967", channel="지그재그")
    assert cs.answer_query_sync("139515930570616967 CS 정보", conversation_context=first)
    assert "AND channel" not in provider[1][-1][0]


@pytest.mark.asyncio
async def test_async_interface_matches_sync_and_preserves_account_scope(provider):
    expected = cs.answer_query_sync(f"{ORDER} CS 정보", ["국내CS"], brand_filter="SK,CL,CBT")
    assert await cs.answer_query(f"{ORDER} CS 정보", ["국내CS"], brand_filter="SK,CL,CBT") == expected
