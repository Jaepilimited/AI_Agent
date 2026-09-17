"""The daily relay must keep the rolling monthly comparison populated."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def relay():
    path = Path(__file__).resolve().parents[1] / "scripts" / "fx_relay.py"
    spec = importlib.util.spec_from_file_location("fx_relay_monthly_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def run_relay(monkeypatch, relay):
    sent, requested = [], []
    closed = []
    client = SimpleNamespace(
        set_missing_host_key_policy=lambda policy: None,
        connect=lambda *args, **kwargs: None,
        close=lambda: closed.append(True),
    )
    monkeypatch.setitem(sys.modules, "paramiko", SimpleNamespace(
        SSHClient=lambda: client, AutoAddPolicy=lambda: None,
    ))
    monkeypatch.setenv("CRAVER_SSH_PW", "test-password")
    monkeypatch.setenv("BRIEFING_RELAY_TOKEN", "test-token")
    monkeypatch.setattr(sys, "argv", ["fx_relay.py"])
    current = ([{"currency": "USD", "krw": 1340.8644, "unit": 1}],
               "open.er-api.com", "2026-09-09")
    history = ([{"currency": "USD", "krw": 1415.95, "unit": 1}],
               "frankfurter.app", "2026-08-07")
    monkeypatch.setattr(relay, "fetch_rates", lambda: current)

    def fetch_history(day):
        requested.append(day)
        return history

    monkeypatch.setattr(relay, "fetch_history", fetch_history)

    def run(responses, args=()):
        monkeypatch.setattr(sys, "argv", ["fx_relay.py", *args])
        replies = iter(responses)

        def push(connection, token, payload):
            assert connection is client and token == "test-token"
            sent.append(payload)
            status, body = next(replies)
            return status, json.dumps(body)

        monkeypatch.setattr(relay, "push", push)
        return relay.main()

    return SimpleNamespace(run=run, sent=sent, requested=requested, closed=closed)


def test_daily_run_fills_requested_month_and_uses_actual_weekend_quote(run_relay):
    status = run_relay.run([
        (200, {"saved": 9, "for_date": "2026-09-09", "history_needed_for": "2026-08-09"}),
        (200, {"saved": 9, "for_date": "2026-08-07"}),
    ])
    assert status == 0
    assert run_relay.requested == ["2026-08-09"]
    assert [p["for_date"] for p in run_relay.sent] == ["2026-09-09", "2026-08-07"]
    assert run_relay.sent[1]["only_missing"] is True
    assert run_relay.sent[1]["source"] == "frankfurter.app"
    assert run_relay.closed == [True]


def test_complete_history_does_not_fetch_or_overwrite_old_quotes(run_relay):
    assert run_relay.run([(200, {"saved": 9, "history_needed_for": ""})]) == 0
    assert run_relay.requested == []
    assert len(run_relay.sent) == 1


def test_history_fetch_failure_is_visible_after_current_quote_saved(monkeypatch, relay, run_relay):
    monkeypatch.setattr(relay, "fetch_history", lambda day: None)
    status = run_relay.run([(200, {"saved": 9, "history_needed_for": "2026-08-09"})])
    assert status == 1
    assert len(run_relay.sent) == 1
    assert run_relay.sent[0]["for_date"] == "2026-09-09"
    assert run_relay.closed == [True]


def test_history_publish_failure_is_not_reported_as_success(run_relay):
    status = run_relay.run([
        (200, {"saved": 9, "history_needed_for": "2026-08-09"}),
        (500, {"detail": "database unavailable"}),
    ])
    assert status == 1
    assert run_relay.closed == [True]


def test_manual_backfill_never_overwrites_native_daily_quotes(run_relay):
    assert run_relay.run([(200, {"saved": 9, "history_needed_for": "2026-07-07"})],
                         args=["--date", "2026-08-09"]) == 0
    assert run_relay.requested == ["2026-08-09"]
    assert len(run_relay.sent) == 1
    assert run_relay.sent[0]["for_date"] == "2026-08-07"
    assert run_relay.sent[0]["only_missing"] is True


def test_dry_run_does_not_connect_or_write(run_relay):
    assert run_relay.run([], args=["--dry-run"]) == 0
    assert run_relay.sent == []
    assert run_relay.closed == []


def history_response():
    return {"amount": 1.0, "base": "USD", "date": "2026-08-07", "rates": {
        "KRW": 1415.95, "JPY": 158.34, "CNY": 6.7477, "EUR": 0.86693,
        "SGD": 1.2809, "PHP": 60.883, "MYR": 4.091, "IDR": 17856.5, "AUD": 1.4204,
    }}


def test_history_keeps_provider_date_and_hundred_unit(monkeypatch, relay):
    monkeypatch.setattr(relay, "_get_json", lambda url: history_response())
    rows, source, quoted = relay.fetch_history("2026-08-09")
    assert quoted == "2026-08-07"
    assert source == "frankfurter.app"
    yen = next(row for row in rows if row["currency"] == "JPY")
    assert yen == {"currency": "JPY", "krw": 894.2466, "unit": 100}


@pytest.mark.parametrize("quoted", [None, "", "2026-07-24", "2026-08-10", "invalid"])
def test_history_without_a_valid_nearby_quote_is_not_misdated(monkeypatch, relay, quoted):
    data = history_response()
    data["date"] = quoted
    monkeypatch.setattr(relay, "_get_json", lambda url: data)
    assert relay.fetch_history("2026-08-09") is None
