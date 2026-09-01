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


# ── 날짜만 보고 하는 공시는 하지 않는다 (2026-09-02 사용자 지시) ────────────
#
# 한때 "질문이 최근 2일을 포함하면" 무조건 한 줄을 붙였다(`loading_edge_notice_for_sql`).
# 그건 적재 상태를 **본 것이 아니라 추측한 것**이라, 적재가 진작 끝난 뒤에도 어제·오늘을
# 물을 때마다 붙었다. 사용자 지시: *"이거는 안내하지말고 데이터가 업데이트 중일때만
# 안내해."*
#
# ⛔ 되살리지 마라 — 매번 뜨는 경고는 곧 아무도 안 읽고, 그러면 **진짜 적재 중**일 때의
#    공시(점검 감지 · 수정 시각이 지금 움직임)까지 함께 무시당한다.

_AD = "`skin1004-319714.marketing_analysis.integrated_ad`"


def test_a_recent_date_alone_says_nothing():
    """어제·오늘을 물었어도 **적재 중이 아니면** 아무 말도 하지 않는다."""
    from app.core.safety import data_update_notice_for_sql

    mm = _mm()
    for asked in ("2026-08-31", "2026-08-30", "2026-08-29"):
        sql = f"SELECT 1 FROM {_AD} WHERE date = '{asked}'"
        assert data_update_notice_for_sql(sql, mm) == "", f"{asked} 에 공시가 붙었다"
    assert data_update_notice_for_sql(
        f"SELECT 1 FROM {_AD} WHERE date BETWEEN '2026-08-01' AND '2026-08-31'", mm) == ""


def test_the_date_only_disclosure_is_gone_for_good():
    """⛔ 문구도 함수도 남기지 마라 — 남으면 다음 사람이 다시 배선한다."""
    import inspect

    from app.core import safety
    assert not hasattr(safety, "loading_edge_notice_for_sql")
    assert "채워지는 중" not in inspect.getsource(safety)


# ── "지금 적재 중" 은 실제 수정 시각으로 판정한다 (2026-08-31 사용자 지시) ────
#
# 사용자: "데이터가 적재중이면 답변에 적재중이라고 표기해야함. 그래야 사람들이
#          이게 오답이 아니구나라고 판단함."
#
# ⛔ 점검 감지(`maintenance_auto_detect_loop`)는 행이 **줄어드는 것**만 본다.
#    실제 사고는 append 중이었고 그래서 화면도 답변도 아무 말이 없었다 —
#    8/30 KBT 광고비가 91.5만원(실제 571.9만원)으로 나갔다.

_SALES = "SELECT 1 FROM `skin1004-319714.Sales_Integration.SALES_ALL_Backup` WHERE Date='2026-06-01'"
_OLD_AD = "SELECT 1 FROM `skin1004-319714.marketing_analysis.integrated_ad` WHERE date='2026-06-01'"


def _mm():
    from app.core.safety import MaintenanceManager
    return MaintenanceManager()


def _loading(label="광고", *, ago=120):
    """적재가 **진행 중인** 감시 상태 — 폴링 사이에 수정 시각이 앞으로 갔다.

    ⚠️ 한 번만 기록하면 '적재 중' 이 아니다. 그건 "최근에 수정됐다" 까지만
       말해 주고, 그 사실은 적재가 **끝난 뒤에도** 한동안 참이다.
    """
    mm = _mm()
    mm.note_table_modified(label, ago + 90)   # 폴링 1회차
    mm.note_table_modified(label, ago)        # 폴링 2회차 — 값이 움직였다
    return mm


def test_a_table_being_written_right_now_is_called_out():
    from app.core.safety import recent_load_notice_for_sql

    mm = _loading(ago=120)                     # 2분 전 수정, 그 사이 계속 움직였다
    note = recent_load_notice_for_sql(_OLD_AD, mm)
    assert "지금 적재 중입니다" in note
    assert "틀린 값이 아니라" in note, "왜 알리는지가 빠지면 사용자가 오답으로 읽는다"
    assert "광고(2분 전)" in note


