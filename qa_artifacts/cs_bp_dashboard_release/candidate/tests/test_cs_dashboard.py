"""Dashboard answers keep the actual dashboard population and all constraints."""
import copy
import json
import re
from datetime import date
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.core import cs_dashboard as cs


def _payload(source="domestic", total=4):
    """Synthetic records include PII to prove only aggregate fields leave them."""
    def metric(value, unit="건"):
        return {"value": value, "unit": unit}
    rows = [{"productName": "스킨1004 마다가스카르 센텔라 앰플 30ml" if i % 2 else "토너",
             "salesChannel": "무신사" if i % 2 else "스마트스토어",
             "reason": "파손" if i % 2 else "변심", "processStatus": "completed",
             "items": [{"productName": "별도 상품", "quantity": 12}] * 3,
             "orderNo": "PRIVATE-ORDER", "customerName": "PRIVATE-NAME",
             "customerEmail": "private@example.com", "raw_payload": {"secret": "PRIVATE-RAW"}}
            for i in range(total)]
    return {"source": cs._SOURCES[source], "period": {"start": "2026-08-01", "end": "2026-08-31"},
            "metrics": {"totalCount": metric(total), "exchangeCount": metric(0),
                        "returnCount": metric(total), "completedCount": metric(total),
                        "inProgressCount": metric(0),
                        "totalCompensationAmount": metric(1060500 if source == "domestic" else 5828.60,
                                                          "원" if source == "domestic" else "USD")},
            "charts": {"typeShare": [{"label": "반품", "value": total}],
                       "dailyTrend": [{"date": "2026-08-01", "total": total},
                                      {"date": "2026-08-02", "total": 0}]},
            "total": total, "rows": rows}


@pytest.fixture
def api(monkeypatch):
    calls = []
    data = {"domestic": _payload(), "overseas": _payload("overseas", 68)}
    def fetch(source, params):
        calls.append((source, dict(params)))
        result = copy.deepcopy(data[source])
        for param, field in (("startDate", "start"), ("endDate", "end")):
            if param in params:
                result["period"][field] = params[param]
        return result
    monkeypatch.setattr(cs, "_fetch_summary", fetch)
    monkeypatch.setattr(cs, "_today", lambda: date(2026, 9, 16))
    return calls, data


def _url(answer):
    return re.findall(r"\]\((http://[^)]+)\)", answer)[-1]


def _chart(answer):
    return json.loads(answer.split("```chart-config\n", 1)[1].split("\n```", 1)[0])


def test_domestic_filters_and_period_are_in_api_and_link(api):
    calls, _ = api
    answer = cs.answer_query_sync("국내 CS 2026년 8월 무신사 반품 완료 건수 알려줘")
    assert calls == [("domestic", {"scope": "all", "channel": "무신사", "claimType": "return",
                                     "status": "completed", "startDate": "2026-08-01", "endDate": "2026-08-31"})]
    assert "국내 CS 대시보드 기준" in answer and "**4건**" in answer
    assert "실제 지급 확인액" in answer and "선택 기간의 완료 건수와 합산하지" in answer
    assert parse_qs(urlsplit(_url(answer)).query)["channel"] == ["무신사"]
    assert "PRIVATE" not in answer and "private@example.com" not in answer


def test_overseas_uses_dashboard_population_and_usd(api):
    answer = cs.answer_query_sync("해외 CS 8월 환불금액 얼마야?")
    assert api[0][0][0] == "overseas"
    assert "**68건**" in answer and "$5,828.60 USD" in answer
    assert "구글시트 대시보드 기준" in answer and "Shopify 환불 테이블과 집계 대상이 다릅니다" in answer
    assert urlsplit(_url(answer)).path == "/global/dashboard"


