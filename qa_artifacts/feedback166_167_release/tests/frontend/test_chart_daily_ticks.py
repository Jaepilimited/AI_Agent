"""Feedback #166: readable daily ticks and markers, including saved charts.

Run the production renderer with the bundled Chart.js so category tick values,
stored dates, tooltips, and point hit areas are checked together.
"""

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    page = browser.new_page(viewport={"width": 1100, "height": 650})
    page.set_content("""<html><head><style>
        #message { width: 960px; }
        .chart-container { position: relative; }
        </style></head><body><div id="message"></div></body></html>""")
    page.add_script_tag(path=str(ROOT / "app/static/chart.umd.min.js"))
    source = (ROOT / "app/frontend/chat.js").read_text(encoding="utf-8")
    start = source.index("  function detectAndRenderCharts(")
    end = source.index("\n  function showWelcome()", start)
    page.add_script_tag(content=source[start:end])
    yield page
    page.close()


def _render(page, labels, *, chart_type="line"):
    # Old conversations retain these original marker settings in their JSON.
    config = {
        "type": chart_type,
        "data": {
            "labels": labels,
            "datasets": [{
                "label": "Revenue",
                "data": [1_000_000 * (i + 1) for i in range(len(labels))],
                "borderColor": "#d4845a",
                "borderWidth": 2.5,
                "pointRadius": 5,
                "pointHoverRadius": 8,
            }],
        },
        "options": {
            "animation": False,
            "responsive": True,
            "maintainAspectRatio": False,
            "plugins": {"tooltip": {}, "legend": {"display": False}},
            "scales": {"x": {}, "y": {"beginAtZero": True, "grace": "8%"}},
        },
    }
    return page.evaluate(r"""config => {
        detectAndRenderCharts(document.getElementById("message"),
            "```chart-config\n" + JSON.stringify(config) + "\n```");
        const chart = Chart.getChart(document.querySelector("#message canvas"));
        if (!chart) throw new Error("Chart did not render");
        window.testChart = chart;
        const point = chart.getDatasetMeta(0).data[0];
        chart.tooltip.setActiveElements([{datasetIndex: 0, index: 0}],
            {x: point.x, y: point.y});
        chart.update("none");
        return {
            ticks: chart.scales.x.ticks.map(tick => tick.label),
            labels: chart.data.labels,
            tooltipTitle: chart.tooltip.title,
        };
    }""", config)


@pytest.mark.parametrize("labels, expected", [
    (["2026-08-28", "2026-08-31", "2026-09-06"], ["08/28", "08/31", "09/06"]),
    (["2026-09-06"], ["09/06"]),
    (["2024-02-29", "2024-03-01"], ["02/29", "03/01"]),
])
def test_same_year_daily_ticks_keep_full_dates_in_data_and_tooltips(page, labels, expected):
    out = _render(page, labels)
    assert out["ticks"] == expected
    assert out["labels"] == labels
    assert out["tooltipTitle"] == [labels[0]]


@pytest.mark.parametrize("labels", [
    ["2025-12-31", "2026-01-01"],
    ["2025-09-06", "2026-09-06"],
    ["2026-08", "2026-09"],
    ["2026-08-28", "Total"],
    ["Product A", "Product B"],
])
def test_cross_year_and_non_daily_labels_keep_their_meaning(page, labels):
    out = _render(page, labels)
    assert out["ticks"] == labels
    assert out["labels"] == labels
    assert out["tooltipTitle"] == [labels[0]]


def test_saved_chart_points_leave_room_for_labels_and_remain_easy_to_hover(page):
    _render(page, ["2026-08-28", "2026-09-06"])
    point = page.evaluate("""() => {
        const point = testChart.getDatasetMeta(0).data[0];
        return {
            outerRadius: point.options.radius + point.options.borderWidth / 2,
            hitNearPoint: point.inRange(point.x + 7, point.y),
        };
    }""")
    # Inline numeric labels sit six pixels above the point's center.
    assert point["outerRadius"] < 6
    assert point["hitNearPoint"] is True


def test_daily_bar_ticks_are_compact_too(page):
    out = _render(page, ["2026-08-28", "2026-09-06"], chart_type="bar")
    assert out["ticks"] == ["08/28", "09/06"]
    assert out["tooltipTitle"] == ["2026-08-28"]
