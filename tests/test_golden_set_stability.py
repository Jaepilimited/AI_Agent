"""Regression tests that keep golden checks stable as source data changes."""

from app.core.golden_runner import load_golden_set


def test_brand_scope_incident_checks_semantics_not_a_frozen_total() -> None:
    item = next(
        item
        for item in load_golden_set()
        if item["id"] == "inc_brand_scope_country_total"
    )
    expected = item["expect"]

    assert {"미국", "캐나다"}.issubset(expected["contains_all"])
    assert "219" not in expected["contains_all"]
    assert {"86.5", "86.6"}.issubset(expected["not_contains"])