@pytest.mark.parametrize("question", [
    "해외 CS 8월 미국 환불 건수", "해외 CS 8월 국가별 건수", "해외 CS Shopify 환불 건수",
    "해외 CS BigQuery 환불 금액", "해외 CS SQL로 환불 건수", "해외 CS 고유 주문 수",
    "해외 CS 원화 환불액", "국내 CS 8월과 7월 비교", "국내 CS 최근 3개월 현황",
    "국내 CS 2026년 3분기 접수 건수", "국내 CS 8월 1일부터 15일까지 현황",
    "국내 CS 8월 피부트러블 접수 건수", "국내 CS 8월 앰플 접수 건수",
    "국내 CS 매출과 접수 건수", "국내 CS 환불액", "국내 CS 사유별 보상액",
    "국내 CS 일별 보상 금액", "국내 CS 상품별 수량", "해외 CS 상품별 건수",
    "국내 CS 8월 완료일 기준 처리 건수", "국내 CS 2026년 13월 현황",
    "국내 CS 2026-08-31~2026-08-01 현황", "국내 CS 채널별 사유별 건수",
    "국내 CS 8월 무신사와 자사몰 반품 건수", "국내 CS 고객별 반품 건수",
    "국내 CS 국내와 해외 통합 건수", "국내 CS 8월 환불액 100만원 이상",
    "국내 CS 8월 채널별 비중 보여줘", "국내 CS 8월 교환 비율 분석",
    "국내 CS 8월 보상액 USD 알려줘", "국내 CS 8월 보상액 달러 알려줘",
])
def test_unsupported_constraints_never_disappear(question, api):
    assert cs.answer_query_sync(question) is None
    assert api[0] == []


@pytest.mark.parametrize("sources", [[], ["매출"], ["해외CS"], ["BP", "CS"]])
def test_disabled_source_does_not_fetch(sources, api):
    assert cs.answer_query_sync("국내 CS 8월 현황", sources) is None
    assert not api[0]


def test_explicit_selection_and_all_sources_scope(api):
    assert cs.answer_query_sync("8월 현황", ["domestic cs"])
    assert cs.answer_query_sync("해외 CS 8월 현황", ["매출", "국내CS", "해외CS"])
    assert [source for source, _ in api[0]] == ["domestic", "overseas"]
    assert cs.answer_query_sync("8월 현황", ["국내CS", "해외CS"]) is None


@pytest.mark.parametrize("period,start,end", [
    ("이번 달", "2026-09-01", "2026-09-30"), ("지난달", "2026-08-01", "2026-08-31"),
    ("2024년 2월", "2024-02-01", "2024-02-29"), ("7월", "2026-07-01", "2026-07-31"),
    ("2026-08-03~2026-08-14", "2026-08-03", "2026-08-14"),
    ("2026-08-03부터 2026-08-14까지", "2026-08-03", "2026-08-14"),
])
def test_periods_use_korean_date_and_exact_bounds(period, start, end, api):
    answer = cs.answer_query_sync(f"국내 CS {period} 현황")
    assert api[0][0][1]["startDate"] == start and api[0][0][1]["endDate"] == end
    assert f"{start} ~ {end}" in answer


def test_no_period_uses_api_period_without_inventing_one(api):
    answer = cs.answer_query_sync("국내 CS 현황")
    assert "startDate" not in api[0][0][1]
    assert "2026-08-01 ~ 2026-08-31" in answer
    assert parse_qs(urlsplit(_url(answer)).query)["startDate"] == ["2026-08-01"]


def test_zero_is_a_valid_dashboard_answer(api):
    api[1]["domestic"] = _payload(total=0)
    api[1]["domestic"]["metrics"]["totalCompensationAmount"]["value"] = 0
    answer = cs.answer_query_sync("국내 CS 8월 몇 건이야?")
    assert "**0건**" in answer and "0원" in answer
    assert "불러오지 못했습니다" not in answer


def test_daily_chart_contains_only_verified_aggregates(api):
    answer = cs.answer_query_sync("국내 CS 8월 일별 추이 그래프로 보여줘")
    chart = _chart(answer)
    assert chart["type"] == "line"
    assert chart["data"]["labels"] == ["2026-08-01", "2026-08-02"]
    assert chart["data"]["datasets"][0]["data"] == [4, 0]
    assert "PRIVATE" not in answer and "customer" not in answer


