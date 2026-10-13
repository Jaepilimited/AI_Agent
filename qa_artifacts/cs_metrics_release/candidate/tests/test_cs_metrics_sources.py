"""CS metrics must be queryable, independently monitored, and refreshed from data."""

import json
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.core import safety, schema_watch, value_lists


DOMESTIC = "skin1004-319714.cs_dashboard.domestic_cs_records"
OVERSEAS = "skin1004-319714.cs_dashboard.overseas_cs_refunds"
SOURCES = {"국내CS": DOMESTIC, "해외CS": OVERSEAS}


@pytest.mark.parametrize("key,table", SOURCES.items())
def test_default_and_explicit_source_scopes_can_query_cs(key, table):
    from app.agents.sql_agent import _allowed_tables_from_sources
    from app.config import get_settings

    assert table in get_settings().allowed_tables
    assert table in _allowed_tables_from_sources(None)
    assert _allowed_tables_from_sources([key]) == {table}
    assert table not in _allowed_tables_from_sources(["매출"])
    assert _allowed_tables_from_sources([]) == set()


@pytest.mark.parametrize("key,table", SOURCES.items())
def test_cs_schema_changes_are_escalated_for_the_queried_tables(key, table):
    assert "cs_dashboard" in schema_watch.WATCHED_DATASETS
    short = table.split(".", 1)[1]
    old = {short: {"process_status": "STRING"}}
    current = {short: {"process_status": "INT64"}}
    changes = schema_watch.diff(old, current)
    assert changes["watched"]
    assert any(short in change for change in changes["changed_types"])


@pytest.mark.parametrize("key,table", SOURCES.items())
def test_active_cs_load_notice_is_limited_to_the_referenced_source(key, table):
    manager = safety.MaintenanceManager()
    manager.auto_activate_table(key, "refresh")
    dataset, table_id = table.split(".")[1:]
    assert safety._MONITORED_TABLES[key] == (dataset, table_id)
    notice = safety.data_update_notice_for_sql(f"SELECT COUNT(*) FROM `{table}`", manager)
    assert key in notice
    other = OVERSEAS if table == DOMESTIC else DOMESTIC
    assert safety.data_update_notice_for_sql(f"SELECT COUNT(*) FROM `{other}`", manager) == ""


@pytest.fixture
def status_without_external_io(monkeypatch):
    from app.core import awards, inventory
    from app.db import mariadb

    monkeypatch.setattr(mariadb, "fetch_one", lambda *args, **kwargs: None)
    monkeypatch.setattr(mariadb, "fetch_all", lambda *args, **kwargs: [])
    monkeypatch.setattr(awards, "status", lambda: {})
    monkeypatch.setattr(inventory, "status", lambda: {})
    monkeypatch.setattr(safety, "_qdrant_cache", {"CS": 1})
    monkeypatch.setattr(safety, "_qdrant_cache_time", time.time())
    monkeypatch.setattr(safety, "_circuits", {})
    manager = safety.MaintenanceManager()
    monkeypatch.setattr(safety, "_maintenance_manager", manager)
    return manager


def test_status_cards_keep_domestic_overseas_and_notion_cs_separate(status_without_external_io):
    status_without_external_io.auto_activate_table("국내CS", "domestic refresh")
    services = safety.get_safety_status()["services"]
    assert services["국내CS"]["status"] == "updating"
    assert services["해외CS"]["status"] == "ok"
    assert services["국내CS"]["detail"] == "국내 CS 처리 기록"
    assert services["해외CS"]["detail"] == "해외 Shopify 환불 기록"
    assert services["국내CS"]["url"].endswith("?gid=0#gid=0")
    assert services["해외CS"]["url"].endswith("?gid=606652395#gid=606652395")
    assert services["CS"]["detail"] == "1 chunks"


def test_bq_failure_reaches_both_cs_status_cards(status_without_external_io):
    safety.get_circuit("bigquery").state = safety.CBState.OPEN
    services = safety.get_safety_status()["services"]
    assert services["국내CS"]["status"] == services["해외CS"]["status"] == "error"
    assert services["국내CS"]["url"] and services["해외CS"]["url"]