def test_a_finished_load_does_not_claim_to_be_loading():
    """⛔ 2026-08-31 사용자 제보: *"지금 적재중입니다는 맞는 표현이 아님.
    이미 업데이트가 되어있음"* — 11분 전에 **끝난** 적재였다.

    수정 시각만 보면 적재가 끝난 뒤에도 창이 닫힐 때까지 계속 참이다. 쓰는 중이면
    폴링마다 값이 앞으로 가고, 끝났으면 굳는다 — 굳었으면 말하지 않는다.
    """
    from app.core.safety import recent_load_notice_for_sql

    mm = _mm()
    for ago in (660, 720, 780):               # 11분 → 12분 → 13분: 값이 굳었다
        mm.note_table_modified("광고", ago)
    assert recent_load_notice_for_sql(_OLD_AD, mm) == ""

    # 한 번 움직인 뒤에도 그 창을 넘기면 조용해진다 (적재가 끝난 것이다)
    mm = _mm()
    mm.note_table_modified("광고", 600)
    mm.note_table_modified("광고", 400)       # 움직였다 — 그러나 그 뒤로 잠잠하다
    import time

    from app.core.safety import _LOAD_IN_PROGRESS_SECONDS
    mm.tables["광고"]["moved_ts"] = time.time() - _LOAD_IN_PROGRESS_SECONDS - 30
    assert recent_load_notice_for_sql(_OLD_AD, mm) == ""


def test_other_tables_and_settled_data_stay_quiet():
    """⚠️ 매번 뜨는 경고는 곧 아무도 안 읽는다."""
    from app.core.safety import recent_load_notice_for_sql

    mm = _loading(ago=120)
    assert recent_load_notice_for_sql(_SALES, mm) == "", "다른 테이블 질문에 붙었다"

    mm.note_table_modified("광고", 3600)         # 1시간 전 = 적재가 끝났다
    assert recent_load_notice_for_sql(_OLD_AD, mm) == ""
    assert recent_load_notice_for_sql(_OLD_AD, _mm()) == "", "아는 게 없으면 말하지 않는다"
    assert recent_load_notice_for_sql("", mm) == ""


def test_polling_jitter_is_not_mistaken_for_a_write():
    """⚠️ 경과 초는 정수로 오고 왕복 지연도 매번 다르다. 1~2초 흔들림을 쓰기로
    읽으면 **가만히 있는 테이블이 영원히 적재 중**이 된다."""
    from app.core.safety import recent_load_notice_for_sql

    mm = _mm()
    for ago in (120, 179, 241, 299):          # 60초 간격 폴링의 자연스러운 흔들림
        mm.note_table_modified("광고", ago)
    assert recent_load_notice_for_sql(_OLD_AD, mm) == ""


def test_a_dead_monitor_turns_the_notice_off_by_itself():
    """⛔ 경과 초를 그대로 저장하면 루프가 멈출 때 그 값이 얼어붙어 **영원히
    '방금 적재됨'** 이 된다. 절대 시각으로 두면 경과가 자연히 커져 꺼진다."""
    import time

    from app.core.safety import _ACTIVE_LOAD_SECONDS, recent_load_notice_for_sql

    mm = _loading(ago=60)
    mm.tables["광고"]["modified_ts"] = time.time() - _ACTIVE_LOAD_SECONDS - 10
    assert recent_load_notice_for_sql(_OLD_AD, mm) == ""


def test_the_two_notices_have_a_fixed_priority():
    """점검 중 > 방금 적재. 한 번에 하나만 붙고, 날짜만 보는 공시는 없다."""
    from app.core.safety import data_update_notice_for_sql

    mm = _loading(ago=60)
    mm.auto_activate_table("광고", "테이블 적재 중 (row 1 < 기준 2)")
    assert "데이터 업데이트 중" in data_update_notice_for_sql(_OLD_AD, mm)

    mm.auto_deactivate_table("광고")
    note = data_update_notice_for_sql(_OLD_AD, mm)
    assert "지금 적재 중입니다" in note and "채워지는 중" not in note


def test_the_monitor_records_on_every_poll_not_only_on_truncate():
    """⛔ 점검 판정 안에 기록을 두면 append 때는 기록이 안 남아 공시도 못 한다."""
    import inspect

    from app.core import safety
    src = inspect.getsource(safety.maintenance_auto_detect_loop)
    before_detect = src.split("Detection 1")[0]
    assert "note_table_modified" in before_detect, \
        "판정보다 먼저, 조건 없이 기록해야 한다"
