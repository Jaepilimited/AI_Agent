"""BP combines QA and strictly scoped CS documents, with actual source links."""

from types import SimpleNamespace

import pytest

from app.agents import cs_agent as bp
from app.agents import orchestrator as orch
from app.agents import qdrant_agent as docs


QA = {
    "tab": "센텔라", "brand": "SKIN1004", "line": "센텔라", "product": "센텔라 앰플",
    "category": "사용법", "question": "사용법", "answer": "QA에서 확인한 사용법",
}
DOC_URL = "https://www.notion.so/3362b4283b00812b9778d8c144f29274"
DOC = {
    "score": 0.8,
    "payload": {"team": "CS", "page_title": "센텔라 앰플 제품 문서", "page_url": DOC_URL,
                "text": "CS 문서에서 확인한 제품 근거", "last_edited_time": "2026-09-15"},
}


@pytest.fixture
def evidence(monkeypatch):
    state = {"qa": [QA], "docs": [DOC], "product": "제품정보 스펙 근거", "prompts": [],
             "lookups": [], "embedded": [], "gaps": [], "error": False, "generate_error": False}
    monkeypatch.setattr(bp, "get_settings", lambda: SimpleNamespace(cs_spreadsheet_id="actual-configured-sheet"))
    monkeypatch.setattr(bp, "_cache_loaded", True)
    monkeypatch.setattr(bp, "_qa_cache", [QA])
    monkeypatch.setattr(bp, "search_qa", lambda query, top_k=10: state["qa"])
    monkeypatch.setattr(bp, "_product_info_block", lambda query: state["product"])
    monkeypatch.setattr(bp, "_log_knowledge_gap", state["gaps"].append)

    async def embed(query):
        state["embedded"].append(query)
        return [0.25]

    def search(vector, team_filter=None, top_k=8):
        state["lookups"].append((team_filter, top_k))
        if state["error"]:
            raise RuntimeError("backend unavailable")
        return state["docs"]

    class LLM:
        def generate(self, prompt, **kwargs):
            state["prompts"].append(prompt)
            if state["generate_error"]:
                raise RuntimeError("provider unavailable")
            return "통합 답변입니다.\n\n> 💡 **이런 것도 물어보세요**\n> - 사용 순서는?"

        def generate_stream(self, prompt, **kwargs):
            answer = self.generate(prompt, **kwargs)
            # The source footer must precede a follow-up heading split across tokens.
            for start in range(0, len(answer), 3):
                yield answer[start:start + 3]

    monkeypatch.setattr(docs, "_embed_query", embed)
    monkeypatch.setattr(docs, "_search", search)
    monkeypatch.setattr(bp, "get_flash_client", LLM)
    return state


async def answer(streaming, query="센텔라 앰플 사용법 알려줘"):
    if streaming:
        return "".join([chunk async for chunk in bp.run_stream(query)])
    return await bp.run(query)


def test_registry_exposes_only_bp_and_preserves_old_cs_aliases(evidence):
    registry = orch.OrchestratorAgent.get_db_registry()
    assert not any(entry["key"] == "CS" for entry in registry)
    entry = next(entry for entry in registry if entry["key"] == "BP")
    assert entry["route"] == "cs"
    assert "제품 Q&A" in entry["desc"] and "CS 문서" in entry["desc"]
    assert entry["links"] == bp.source_links()
    for name in ("CS", "cs", "cs문서", "cs자료", "notion_cs", "BP"):
        parsed, clean = orch.OrchestratorAgent.parse_db_prefix(f"@@{name} 제품정보 알려줘")
        assert parsed["key"] == "BP" and parsed["route"] == "cs"
        assert clean == "제품정보 알려줘"
    parsed, clean = orch.OrchestratorAgent.parse_db_prefix("@@CS @@BP 제품정보 알려줘")
    assert isinstance(parsed, dict) and parsed["key"] == "BP"


def test_source_links_use_the_configured_sheet_and_verified_product_root(evidence):
    from app.core.product_info import ROOT_PAGE_ID

    links = bp.source_links()
    assert links[0] == {"label": "제품 Q&A", "url": "https://docs.google.com/spreadsheets/d/actual-configured-sheet/edit"}
    assert ROOT_PAGE_ID.replace("-", "") in links[1]["url"]
    assert not evidence["lookups"] and not evidence["embedded"]


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_one_synthesis_uses_qa_documents_and_product_specs_with_links(evidence, streaming):
    result = await answer(streaming)
    assert len(evidence["prompts"]) == 1
    prompt = evidence["prompts"][0]
    assert "QA에서 확인한 사용법" in prompt
    assert "CS 문서에서 확인한 제품 근거" in prompt
    assert "제품정보 스펙 근거" in prompt
    assert evidence["lookups"] == [("CS", docs.TOP_K)]
    assert "[제품 Q&A](https://docs.google.com/spreadsheets/d/actual-configured-sheet/edit)" in result
    assert f"[센텔라 앰플 제품 문서]({DOC_URL})" in result
    assert result.index(DOC_URL) < result.index("이런 것도 물어보세요")
    assert result.count("BP 자료 원본") == 1


