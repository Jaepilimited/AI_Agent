"""Dashboard answers keep their own population in both chat answer modes."""
import pytest

from app.agents import sql_agent
from app.core import cs_dashboard, cs_metrics


@pytest.fixture
def dashboard_payload(monkeypatch):
    def fetch(source, params):
        assert source == "overseas"
        assert params["startDate"] == "2026-08-01"
        assert params["endDate"] == "2026-08-31"
        metric = lambda value, unit="건": {"value": value, "unit": unit}
        return {
            "source": "google_sheets_overseas",
            "period": {"start": "2026-08-01", "end": "2026-08-31"},
            "metrics": {
                "totalCount": metric(68), "completedCount": metric(66),
                "inProgressCount": metric(2), "exchangeCount": metric(0),
                "returnCount": metric(68), "totalCompensationAmount": metric(5828.60, "USD"),
            },
        }
    monkeypatch.setattr(cs_dashboard, "_fetch_summary", fetch)
    def no_sql(*args, **kwargs):
        raise AssertionError("Dashboard question unexpectedly reached SQL generation")
    monkeypatch.setattr(sql_agent, "generate_sql", no_sql)


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_both_sql_entrypoints_prefer_the_dashboard_population(dashboard_payload, streaming):
    query = "2026년 8월 해외 CS 환불 건수와 환불액 알려줘"
    kwargs = {"enabled_sources": ["해외CS"]}
    if streaming:
        answer = "".join(sql_agent.run_sql_agent_stream(query, **kwargs))
    else:
        answer = await sql_agent.run_sql_agent(query, **kwargs)
    assert "68건" in answer and "5,828.60" in answer
    assert "해외 CS 대시보드 기준" in answer
    assert "global/dashboard?" in answer and "startDate=2026-08-01" in answer
    assert "Shopify" in answer and "집계 대상이 다릅니다" in answer


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_unsupported_dimensions_reach_the_existing_sql_pipeline(monkeypatch, streaming):
    class SQLReached(Exception):
        pass
    def generate(state):
        assert state["enabled_sources"] == ["해외CS"]
        raise SQLReached
    def no_dashboard(*args, **kwargs):
        raise AssertionError("Unsupported question reached dashboard API")
    monkeypatch.setattr(sql_agent, "generate_sql", generate)
    monkeypatch.setattr(cs_dashboard, "_fetch_summary", no_dashboard)
    with pytest.raises(SQLReached):
        query = "2026년 8월 해외 CS 국가별 Shopify 환불액 알려줘"
        if streaming:
            list(sql_agent.run_sql_agent_stream(query, enabled_sources=["해외CS"]))
        else:
            await sql_agent.run_sql_agent(query, enabled_sources=["해외CS"])


@pytest.mark.asyncio
async def test_source_api_exposes_bp_and_dashboard_links():
    from app.api.routes import list_datasources
    entries = {entry["key"]: entry for entry in await list_datasources()}
    assert "CS" not in entries
    assert len(entries["BP"]["links"]) >= 2
    assert entries["BP"]["route"] == "cs"
    for key in ("국내CS", "해외CS"):
        assert entries[key]["url"] == cs_metrics.DASHBOARD_URL


def test_detail_query_never_claims_to_be_the_dashboard_population():
    sql = f"SELECT country_name, SUM(refund_amount_usd) FROM `{cs_metrics.OVERSEAS_TABLE}` GROUP BY country_name"
    notice = cs_metrics.notice(sql)
    assert "Shopify 환불 기록" in notice
    assert "구글시트 집계와는 별도 자료" in notice
    assert "http://34.64.99.254:8061/global/dashboard" in notice
