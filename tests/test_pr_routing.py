"""PR 리스트 자료의 소스 선택, 검색 범위, 작성월과 출처를 함께 지킨다."""

import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agents import orchestrator as orch
from app.agents import qdrant_agent as qdrant


PR_URL = "https://docs.google.com/spreadsheets/d/1MLuwW74Nvv33YW6cVG5qW4uJLGuj3miUqcky9r20A8c/edit?gid=0"


def pr_result(month="2025-08", inferred=True, raw="8월", title="일본 행사"):
    return {"score": 0.81, "payload": {
        "team": "PR", "page_title": title, "page_url": PR_URL,
        "text": "일본에서 브랜드 행사를 진행했습니다.",
        "issue_month": month, "month_inferred": inferred, "month_raw": raw,
    }}


@pytest.mark.parametrize("alias", ["PR", "pr", "홍보", "보도자료", "프레인"])
def test_pr_alias_selects_the_pr_source(alias):
    entry, query = orch.OrchestratorAgent.parse_db_prefix(f"@@{alias} 8월 이슈")
    assert entry["key"] == "PR"
    assert (entry["route"], entry["group"], entry["icon"]) == ("notion", "브랜드 성과", "sheet")
    assert query == "8월 이슈"
    assert qdrant.resolve_team_filter(alias) == "PR"


@pytest.mark.parametrize("query", [
    "8월 PR 이슈 알려줘", "프레인 PR 이슈 정리해줘", "보도자료 찾아줘",
    "홍보 자료 보여줘", "프레인 리스트 자료 찾아줘", "매출 관련 PR 이슈 찾아줘",
])
def test_pr_lookup_is_confident_without_llm_classification(query):
    agent = orch.OrchestratorAgent.__new__(orch.OrchestratorAgent)
    assert agent._keyword_classify_ex(query) == ("notion", True)


@pytest.mark.parametrize("query", [
    "product 정보", "promotion 일정", "price 데이터", "approve 이슈",
    "appr 이슈 알려줘", "PR 이슈보다는 매출 자료", "홍보용 문구 작성해줘",
])
def test_pr_is_not_an_english_substring_or_an_unrelated_use(query):
    agent = orch.OrchestratorAgent.__new__(orch.OrchestratorAgent)
    assert not agent._is_pr_lookup_query(query)


@pytest.mark.parametrize("query, route", [
    ("6월 실적 알려줘", "bigquery"),
    ("8월 행사 일정 알려줘", "bigquery"),
    ("2026년 매출 알려줘", "bigquery"),
    ("보도자료 초안 작성해줘", "direct"),
    ("홍보 전략 기획해줘", "direct"),
    ("PR 이슈란 뭐야?", "direct"),
    ("PR 이슈 정리해줘\n| 월 | 내용 |\n| --- | --- |\n| 8월 | " + "사용자가 제공한 행사 설명입니다. " * 8 + "|\n| 9월 | 입점 |", "direct"),
])
def test_existing_authoring_pasted_data_and_business_routes_keep_priority(query, route):
    agent = orch.OrchestratorAgent.__new__(orch.OrchestratorAgent)
    assert agent._keyword_classify_ex(query)[0] == route