def test_product_grouping_uses_dashboard_representative_name_not_items(api):
    answer = cs.answer_query_sync("국내 CS 8월 상품별 건수")
    chart = _chart(answer)
    assert set(chart["data"]["labels"]) == {"토너", "센텔라 앰플 30ml"}
    assert sum(chart["data"]["datasets"][0]["data"]) == 4
    assert "별도 상품" not in answer
    assert urlsplit(_url(answer)).path == "/reports/product"


def test_reason_chart_keeps_all_dashboard_reason_counts(api):
    answer = cs.answer_query_sync("해외 CS 8월 사유별 건수")
    assert sum(_chart(answer)["data"]["datasets"][0]["data"]) == 68
    assert "PRIVATE" not in answer


def test_channel_group_alias_and_others_link_preserve_scope(api):
    assert cs.answer_query_sync("국내 CS 8월 판매처별 건수")
    answer = cs.answer_query_sync("국내 CS 8월 자사몰·입점몰 사유별 건수")
    assert api[0][-1][1]["scope"] == "others"
    assert urlsplit(_url(answer)).path == "/data/channels"
    assert parse_qs(urlsplit(_url(answer)).query)["scope"] == ["others"]


@pytest.mark.parametrize("region,source", [("국내", "domestic"), ("해외", "overseas"), ("글로벌", "overseas")])
def test_own_store_inside_source_name_remains_an_actual_filter(region, source, api):
    answer = cs.answer_query_sync(f"{region} 자사몰 CS 8월 건수")
    assert answer and api[0][-1][0] == source
    assert api[0][-1][1]["channel"] == "자사몰"
    assert parse_qs(urlsplit(_url(answer)).query)["channel"] == ["자사몰"]


@pytest.mark.parametrize("suffix", ["링크도 같이", "링크 알려줘", "링크도 줘", "링크 포함", "원본 링크",
                                   "과 링크 알려줘", "원본 링크도 함께"])
def test_data_plus_link_still_returns_data_and_matching_link(suffix, api):
    answer = cs.answer_query_sync("해외 CS 8월 환불 금액 " + suffix)
    assert "**68건**" in answer and "$5,828.60 USD" in answer
    assert parse_qs(urlsplit(_url(answer)).query)["startDate"] == ["2026-08-01"]


@pytest.mark.parametrize("failure", [httpx.ConnectTimeout("PRIVATE-DETAIL"),
                                      httpx.HTTPStatusError("PRIVATE-DETAIL", request=httpx.Request("GET", "http://example.com"),
                                                            response=httpx.Response(403))])
def test_failed_api_returns_same_dashboard_link_without_other_metrics(failure, monkeypatch):
    def fail(*_):
        raise failure
    monkeypatch.setattr(cs, "_fetch_summary", fail)
    answer = cs.answer_query_sync("해외 CS 2026년 8월 반품 건수")
    assert "불러오지 못했습니다" in answer and "PRIVATE" not in answer
    params = parse_qs(urlsplit(_url(answer)).query)
    assert params["startDate"] == ["2026-08-01"] and params["claimType"] == ["return"]
    assert "94" not in answer and "68" not in answer and "chart-config" not in answer


@pytest.mark.parametrize("damage", ["source", "period", "rows", "pii_label", "nan"])
def test_unverified_aggregates_are_never_presented(damage, api):
    data = api[1]["domestic"]
    if damage == "source":
        data["source"] = "shopify"
    elif damage == "period":
        data["period"]["end"] = "2026-07-31"
    elif damage == "rows":
        data["rows"].pop()
    elif damage == "pii_label":
        data["rows"][0]["productName"] = "private@example.com"
    else:
        data["metrics"]["totalCount"]["value"] = float("nan")
    answer = cs.answer_query_sync("국내 CS 상품별 건수")
    assert "불러오지 못했습니다" in answer
    assert "private@example.com" not in answer and "chart-config" not in answer


