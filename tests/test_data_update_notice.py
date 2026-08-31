"""User-visible notices for the exact BigQuery data being refreshed."""

import pytest

from app.core import safety
from app.core.safety import MaintenanceManager


SALES_SQL = (
    "SELECT Country, SUM(Sales1_R) AS revenue "
    "FROM `skin1004-319714.Sales_Integration.SALES_ALL_Backup` "
    "GROUP BY Country"
)
PRODUCT_SQL = (
    "SELECT Product_Name, SUM(Total_Qty) AS qty "
    "FROM `skin1004-319714.Sales_Integration.Product` "
    "GROUP BY Product_Name"
)


def test_notice_names_only_active_tables_referenced_by_sql():
    """Removing SQL/table matching would wrongly name unrelated updating data."""
    manager = MaintenanceManager()
    manager.auto_activate_table("매출", "reload")
    manager.auto_activate_table("광고", "reload")

    notice = safety.data_update_notice_for_sql(SALES_SQL, manager=manager)

    assert "매출" in notice
    assert "광고" not in notice
    assert "현재 업데이트되고 있습니다" in notice


def test_notice_is_empty_when_only_different_table_is_updating():
    """Using aggregate maintenance state would create this false warning."""
    manager = MaintenanceManager()
    manager.auto_activate_table("광고", "reload")

    assert safety.data_update_notice_for_sql(SALES_SQL, manager=manager) == ""


def test_notice_lists_each_referenced_table_that_is_updating():
    manager = MaintenanceManager()
    manager.auto_activate_table("매출", "reload")
    manager.auto_activate_table("제품", "reload")
    joined_sql = f"{SALES_SQL} UNION ALL {PRODUCT_SQL}"

    notice = safety.data_update_notice_for_sql(joined_sql, manager=manager)

    assert "매출·제품" in notice


def _patch_sql_pipeline(monkeypatch, sql_agent, generated_sql=SALES_SQL):
    rows = [{"country": "일본", "revenue": 100}]
    monkeypatch.setattr(
        sql_agent, "generate_sql", lambda state: {"generated_sql": generated_sql}
    )
    monkeypatch.setattr(sql_agent, "validate_sql_node", lambda state: {"sql_valid": True})
    monkeypatch.setattr(
        sql_agent, "_enforce_partition_filter", lambda sql, *args, **kwargs: sql
    )
    monkeypatch.setattr(sql_agent, "execute_sql", lambda state: {"sql_result": rows})
    return rows


@pytest.mark.asyncio
async def test_non_streaming_sql_answer_starts_with_relevant_update_notice(monkeypatch):
    """Dropping the run_sql_agent integration would hide the notice from callers."""
    from app.agents import sql_agent

    manager = MaintenanceManager()
    manager.auto_activate_table("매출", "reload")
    monkeypatch.setattr(safety, "_maintenance_manager", manager)
    _patch_sql_pipeline(monkeypatch, sql_agent)
    monkeypatch.setattr(sql_agent, "format_answer", lambda state: {"answer": "조회 답변"})

    answer = await sql_agent.run_sql_agent("국가별 매출")

    assert answer.startswith("> ⚠️ **데이터 업데이트 중**")
    assert answer.endswith("조회 답변")


def test_streaming_sql_answer_emits_notice_before_answer(monkeypatch):
    """Appending the notice after streaming would make it easy to miss."""
    from app.agents import sql_agent

    class FakeLlm:
        def generate_stream(self, *_args, **_kwargs):
            yield "조회 답변"

    manager = MaintenanceManager()
    manager.auto_activate_table("매출", "reload")
    monkeypatch.setattr(safety, "_maintenance_manager", manager)
    monkeypatch.delenv("BQ_TOOL_LOOP", raising=False)
    monkeypatch.delenv("BQ_FAST_ANSWER", raising=False)
    _patch_sql_pipeline(monkeypatch, sql_agent)
    monkeypatch.setattr("app.core.llm.get_flash_client", lambda: FakeLlm())
    monkeypatch.setattr(sql_agent, "_try_generate_chart", lambda *_args, **_kwargs: "")

    chunks = list(sql_agent.run_sql_agent_stream("국가별 매출"))

    assert chunks[0].startswith("> ⚠️ **데이터 업데이트 중**")
    assert "조회 답변" in "".join(chunks)