@pytest.fixture
def routed_search(monkeypatch):
    """실제 orchestrator → qdrant.run → _search; 외부 서비스 경계만 대역으로 둔다."""
    agent = orch.OrchestratorAgent()
    # 로컬 최소 테스트 환경에는 SDK가 없을 수 있다. SDK 값 객체만 대체하고
    # 실제 _search가 만드는 team 필터를 검사한다.
    try:
        import qdrant_client.models
    except ImportError:
        monkeypatch.setitem(sys.modules, "qdrant_client.models", SimpleNamespace(
            Filter=SimpleNamespace, FieldCondition=SimpleNamespace, MatchValue=SimpleNamespace))
    agent._handle_report = AsyncMock(return_value=None)
    agent._verify_coherence = AsyncMock()
    monkeypatch.setattr("app.core.safety.get_maintenance_manager", lambda: SimpleNamespace(active=False, manual=False))
    monkeypatch.setattr("app.knowledge.wiki_search.search_with_pages", AsyncMock(return_value=""))
    monkeypatch.setattr("app.core.safety.get_circuit", lambda _: SimpleNamespace(
        is_available=lambda: True, record_success=lambda: None, record_failure=lambda: None))
    monkeypatch.setattr(qdrant, "_embed_query", AsyncMock(return_value=[0.1, 0.2]))
    scopes, prompts = [], []

    def query_points(**kwargs):
        scope = kwargs["query_filter"].must[0].match.value if kwargs["query_filter"] else None
        scopes.append(scope)
        result = pr_result() if scope == "PR" else {"score": 0.81, "payload": {
            "team": scope, "page_title": "업무 자료", "text": "자료 내용",
            "page_url": "https://www.notion.so/123456789012345678901234567890ab",
            "last_edited_time": "2026-09-09T01:00:00Z",
        }}
        return SimpleNamespace(points=[SimpleNamespace(**result)])

    def generate(prompt, *args):
        prompts.append(prompt)
        return "브랜드 행사 자료입니다."

    monkeypatch.setattr(qdrant, "_get_client", lambda: SimpleNamespace(query_points=query_points))
    monkeypatch.setattr(qdrant, "get_flash_client", lambda: SimpleNamespace(generate=generate))
    monkeypatch.setattr(orch, "get_flash_client", lambda: (_ for _ in ()).throw(AssertionError("PR lookup reached classifier")))
    return agent, scopes, prompts


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("query, sources, expected", [
    ("8월 PR 이슈 알려줘", None, "PR"),
    ("@@PR 8월 이슈", None, "PR"),
    ("8월 이슈", ["PR"], "PR"),
    ("@@BCM PR 이슈 자료 찾아줘", None, "BCM"),
    ("PR 이슈 자료 찾아줘", ["BCM"], "BCM"),
])
async def test_actual_query_paths_keep_the_selected_qdrant_filter(routed_search, stream, query, sources, expected):
    agent, scopes, prompts = routed_search
    if stream:
        events = [event async for event in agent.route_and_stream(query, enabled_sources=sources)]
        answer = "".join(data for kind, data in events if kind == "chunk")
        if not answer:
            answer = "".join(data for kind, data in events if kind == "done")
    else:
        answer = (await agent.route_and_execute(query, enabled_sources=sources))["answer"]
    assert scopes == [expected]
    assert prompts
    if expected == "PR":
        assert PR_URL in answer
        assert "작성월 추정" in answer
        assert "Notion 사내 문서 검색" not in answer
        assert "노션 공개 링크" not in answer


@pytest.mark.parametrize("notion_data", [[{"payload": {"team": "BCM"}}], {"vectors": [{"team": "BCM"}]}])
def test_counts_include_the_independently_owned_pr_manifest(tmp_path, monkeypatch, notion_data):
    notion_file, pr_file = tmp_path / "notion.json", tmp_path / "pr.json"
    notion_file.write_text(json.dumps(notion_data), encoding="utf-8")
    pr_file.write_text(json.dumps({"points": [pr_result(), pr_result()]}), encoding="utf-8")
    monkeypatch.setattr(qdrant, "_LOCAL_JSON", notion_file)
    monkeypatch.setattr(qdrant, "_PR_LOCAL_JSON", pr_file, raising=False)
    monkeypatch.setattr(qdrant, "_TEAM_COUNTS", None)
    assert qdrant.index_team_counts() == {"BCM": 1, "PR": 2}
    pr_file.write_text(json.dumps({"points": [pr_result()]}), encoding="utf-8")
    assert qdrant.index_team_counts()["PR"] == 2
    assert qdrant.index_team_counts(refresh=True)["PR"] == 1