@pytest.mark.parametrize("name,table,column,cap", [
    ("DomesticCSChannel", DOMESTIC, "channel", 50),
    ("DomesticCSClaimType", DOMESTIC, "claim_type", 50),
    ("DomesticCSStatus", DOMESTIC, "process_status", 50),
    ("DomesticCSReason", DOMESTIC, "reason_normalized", 100),
    ("DomesticCSCompensation", DOMESTIC, "compensation_eligibility", 50),
    ("OverseasCSStatus", OVERSEAS, "process_status", 50),
    ("OverseasCSCountry", OVERSEAS, "country_name", 300),
    ("OverseasCSReason", OVERSEAS, "return_type_normalized", 100),
    ("OverseasCSNormalization", OVERSEAS, "normalization_status", 50),
])
def test_cs_vocabularies_read_distinct_values_from_the_corresponding_table(
    monkeypatch, name, table, column, cap
):
    from app.core import bigquery

    calls = []

    def query(sql):
        calls.append(sql)
        return [{"v": "observed value"}]

    monkeypatch.setattr(bigquery, "get_bigquery_client", lambda: SimpleNamespace(execute_query=query))
    assert value_lists._fetch_live(name) == ["observed value"]
    assert f"SELECT DISTINCT `{column}`" in calls[0]
    assert f"FROM `{table}`" in calls[0]
    assert f"`{column}` IS NOT NULL" in calls[0]
    assert calls[0].endswith(f"LIMIT {cap + 1}")


@pytest.fixture
def cached_lists(monkeypatch):
    cache = {name: ["observed value"] for name in value_lists.REGISTRY}
    monkeypatch.setattr(value_lists, "ensure_value_cache_table", lambda: None)
    monkeypatch.setattr(value_lists, "_is_stale", lambda: False)

    def fetch(sql, params=None):
        if params:
            return {"payload": json.dumps(cache[params[0]])} if params[0] in cache else None
        return {"t": datetime.now(), "c": len(cache)}

    def execute(sql, params):
        cache[params[0]] = json.loads(params[1])

    monkeypatch.setattr(value_lists, "fetch_one", fetch)
    monkeypatch.setattr(value_lists, "execute", execute)
    return cache


def test_optional_reason_can_refresh_from_empty_to_real_values(monkeypatch, cached_lists):
    monkeypatch.setattr(value_lists, "_fetch_live", lambda name: [])
    assert value_lists.refresh("OverseasCSReason") == {"OverseasCSReason": 0}
    assert "OverseasCSReason" not in value_lists.status()["missing"]
    rendered = value_lists.fill("{{VALUES:OverseasCSReason}}")
    assert "0개" in rendered and "NULL/빈값" in rendered

    monkeypatch.setattr(value_lists, "_fetch_live", lambda name: ["배송 지연"])
    assert value_lists.refresh("OverseasCSReason") == {"OverseasCSReason": 1}
    assert "배송 지연" in value_lists.fill("{{VALUES:OverseasCSReason}}")
    assert "0개" not in value_lists.fill("{{VALUES:OverseasCSReason}}")


def test_optional_reason_missing_cache_and_empty_required_list_still_fail(cached_lists):
    cached_lists.pop("OverseasCSReason")
    cached_lists["Country"] = []
    missing = value_lists.status()["missing"]
    assert {"Country", "OverseasCSReason"} <= set(missing)
    assert value_lists.render("Country") == ""
    assert value_lists.render("OverseasCSReason") == ""


def test_failed_optional_reason_refresh_preserves_the_last_values(monkeypatch, cached_lists):
    cached_lists["OverseasCSReason"] = ["previous reason"]

    def unavailable(name):
        raise RuntimeError("source unavailable")

    monkeypatch.setattr(value_lists, "_fetch_live", unavailable)
    assert value_lists.refresh("OverseasCSReason") == {}
    assert value_lists.values("OverseasCSReason") == ["previous reason"]