@pytest.mark.asyncio
async def test_document_search_uses_current_question_and_rechecks_team_scope(evidence):
    evidence["docs"] = [
        DOC,
        {"score": 0.99, "payload": {**DOC["payload"], "team": "PEOPLE", "text": "타 팀 비공개 문서"}},
        {"score": 0.3, "payload": {**DOC["payload"], "text": "무관한 문서"}},
    ]
    result, ok = await bp._search_cs_documents("[이전 대화] 다른 제품\n[현재 질문] 센텔라 앰플")
    assert ok and result == [DOC]
    assert evidence["embedded"] == ["센텔라 앰플"]
    assert evidence["lookups"] == [("CS", docs.TOP_K)]


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("cache_loaded", [False, True])
async def test_cs_documents_survive_missing_or_unmatched_qa(evidence, monkeypatch, streaming, cache_loaded):
    evidence.update(qa=[], product="")
    monkeypatch.setattr(bp, "_cache_loaded", cache_loaded)
    monkeypatch.setattr(bp, "_qa_cache", [])

    async def no_sheet():
        return 0

    monkeypatch.setattr(bp, "warmup", no_sheet)
    result = await answer(streaming)
    assert "통합 답변" in result
    assert "CS 문서에서 확인한 제품 근거" in evidence["prompts"][0]
    assert DOC_URL in result
    assert not evidence["gaps"]


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_real_qa_warmup_failure_does_not_discard_cs_documents(evidence, monkeypatch, streaming):
    evidence.update(qa=[], product="")
    monkeypatch.setattr(bp, "_cache_loaded", False)
    monkeypatch.setattr(bp, "_qa_cache", [])

    async def unavailable_sheet():
        raise RuntimeError("QA sheet unavailable")

    monkeypatch.setattr(bp, "load_all_sheets", unavailable_sheet)
    result = await answer(streaming)
    assert not bp._cache_loaded
    assert "CS 문서에서 확인한 제품 근거" in evidence["prompts"][0]
    assert DOC_URL in result
    assert "QA sheet unavailable" not in result
    assert not evidence["gaps"]


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_product_info_alone_remains_usable(evidence, streaming):
    evidence.update(qa=[], docs=[])
    await answer(streaming)
    assert "제품정보 스펙 근거" in evidence["prompts"][0]
    assert not evidence["gaps"]


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_document_failure_preserves_qa_and_reports_the_partial_scope(evidence, streaming):
    evidence["error"] = True
    result = await answer(streaming)
    assert "QA에서 확인한 사용법" in evidence["prompts"][0]
    assert "CS 문서를 이번에 확인하지 못했습니다" in result
    assert "backend unavailable" not in result
    assert "actual-configured-sheet" in result


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_no_match_logs_one_gap_and_still_links_to_the_actual_sources(evidence, streaming):
    evidence.update(qa=[], docs=[], product="")
    result = await answer(streaming)
    assert len(evidence["gaps"]) == 1
    assert "관련 근거를 찾지 못했습니다" in evidence["prompts"][0]
    assert "actual-configured-sheet" in result and bp._CS_DOCUMENT_ROOT_URL in result


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_synthesis_failure_keeps_both_raw_evidence_sources(evidence, streaming):
    evidence["generate_error"] = True
    result = await answer(streaming)
    assert "QA에서 확인한 사용법" in result
    assert "CS 문서에서 확인한 제품 근거" in result
    assert DOC_URL in result
    assert "provider unavailable" not in result


def test_citations_are_deduplicated_and_cannot_link_to_foreign_or_script_urls(evidence):
    bad = {"score": 0.9, "payload": {**DOC["payload"], "page_url": "javascript:alert(1)"}}
    other = {"score": 0.9, "payload": {**DOC["payload"], "team": "DB", "page_url": "https://www.notion.so/private"}}
    footer = bp._source_footer([DOC, DOC, bad, other])
    assert footer.count(DOC_URL) == 1
    assert "javascript:" not in footer and "/private" not in footer


def test_bp_provenance_keeps_document_followups_in_bp(evidence):
    context = "AI: 사내 문서에서 확인한 제품 정보입니다." + bp._source_footer([DOC])
    assert orch._previous_route(context) == "cs"


@pytest.mark.parametrize("heading", ["CS 대시보드 기준입니다.", "**해외 CS 대시보드 · 조회 안내**"])
def test_dashboard_provenance_takes_priority_over_the_cs_product_words(heading):
    assert orch._previous_route(f"AI: {heading} 제품 Q&A와 집계 기준이 다릅니다.") == "bigquery"