@pytest.mark.parametrize("notion_data", [None, "invalid json"])
def test_unreadable_notion_manifest_does_not_hide_pr(tmp_path, monkeypatch, notion_data):
    notion_file, pr_file = tmp_path / "notion.json", tmp_path / "pr.json"
    if notion_data is not None:
        notion_file.write_text(notion_data, encoding="utf-8")
    pr_file.write_text(json.dumps({"points": [pr_result()]}), encoding="utf-8")
    monkeypatch.setattr(qdrant, "_LOCAL_JSON", notion_file)
    monkeypatch.setattr(qdrant, "_PR_LOCAL_JSON", pr_file, raising=False)
    monkeypatch.setattr(qdrant, "_TEAM_COUNTS", None)
    assert qdrant.index_team_counts(refresh=True) == {"PR": 1}


def test_pr_context_carries_confirmed_inferred_and_unknown_months():
    context = qdrant._format_results([
        pr_result("2024-06", False, "2024-06"),
        pr_result(), pr_result("", False, "지영님"),
    ])
    assert "작성월: 2024-06" in context
    assert "작성월 추정: 2025-08" in context
    assert "작성월: 미상" in context
    assert "지영님" in context
    assert PR_URL in context
    assert "문서 수정일" not in context


def test_pr_context_keeps_the_entire_row_including_its_final_status():
    result = pr_result()
    result["payload"]["text"] = "행사 세부사항 " * 300 + "행사는 아직 예정이며 완료되지 않았습니다."
    assert result["payload"]["text"] in qdrant._format_results([result])


def test_pr_is_not_misidentified_as_an_undated_public_notion_document():
    assert qdrant._vintage_note([pr_result()]) == ""
    other = {"score": 0.75, "payload": {"team": "BCM", "page_url": "https://www.notion.so/other"}}
    assert qdrant._vintage_note([pr_result(), other], f"출처: {PR_URL}") == ""


def test_existing_formatted_month_does_not_get_a_duplicate_notice():
    answer = qdrant.pr_answer_with_source("일본 행사 · 작성월 추정: **2025-08**", [pr_result()])
    assert answer.count("2025-08") == 1
    assert "참고 PR 자료의 작성월" not in answer


def test_month_backstop_prefers_the_named_result_and_does_not_list_unused_hits():
    results = [pr_result(title="일본 행사"), pr_result("2026-09", False, "2026-09", "브라질 입점")]
    answer = qdrant.pr_answer_with_source("브라질 입점 관련 자료입니다.", results)
    assert "작성월: 2026-09" in answer
    assert "2025-08" not in answer
    assert "일본 행사" not in answer


def test_month_backstop_without_a_named_result_uses_only_the_top_hit():
    results = [pr_result(), pr_result("2026-09", False, "2026-09", "브라질 입점")]
    answer = qdrant.pr_answer_with_source("관련 자료입니다.", results)
    assert "작성월 추정: 2025-08" in answer
    assert "2026-09" not in answer


def test_month_backstop_prefers_the_cited_row_link():
    results = [pr_result(), pr_result("2026-09", False, "2026-09", "브라질 입점")]
    results[0]["payload"]["page_url"] = PR_URL + "&range=G10"
    results[1]["payload"]["page_url"] = PR_URL + "&range=G20"
    answer = qdrant.pr_answer_with_source(f"입점 자료입니다. [원본]({PR_URL}&range=G20)", results)
    assert "작성월: 2026-09" in answer
    assert "2025-08" not in answer
    assert answer.count(PR_URL) == 1