@pytest.mark.asyncio
async def test_unlimited_answer_keeps_the_same_update_notice(monkeypatch):
    """The full-data follow-up must not bypass the update disclosure."""
    from app.agents import sql_agent

    class FakeBigQuery:
        def execute_query(self, *_args, **_kwargs):
            return [{"country": "일본", "revenue": 100}]

    class FakeLlm:
        def generate(self, *_args, **_kwargs):
            return "전체 조회 답변"

    manager = MaintenanceManager()
    manager.auto_activate_table("매출", "reload")
    monkeypatch.setattr(safety, "_maintenance_manager", manager)
    monkeypatch.setattr(sql_agent, "validate_sql", lambda *_args, **_kwargs: (True, ""))
    monkeypatch.setattr(sql_agent, "get_bigquery_client", lambda: FakeBigQuery())
    monkeypatch.setattr(sql_agent, "get_flash_client", lambda: FakeLlm())

    answer = await sql_agent.run_sql_agent_unlimited(SALES_SQL, "전체 데이터")

    assert answer.startswith("> ⚠️ **데이터 업데이트 중**")
    assert "전체 조회 답변" in answer


@pytest.mark.asyncio
async def test_orchestrator_does_not_add_aggregate_warning_for_unrelated_table(
    monkeypatch,
):
    """Leaving the old aggregate warning would still flag sales during an ad reload."""
    from app.agents import orchestrator
    from app.agents import sql_agent

    manager = MaintenanceManager()
    manager.auto_activate_table("광고", "reload")
    monkeypatch.setattr(safety, "_maintenance_manager", manager)
    monkeypatch.setattr(
        sql_agent, "run_sql_agent_stream", lambda *_args, **_kwargs: iter(["조회 답변"])
    )

    agent = orchestrator.OrchestratorAgent()
    events = [
        event
        async for event in agent.route_and_stream(
            "@@매출 국가별 매출", messages=[], enabled_sources=["매출"]
        )
    ]
    answer = "".join(data for kind, data in events if kind == "chunk")

    assert answer == "조회 답변"
    assert "업데이트" not in answer


# ── 적재 최전선(최근 1~2일)은 답변에 공시한다 (2026-08-31 사용자 제보) ──────
#
# ⛔ 감지가 **truncate(행 줄어듦)만** 봤다. `maintenance_auto_detect_loop` 주석에
#    "단순 append 는 조회해도 안전" 이라고 적혀 있는데, **날짜를 지목한 질문에는
#    틀리다.** 8/30 KBT 광고비를 물었을 때 그 날짜가 아직 채워지는 중이라
#    NaverGFA·NaverSearch 가 통째로 빠지고 Google 이 447,791원(실제 3,863,561원,
#    8.6배)으로 나갔다. 에러도 경고도 없었다.
#
# ⚠️ 반대 방향이 없으면 모든 답변에 경고가 붙고, 매번 붙는 경고는 곧 안 읽힌다.

_AD = "`skin1004-319714.marketing_analysis.integrated_ad`"


def _note(sql: str):
    from datetime import date

    from app.core.safety import loading_edge_notice_for_sql
    return loading_edge_notice_for_sql(sql, today=date(2026, 8, 31))


def test_recent_dates_are_disclosed():
    for asked in ("2026-08-31", "2026-08-30", "2026-08-29"):
        note = _note(f"SELECT 1 FROM {_AD} WHERE date = '{asked}'")
        assert note, f"{asked} 를 물었는데 아무 말이 없다"
        assert "채워지는 중" in note


def test_a_month_range_touching_today_is_disclosed():
    """말일이 오늘이면 그 달 전체 집계도 아직 안 끝났다."""
    note = _note(f"SELECT 1 FROM {_AD} WHERE date BETWEEN '2026-08-01' AND '2026-08-31'")
    assert note and "광고" in note


def test_past_only_and_unmonitored_stay_quiet():
    """⚠️ 매번 붙는 경고는 곧 안 읽힌다 — 지난 기간엔 아무 말도 하지 않는다."""
    assert _note(f"SELECT 1 FROM {_AD} WHERE date = '2026-08-20'") == ""
    assert _note(f"SELECT 1 FROM {_AD} WHERE date BETWEEN '2026-01-01' AND '2026-06-30'") == ""
    assert _note("SELECT 1 FROM `p.d.not_monitored` WHERE date = '2026-08-30'") == ""
    assert _note(f"SELECT 1 FROM {_AD}") == "", "날짜를 안 물었으면 최전선도 없다"
    assert _note("") == ""


def test_the_disclosure_rides_the_existing_answer_hook():
    """공시는 코드가 붙인다 — 프롬프트에 맡기면 확률이다."""
    import inspect

    from app.core import safety
    src = inspect.getsource(safety.data_update_notice_for_sql)
    assert "loading_edge_notice_for_sql(sql)" in src, \
        "업데이트 중이 아닐 때 최전선 공시로 넘어가지 않는다"
