"""Dashboard link catalog and deterministic chat-answer regression tests."""

from pathlib import Path

import pytest

try:
    from app.core import dashboard_links
except ImportError:  # RED should be an assertion failure, not a collection error.
    dashboard_links = None

from app.agents.orchestrator import OrchestratorAgent


ROOT = Path(__file__).parents[1]


def _links_module():
    assert dashboard_links is not None, "dashboard link catalog module is missing"
    return dashboard_links


def test_shared_catalog_contains_every_dashboard_link():
    links = _links_module().iter_dashboard_links()

    assert len(links) == 92
    assert all(link["title"] and link["url"].startswith(("http://", "https://")) for link in links)
    assert any(
        link["title"] == "프로모션 캘린더"
        and link["url"] == "http://34.64.99.254:8041/"
        for link in links
    )


def test_every_catalog_title_can_resolve_its_own_url():
    module = _links_module()

    for link in module.iter_dashboard_links():
        answer = module.answer_dashboard_link_query(f"{link['title']} 링크 알려줘")
        assert answer is not None, link["title"]
        assert f"]({link['url']})" in answer, link["title"]


@pytest.mark.parametrize(
    ("title", "url"),
    [
        (
            "재고 데이터 피벗 엑셀 변환기",
            "https://aistudio.google.com/apps/e9b00eeb-b6f3-418c-8d22-a9b811168755?fullscreenApplet=true",
        ),
        ("SKIN1004 CS 대시보드", "https://cs.cravercorp.internal"),
        (
            "국내 교환/반품 통합 대시보드",
            "http://34.64.99.254:8061/data/channels?startDate=2026-08-01&endDate=2026-08-31&status=in_progress",
        ),
    ],
)
def test_latest_raw_sheet_entries_are_available_as_dashboard_links(title, url):
    """New rows in the dashboard RAW sheet must be selectable from chat and UI."""
    matches = _links_module().find_dashboard_links(f"{title} URL")

    assert matches
    assert matches[0]["title"] == title
    assert matches[0]["url"] == url


@pytest.mark.parametrize(
    ("query", "title", "url"),
    [
        ("프로모션 캘린더 주소 알려줘", "프로모션 캘린더", "http://34.64.99.254:8041/"),
        ("물류관리 시스템 링크", "물류관리 시스템", "http://34.64.99.254:8051/"),
        ("메가와리 대시보드 어디서 봐?", "메가와리 대시보드", "https://lookerstudio.google.com/reporting/3ada86c9-85a4-4191-bdf0-1fb879d6a2ac/page/d51NF"),
    ],
)
def test_specific_dashboard_link_questions_return_exact_clickable_url(query, title, url):
    answer = _links_module().answer_dashboard_link_query(query)

    assert answer is not None
    assert title in answer
    assert f"]({url})" in answer


def test_multiple_matches_are_capped_at_five():
    matches = _links_module().find_dashboard_links("틱톡 링크", limit=5)

    assert 2 <= len(matches) <= 5
    assert all("틱톡" in (match["title"] + match["description"]).lower() for match in matches)


@pytest.mark.parametrize(
    "query",
    [
        "8월 프로모션 일정 알려줘",
        "올해 프로모션 성과를 분석해줘",
        "메가와리 매출이 얼마나 나왔어?",
    ],
)
def test_data_questions_do_not_turn_into_dashboard_link_answers(query):
    assert _links_module().answer_dashboard_link_query(query) is None


def test_dashboard_html_reads_the_shared_catalog_instead_of_embedding_urls():
    html = (ROOT / "app" / "static" / "dashboard.html").read_text(encoding="utf-8")

    assert "dashboard-catalog.json" in html
    assert "var DATA = {};" in html
    assert "http://34.64.99.254:8041/" not in html


@pytest.mark.asyncio
async def test_nonstream_chat_short_circuits_to_dashboard_link_answer(monkeypatch):
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    agent.parse_db_prefix = lambda query: (None, query)
    monkeypatch.setattr("app.core.term_aliases._load", lambda: [])

    result = await agent.route_and_execute("프로모션 캘린더 주소 알려줘")

    assert result["source"] == "direct"
    assert "](http://34.64.99.254:8041/)" in result["answer"]


@pytest.mark.asyncio
async def test_stream_chat_short_circuits_to_dashboard_link_answer(monkeypatch):
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    agent.parse_db_prefix = lambda query: (None, query)
    monkeypatch.setattr("app.core.term_aliases._load", lambda: [])

    events = [event async for event in agent.route_and_stream("프로모션 캘린더 주소 알려줘")]

    assert events[0] == ("source", "direct")
    assert any(kind == "done" and "](http://34.64.99.254:8041/)" in data for kind, data in events)


@pytest.mark.asyncio
async def test_promotion_schedule_question_stays_on_data_route(monkeypatch):
    agent = OrchestratorAgent.__new__(OrchestratorAgent)

    async def no_report(*args, **kwargs):
        return None

    async def bigquery(*args, **kwargs):
        return {"source": "bigquery", "sentinel": "promotion-data"}

    agent.parse_db_prefix = lambda query: (None, query)
    agent._handle_report = no_report
    agent._keyword_classify_ex = lambda query: ("bigquery", True)
    agent._handler_map = lambda: {"bigquery": bigquery}
    monkeypatch.setattr("app.core.term_aliases._load", lambda: [])

    result = await agent.route_and_execute("8월 프로모션 일정 알려줘")

    assert result == {"source": "bigquery", "sentinel": "promotion-data"}