def test_uncited_pr_hit_does_not_change_a_notion_answer():
    results = [pr_result(), {"score": 0.8, "payload": {
        "team": "BCM", "page_title": "업무 절차", "page_url": "https://www.notion.so/process"}}]
    answer = "[업무 절차](https://www.notion.so/process) 문서입니다."
    assert qdrant.pr_answer_with_source(answer, results) == answer


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 308])
async def test_empty_pr_search_has_source_and_never_uses_web(monkeypatch, count):
    monkeypatch.setattr(qdrant, "_embed_query", AsyncMock(return_value=[0.1]))
    monkeypatch.setattr(qdrant, "_search", lambda *a, **kw: [])
    monkeypatch.setattr(qdrant, "index_team_counts", lambda: {"PR": count})
    monkeypatch.setattr(qdrant, "get_flash_client", lambda: (_ for _ in ()).throw(AssertionError("PR used web")))
    answer = await qdrant.run("8월 이슈", team_key="PR")
    assert PR_URL in answer
    assert str(count) in answer
    assert "PR" in answer
    assert "팀 지정을 빼고" not in answer


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["embedding", "search", "answer_client", "answer"])
async def test_pr_service_failures_show_a_status_and_original_link(monkeypatch, stage):
    def fail(*args, **kwargs):
        raise RuntimeError("service unavailable")
    monkeypatch.setattr(qdrant, "_embed_query", AsyncMock(side_effect=RuntimeError("service unavailable")) if stage == "embedding" else AsyncMock(return_value=[0.1]))
    monkeypatch.setattr(qdrant, "_search", fail if stage == "search" else lambda *a, **kw: [pr_result()])
    monkeypatch.setattr(qdrant, "get_flash_client", fail if stage == "answer_client" else lambda: SimpleNamespace(generate=fail))
    answer = await qdrant.run("프레인 이슈", team_key="PR")
    assert PR_URL in answer
    assert "PR" in answer
    assert "실패" in answer or "못했습니다" in answer or "오류" in answer
    assert "자료가 없습니다" not in answer


@pytest.mark.asyncio
async def test_pr_prompt_and_answer_do_not_claim_historical_issues_are_current(monkeypatch):
    monkeypatch.setattr(qdrant, "_embed_query", AsyncMock(return_value=[0.1]))
    monkeypatch.setattr(qdrant, "_search", lambda *a, **kw: [pr_result()])
    prompts = []
    def generate(prompt, *args):
        prompts.append(prompt)
        return "행사 자료입니다."
    monkeypatch.setattr(qdrant, "get_flash_client", lambda: SimpleNamespace(generate=generate))
    answer = await qdrant.run("요즘 PR 이슈", team_key="PR")
    assert "항목마다" in prompts[0] and "작성월" in prompts[0]
    assert "현재 성과" in prompts[0]
    assert "예정" in prompts[0] and "완료" in prompts[0]
    assert "Notion 사내 문서 검색" not in prompts[0]
    assert "작성월 추정: 2025-08" in answer
    assert answer.count(PR_URL) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("query, sources", [("8월 PR 이슈 알려줘", None), ("@@PR 8월 이슈", None), ("8월 이슈", ["PR"])])
async def test_streaming_pr_timeout_keeps_the_original_source(routed_search, query, sources):
    agent, _, _ = routed_search
    agent._handle_qdrant = AsyncMock(side_effect=TimeoutError())
    events = [event async for event in agent.route_and_stream(query, enabled_sources=sources)]
    answer = "".join(data for kind, data in events if kind == "chunk")
    assert PR_URL in answer
    assert "PR 자료 검색이 지연" in answer
    assert "매출" not in answer


@pytest.mark.asyncio
async def test_streaming_pr_circuit_failure_keeps_the_original_source(routed_search, monkeypatch):
    agent, scopes, _ = routed_search
    monkeypatch.setattr("app.core.safety.get_circuit", lambda _: SimpleNamespace(is_available=lambda: False))
    events = [event async for event in agent.route_and_stream("8월 PR 이슈 알려줘")]
    answer = "".join(data for kind, data in events if kind == "chunk")
    assert PR_URL in answer
    assert "PR 자료 검색 서비스" in answer
    assert scopes == []
