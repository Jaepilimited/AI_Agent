"""Monthly FX comparisons must use a nearby quote and preserve collected history."""

import asyncio
import re
import sqlite3
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request

from app.core import fx_rates


@pytest.fixture
def fx_db(monkeypatch):
    """Run the production queries; translate only MariaDB syntax and row types."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row

    def query(sql, params=()):
        sql = re.sub(r"\) ENGINE=.*", ")", sql, flags=re.DOTALL)
        sql = sql.replace("%s", "?").replace("NOW()", "CURRENT_TIMESTAMP")
        sql = sql.replace(
            "ON DUPLICATE KEY UPDATE", "ON CONFLICT(for_date, currency) DO UPDATE SET",
        )
        sql = re.sub(r"VALUES\((\w+)\)", r"excluded.\1", sql)
        return connection.execute(
            sql, tuple(value.isoformat() if isinstance(value, date) else value
                       for value in params),
        )

    def fetch_all(sql, params=()):
        rows = [dict(row) for row in query(sql, params).fetchall()]
        for row in rows:
            if row.get("d") is not None:
                row["d"] = date.fromisoformat(row["d"])
        return rows

    def fetch_one(sql, params=()):
        rows = fetch_all(sql, params)
        return rows[0] if rows else None

    monkeypatch.setattr(fx_rates, "execute", lambda sql, params=(): query(sql, params).rowcount)
    monkeypatch.setattr(fx_rates, "fetch_all", fetch_all)
    monkeypatch.setattr(fx_rates, "fetch_one", fetch_one)
    fx_rates.ensure_tables()
    yield connection
    connection.close()


def seed(connection, day, currency="USD", krw=1300, unit=1, source="live-api"):
    connection.execute(
        "INSERT INTO fx_rates (for_date, currency, krw, unit, source, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (day, currency, krw, unit, source, "2026-08-07 09:10:00"),
    )


def test_old_bootstrap_quote_cannot_become_september_monthly_comparison(fx_db):
    seed(fx_db, "2026-07-24", krw=1250)
    seed(fx_db, "2026-08-26", krw=1350)
    seed(fx_db, "2026-09-09", krw=1400)

    result = fx_rates.latest(date(2026, 9, 9))

    assert result["for_date"] == "2026-09-09"
    assert result["basis_date"] == ""
    assert result["basis_note"] == ""
    assert result["items"][0]["krw"] == 1400
    assert result["items"][0]["was_krw"] is None
    assert result["items"][0]["change_pct"] is None
    assert fx_rates.change_label(result["items"][0]) == ""


def test_weekend_target_uses_nearest_earlier_quote_within_tolerance(fx_db):
    seed(fx_db, "2026-08-06", krw=1200)
    seed(fx_db, "2026-08-07", krw=1250)
    seed(fx_db, "2026-08-10", krw=1350)
    seed(fx_db, "2026-09-09", krw=1400)

    result = fx_rates.latest(date(2026, 9, 9))

    assert result["basis_date"] == "2026-08-07"
    assert result["basis_note"] == "한 달 전 대비 (2026-08-07 고시)"
    assert result["items"][0]["was_krw"] == 1250
    assert result["items"][0]["change_pct"] == 12.0


@pytest.mark.parametrize("basis, expected", [
    ("2026-08-02", "2026-08-02"),
    ("2026-08-01", ""),
])
def test_basis_tolerance_has_an_inclusive_lower_bound(fx_db, basis, expected):
    seed(fx_db, basis)
    seed(fx_db, "2026-09-09")
    assert fx_rates.latest(date(2026, 9, 9))["basis_date"] == expected


def test_unchanged_quote_is_zero_but_absent_currency_has_no_comparison(fx_db):
    seed(fx_db, "2026-08-07", krw=1400)
    seed(fx_db, "2026-09-09", krw=1400)
    seed(fx_db, "2026-09-09", "JPY", krw=900, unit=100)

    result = fx_rates.latest(date(2026, 9, 9))
    usd, jpy = result["items"]

    assert usd["was_krw"] == 1400
    assert usd["change_pct"] == 0
    assert fx_rates.change_label(usd) == "보합"
    assert jpy["was_krw"] is None
    assert jpy["change_pct"] is None
    assert fx_rates.change_label(jpy) == ""


@pytest.mark.parametrize("current, basis", [
    ("2026-03-31", "2026-02-28"),
    ("2024-03-31", "2024-02-29"),
    ("2026-01-31", "2025-12-31"),
])
def test_month_comparison_clamps_to_calendar_month_end(fx_db, current, basis):
    seed(fx_db, basis)
    seed(fx_db, current, krw=1400)
    assert fx_rates.latest(date.fromisoformat(current))["basis_date"] == basis


def test_backfill_preserves_existing_quote_unit_source_and_fetch_time(fx_db):
    seed(fx_db, "2026-08-07", krw=1300)
    seed(fx_db, "2026-08-07", "JPY", krw=900, unit=100)

    fx_rates.put(date(2026, 8, 7), [
        {"currency": "USD", "krw": 1400, "unit": 1},
        {"currency": "JPY", "krw": 9, "unit": 1},
        {"currency": "EUR", "krw": 1600, "unit": 1},
    ], "history-api", only_missing=True)

    existing = fx_db.execute(
        "SELECT currency, krw, unit, source, fetched_at FROM fx_rates "
        "WHERE currency IN ('USD', 'JPY') ORDER BY currency",
    ).fetchall()
    assert [tuple(row) for row in existing] == [
        ("JPY", 900, 100, "live-api", "2026-08-07 09:10:00"),
        ("USD", 1300, 1, "live-api", "2026-08-07 09:10:00"),
    ]
    inserted = fx_db.execute(
        "SELECT krw, unit, source FROM fx_rates WHERE currency = 'EUR'",
    ).fetchone()
    assert tuple(inserted) == (1600, 1, "history-api")


def test_current_quote_still_updates_existing_rate(fx_db):
    seed(fx_db, "2026-09-09", "JPY", krw=9, unit=1, source="earlier-api")

    saved = fx_rates.put(date(2026, 9, 9), [
        {"currency": "JPY", "krw": 910, "unit": 100},
    ], "live-api")

    row = fx_db.execute("SELECT krw, unit, source, fetched_at FROM fx_rates").fetchone()
    assert saved == 1
    assert tuple(row)[:3] == (910, 100, "live-api")
    assert row["fetched_at"] != "2026-08-07 09:10:00"


ALL_CURRENCIES = ("USD", "JPY", "CNY", "EUR", "SGD", "PHP", "MYR", "IDR", "AUD")


@pytest.mark.parametrize("current, history_day, currencies, expected", [
    ("2026-09-09", "2026-08-09", (), "2026-08-09"),
    ("2026-09-09", "2026-08-09", ("USD",), "2026-08-09"),
    ("2026-09-09", "2026-08-09", ALL_CURRENCIES[:-1] + ("KRW",), "2026-08-09"),
    ("2026-09-09", "2026-08-09", ALL_CURRENCIES, None),
    ("2026-09-09", "2026-08-07", ALL_CURRENCIES, "2026-08-09"),
    ("2026-09-10", "2026-08-07", ALL_CURRENCIES, "2026-08-10"),
    ("2026-03-31", "2026-02-28", (), "2026-02-28"),
])
def test_history_request_requires_every_currency_on_exact_target(
    fx_db, current, history_day, currencies, expected,
):
    for currency in currencies:
        seed(fx_db, history_day, currency)

    target = fx_rates.missing_history_target(date.fromisoformat(current))

    assert (target.isoformat() if target else None) == expected


@pytest.fixture
def relay_push(monkeypatch, fx_db):
    from app.api import jandi_briefing_api as api

    monkeypatch.setattr(
        api, "get_settings", lambda: SimpleNamespace(briefing_relay_token="fx-test-token"),
    )
    request = Request({
        "type": "http", "client": ("127.0.0.1", 8000),
        "headers": [(b"x-relay-token", b"fx-test-token")],
    })
    return lambda payload: asyncio.run(api.relay_fx(request, payload))


def test_current_push_requests_missing_month_and_keeps_cleanup_boundary(relay_push, fx_db):
    seed(fx_db, "2026-06-10")
    seed(fx_db, "2026-06-11")

    response = relay_push({
        "for_date": "2026-09-09", "source": "live-api",
        "rates": [{"currency": "USD", "krw": 1400, "unit": 1}],
    })

    assert response == {
        "saved": 1, "for_date": "2026-09-09", "history_needed_for": "2026-08-09",
    }
    rows = fx_db.execute("SELECT for_date, krw FROM fx_rates ORDER BY for_date").fetchall()
    assert [tuple(row) for row in rows] == [("2026-06-11", 1300), ("2026-09-09", 1400)]


def test_current_push_does_not_request_complete_history(relay_push, fx_db):
    for currency in ALL_CURRENCIES:
        seed(fx_db, "2026-08-09", currency)

    response = relay_push({
        "for_date": "2026-09-09", "source": "live-api", "only_missing": False,
        "rates": [{"currency": "USD", "krw": 1400, "unit": 1}],
    })

    assert response == {"saved": 1, "for_date": "2026-09-09", "history_needed_for": ""}


def test_history_push_preserves_quote_and_never_requests_another_month(
    relay_push, fx_db, monkeypatch,
):
    seed(fx_db, "2026-08-07", krw=1300)

    def unexpected_history_lookup(day):
        raise AssertionError("Backfill must not trigger another historical lookup")

    monkeypatch.setattr(fx_rates, "missing_history_target", unexpected_history_lookup)
    response = relay_push({
        "for_date": "2026-08-07", "source": "history-api", "only_missing": True,
        "rates": [{"currency": "USD", "krw": 1500, "unit": 1},
                  {"currency": "JPY", "krw": 900, "unit": 100}],
    })

    assert response == {"saved": 2, "for_date": "2026-08-07", "history_needed_for": ""}
    row = fx_db.execute(
        "SELECT krw, source, fetched_at FROM fx_rates WHERE currency='USD'",
    ).fetchone()
    assert tuple(row) == (1300, "live-api", "2026-08-07 09:10:00")
    assert fx_db.execute("SELECT krw FROM fx_rates WHERE currency='JPY'").fetchone()[0] == 900


@pytest.mark.parametrize("only_missing", ["false", "true", 0, 1, None])
def test_only_missing_requires_a_boolean_before_any_write(relay_push, fx_db, only_missing):
    with pytest.raises(HTTPException) as caught:
        relay_push({
            "for_date": "2026-09-09", "source": "live-api", "only_missing": only_missing,
            "rates": [{"currency": "USD", "krw": 1400, "unit": 1}],
        })

    assert caught.value.status_code == 400
    assert fx_db.execute("SELECT COUNT(*) FROM fx_rates").fetchone()[0] == 0