def test_clear_followup_inherits_exact_scope_and_replaces_only_month(api):
    first = cs.answer_query_sync("해외 CS 8월 자사몰 반품 사유별 건수")
    second = cs.answer_query_sync("그럼 7월은?", conversation_context=first)
    source, params = api[0][-1]
    assert source == "overseas" and params["channel"] == "자사몰" and params["claimType"] == "return"
    assert params["startDate"] == "2026-07-01"
    assert "사유별 건수" in second and "대시보드 기준" in second


def test_new_region_and_period_does_not_keep_previous_filters(api):
    first = cs.answer_query_sync("국내 CS 8월 무신사 반품 건수")
    assert cs.answer_query_sync("해외 CS 7월 현황", conversation_context=first)
    assert api[0][-1][1]["channel"] == "all" and api[0][-1][1]["claimType"] == "all"


def test_unknown_context_or_unapproved_link_does_not_broaden_query(api):
    for context in ("지난 답변은 매출입니다", "[CS 대시보드에서 요약 보기](http://evil.example/dashboard?startDate=2026-08-01&endDate=2026-08-31)"):
        assert cs.answer_query_sync("그럼 7월은?", ["해외CS"], context) is None
    assert not api[0]


@pytest.mark.parametrize("latest", [
    "8월 매출은 5억원입니다.",
    "센텔라 앰플은 피부 진정에 사용하는 제품입니다.",
    "[실행된 쿼리 테이블: skin1004-319714.cs_dashboard.overseas_cs_refunds] Shopify 환불 94건입니다.\n"
    "관련 화면: [해외 CS 대시보드](http://34.64.99.254:8061/global/dashboard?startDate=2026-08-01&endDate=2026-08-31)",
])
def test_older_dashboard_cannot_hijack_a_later_answer(latest, api):
    old = cs.answer_query_sync("국내 CS 8월 무신사 건수")
    context = f"사용자: 국내 CS\nAI: {old}\n사용자: 다른 질문\nAI: {latest}"
    calls_before = len(api[0])
    assert cs.answer_query_sync("채널별로", conversation_context=context) is None
    assert len(api[0]) == calls_before


def test_actual_orchestrator_context_keeps_current_dashboard_filters(api):
    from app.agents.orchestrator import _build_conversation_context
    first = cs.answer_query_sync("해외 CS 8월 자사몰 반품 사유별 건수")
    context = _build_conversation_context([
        {"role": "user", "content": "해외 CS 8월 자사몰 반품 사유별 건수"},
        {"role": "assistant", "content": first},
        {"role": "user", "content": "그럼 7월은?"},
    ])
    assert cs.answer_query_sync("그럼 7월은?", conversation_context=context)
    assert api[0][-1][0] == "overseas" and api[0][-1][1]["channel"] == "자사몰"
    assert api[0][-1][1]["startDate"] == "2026-07-01"


def test_link_survives_long_aggregate_table_in_history(api):
    data = _payload(total=120)
    for index, row in enumerate(data["rows"]):
        row["productName"] = f"서로 다른 상품의 긴 표시 이름 {index}"
    api[1]["domestic"] = data
    answer = cs.answer_query_sync("국내 CS 8월 상품별 건수")
    assert _url(answer) in answer[:800]
    assert cs.answer_query_sync("그럼 7월은?", conversation_context="AI: " + answer[:3000])
    assert api[0][-1][1]["startDate"] == "2026-07-01"


def test_network_adapter_gets_only_fixed_endpoint(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=_payload("overseas"))
    real_client = httpx.Client
    def client(**kwargs):
        assert kwargs == {"timeout": 8.0, "follow_redirects": False}
        return real_client(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(cs.httpx, "Client", client)
    cs._fetch_summary("overseas", {"startDate": "2026-08-01"})
    request = requests[0]
    assert request.method == "GET"
    assert str(request.url).startswith(cs.DASHBOARD_URL.rstrip("/") + "/api/overseas/dashboard/summary?")
    assert request.url.params["startDate"] == "2026-08-01"


@pytest.mark.asyncio
async def test_async_public_interface_preserves_sync_result(api):
    expected = cs.answer_query_sync("국내 CS 8월 현황", ["국내CS"])
    assert await cs.answer_query("국내 CS 8월 현황", ["국내CS"]) == expected
