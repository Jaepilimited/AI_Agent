"""Regression tests for financial P&L access control."""

from types import SimpleNamespace

import pytest

from app.api import admin_group_api
from app.agents.orchestrator import OrchestratorAgent
from app.agents.sql_agent import (
    _allowed_tables_from_sources,
    _load_prompt,
    _mask_fi_prompt,
)
from app.core.security import FI_ACCESS_DENIED_MESSAGE, validate_sql
from app.db.models import User


ADMIN = User(id=1, email="admin@example.com", role="admin")
FI_TABLE = "skin1004-319714.Sales_Integration.FI_LLM_Flat"
FI_SQL = f"SELECT SUM(Amount) FROM `{FI_TABLE}` LIMIT 10"


def test_validate_sql_blocks_fi_when_user_allowlist_excludes_it():
    """Restoring FI to a non-authorized allowlist must make this test fail."""
    allowed_tables = _allowed_tables_from_sources(None, can_view_fi=False)

    is_valid, error = validate_sql(FI_SQL, allowed_tables=allowed_tables)

    assert is_valid is False
    assert error == FI_ACCESS_DENIED_MESSAGE


def test_validate_sql_default_allowlist_keeps_backward_compatibility():
    """Changing validate_sql's default to fail-closed must break internal compatibility."""
    is_valid, error = validate_sql(FI_SQL)

    assert is_valid is True
    assert error == ""


def test_prompt_mask_removes_only_fi_sections_and_preserves_neighbors():
    """Over-broad FI masking must break the neighboring table sections."""
    full_prompt = _load_prompt("sql_generator.txt", can_view_fi=True)
    masked_prompt = _load_prompt("sql_generator.txt", can_view_fi=False)
    table_13_header = "## 테이블 13:"
    table_14_header = "## 테이블 14: FI_LLM_Flat"

    table_13_section = full_prompt[
        full_prompt.index(table_13_header):full_prompt.index(table_14_header)
    ]
    assert "FI_LLM_Flat" not in masked_prompt
    assert table_13_section in masked_prompt
    assert "## 출력 형식" in masked_prompt

    adjacent_fixture = """## 테이블 13: Keep Before
before-body
## 테이블 14: FI_LLM_Flat
secret-body
## 테이블 15: Keep After
after-body
"""
    masked_fixture = _mask_fi_prompt(adjacent_fixture)
    assert "## 테이블 13: Keep Before\nbefore-body" in masked_fixture
    assert "## 테이블 15: Keep After\nafter-body" in masked_fixture
    assert "FI_LLM_Flat" not in masked_fixture


@pytest.mark.asyncio
async def test_unauthorized_fi_query_returns_denial_without_llm(monkeypatch):
    """Moving the FI gate after classification must trigger the fail-fast LLM doubles."""
    def fail_if_called(*args, **kwargs):
        raise AssertionError("LLM must not be called for an unauthorized FI query")

    monkeypatch.setattr("app.agents.orchestrator.get_flash_client", fail_if_called)
    monkeypatch.setattr("app.agents.orchestrator.get_llm_client", fail_if_called)

    result = await OrchestratorAgent().route_and_execute(
        "영업이익 알려줘",
        can_view_fi=False,
    )

    assert result["answer"] == FI_ACCESS_DENIED_MESSAGE


@pytest.mark.asyncio
async def test_authorized_fi_query_continues_to_existing_bigquery_path(monkeypatch):
    """Blocking authorized users at the route gate must break the BigQuery sentinel result."""
    agent = OrchestratorAgent()
    observed = {"called": False, "can_view_fi": None}

    async def fake_bigquery_handler(*args, **kwargs):
        observed["called"] = True
        observed["can_view_fi"] = kwargs.get("can_view_fi")
        return {"source": "bigquery", "answer": "authorized-fi-result"}

    monkeypatch.setattr(agent, "_handle_bigquery", fake_bigquery_handler)

    result = await agent.route_and_execute(
        "영업이익 알려줘",
        can_view_fi=True,
        enabled_sources=["손익"],
    )

    assert observed == {"called": True, "can_view_fi": True}
    assert result == {"source": "bigquery", "answer": "authorized-fi-result"}


@pytest.mark.asyncio
async def test_admin_can_update_fi_access_without_external_db(monkeypatch):
    """Removing the FI update endpoint must break the observable permission change."""
    state = {"can_view_fi": False}

    async def fake_fetch_one(sql, params=()):
        return {"id": 17, "username": "hejin", "display_name": "진한얼"}

    async def fake_execute(sql, params=()):
        state["can_view_fi"] = bool(params[0])
        return 1

    monkeypatch.setattr(admin_group_api, "_fetch_one", fake_fetch_one)
    monkeypatch.setattr(admin_group_api, "_execute", fake_execute)

    result = await admin_group_api.update_fi_access(
        17,
        SimpleNamespace(can_view_fi=True),
        ADMIN,
    )

    assert state["can_view_fi"] is True
    assert result == {"ok": True, "ad_user_id": 17, "can_view_fi": True}


@pytest.mark.asyncio
async def test_admin_user_list_exposes_fi_and_signup_state(monkeypatch):
    """Dropping either selected field or the FI-only condition must break the list contract."""
    row = {
        "id": 17,
        "username": "hejin",
        "display_name": "진한얼",
        "email": "hejin@example.com",
        "department": "Corporate Strategy > 경영관리",
        "can_view_fi": 1,
        "user_id": None,
        "group_names": None,
    }

    async def fake_fetch_all(sql, params=()):
        assert "a.can_view_fi" in sql
        assert "u.id AS user_id" in sql
        assert "LEFT JOIN users u ON u.ad_user_id = a.id" in sql
        assert "a.can_view_fi = 1" in sql
        return [row]

    monkeypatch.setattr(admin_group_api, "_fetch_all", fake_fetch_all)

    result = await admin_group_api.list_ad_users(
        user=ADMIN,
        dept=None,
        search=None,
        group_id=None,
        unassigned=False,
        fi_only=True,
    )

    assert result == [row]


@pytest.mark.asyncio
async def test_admin_stats_reports_total_fi_access_count(monkeypatch):
    """Removing the FI count from admin stats must break the top-level counter."""
    async def fake_fetch_one(sql, params=()):
        assert "can_view_fi = 1" in sql
        return {
            "total_ad_users": 362,
            "assigned_users": 120,
            "total_groups": 8,
            "fi_allowed_users": 8,
        }

    monkeypatch.setattr(admin_group_api, "_fetch_one", fake_fetch_one)

    result = await admin_group_api.ad_stats(user=ADMIN)

    assert result["fi_allowed_users"] == 8
