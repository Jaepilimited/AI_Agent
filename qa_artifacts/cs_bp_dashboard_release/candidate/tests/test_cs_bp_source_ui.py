"""BP consolidation preserves source choices and exposes the correct source links."""

import json

import pytest

from tests.test_source_selection import (
    KNOWN_KEY, LOGISTICS_MIGRATION_KEY, SOURCES_KEY, _STORAGE_STUB,
    _extract_js_function, _js_source, _node, _seed,
)


CURRENT_KEYS = ["매출", "BP", "국내CS", "해외CS"]


def _migrate(selected, known, disable_bp_after=False, partial_status_first=False):
    src = _js_source()
    driver = [
        _STORAGE_STUB,
        _seed(SOURCES_KEY, selected),
        _seed(LOGISTICS_MIGRATION_KEY, "1"),
        "var _SOURCES_STORAGE_KEY = " + json.dumps(SOURCES_KEY) + ";",
        "var _KNOWN_SOURCES_KEY = " + json.dumps(KNOWN_KEY) + ";",
        "var _LOGISTICS_MIGRATION_KEY = " + json.dumps(LOGISTICS_MIGRATION_KEY) + ";",
        "var DATA_SOURCE_KEYS = [];",
    ]
    if known is not None:
        driver.append(_seed(KNOWN_KEY, known))
    for name in (
        "_mergeBpSourceKeys", "loadEnabledSources", "saveEnabledSources",
        "_hasSavedSourcePrefs", "_loadKnownSourceKeys", "_saveKnownSourceKeys",
        "_migrateLogisticsSourceDefault", "_reconcileEnabledSources", "_adoptNewSourceKeys",
    ):
        driver.append(_extract_js_function(src, name))
    driver.extend([
        "var enabledSources = loadEnabledSources();",
    ])
    if partial_status_first:
        driver.append("_saveKnownSourceKeys(['매출']);")
    driver.extend([
        "DATA_SOURCE_KEYS = " + json.dumps(CURRENT_KEYS) + ";",
        "_reconcileEnabledSources();",
        "enabledSources = enabledSources.concat(_adoptNewSourceKeys(DATA_SOURCE_KEYS, _loadKnownSourceKeys(), enabledSources));",
        "saveEnabledSources();",
    ])
    if disable_bp_after:
        driver.extend([
            "enabledSources = enabledSources.filter(function(k) { return k !== 'BP'; });",
            "saveEnabledSources();",
            "enabledSources = loadEnabledSources();",
            "_reconcileEnabledSources();",
            "enabledSources = enabledSources.concat(_adoptNewSourceKeys(DATA_SOURCE_KEYS, _loadKnownSourceKeys(), enabledSources));",
        ])
    driver.append("console.log(JSON.stringify({selected: enabledSources, saved: JSON.parse(__store[_SOURCES_STORAGE_KEY]), known: _loadKnownSourceKeys()}));")
    return _node("\n".join(driver))


@pytest.mark.parametrize("selected,expected", [
    (["CS"], ["BP"]),
    (["CS", "BP"], ["BP"]),
    (["CS Q&A", "국내CS"], ["BP", "국내CS"]),
    (["매출", "CS", "해외CS"], ["매출", "BP", "해외CS"]),
    (["국내CS"], ["국내CS"]),
    ([], []),
])
def test_old_cs_selection_moves_to_bp_without_enabling_other_sources(selected, expected):
    old_known = ["매출", "CS", "국내CS", "해외CS"]
    result = _migrate(selected, old_known)
    assert result["selected"] == result["saved"] == expected
    assert result["known"] == CURRENT_KEYS


def test_user_can_disable_bp_after_the_merge_and_it_stays_disabled():
    result = _migrate(["CS", "매출"], ["매출", "CS", "국내CS", "해외CS"], disable_bp_after=True)
    assert result["selected"] == result["saved"] == ["매출"]


def test_old_browser_without_known_source_ledger_keeps_its_filter():
    result = _migrate(["CS"], None)
    assert result["selected"] == ["BP"]
    assert result["known"] is None


def test_status_arriving_before_source_registry_does_not_reenable_disabled_sources():
    result = _migrate([], ["매출", "CS", "국내CS", "해외CS"], partial_status_first=True)
    assert result["selected"] == result["saved"] == []
    assert result["known"] == CURRENT_KEYS


def _links_html(service):
    src = _js_source()
    return _node("\n".join([
        _extract_js_function(src, "_escape"),
        _extract_js_function(src, "_statusSourceLinks"),
        "console.log(JSON.stringify(_statusSourceLinks(" + json.dumps(service) + ")));",
    ]))


def test_bp_renders_both_original_links_instead_of_replacing_them_with_dashboard():
    links = [
        {"label": "제품 Q&A", "url": "https://docs.google.com/spreadsheets/d/qa/edit"},
        {"label": "CS 제품 문서", "url": "https://www.notion.so/cs-root"},
    ]
    html = _links_html({"links": links, "url": links[0]["url"]})
    assert html.count("<a ") == 2
    assert all(link["url"] in html for link in links)
    assert "CS 제품 문서" in html and "제품 Q&amp;A" in html


def test_bp_status_puts_both_links_below_the_narrow_status_row():
    src = _js_source()
    service = {"status": "ok", "detail": "제품 Q&A 12건 · CS 문서 연결", "links": [
        {"label": "제품 Q&A", "url": "https://docs.google.com/spreadsheets/d/qa/edit"},
        {"label": "CS 문서", "url": "https://www.notion.so/cs-root"},
    ]}
    html = _node("\n".join([
        "var DATA_SOURCE_KEYS = ['BP'], enabledSources = ['BP'];",
        "var SERVICE_ICONS = {BP: {label: 'BP', svg: ''}}, issues = [], maintenanceReason = '';",
        _extract_js_function(src, "_escape"),
        _extract_js_function(src, "_statusSourceLinks"),
        _extract_js_function(src, "renderItem"),
        "console.log(JSON.stringify(renderItem('BP', " + json.dumps(service) + ")));",
    ]))
    status_row, links_row = html.split('<div class="status-source-links"', 1)
    assert "<a " not in status_row
    assert links_row.count("<a ") == 2 and "flex-wrap:wrap" in links_row


def test_cs_dashboard_link_keeps_the_root_url_and_dashboard_label():
    html = _links_html({"url": "http://34.64.99.254:8061/", "url_label": "대시보드"})
    assert 'href="http://34.64.99.254:8061/"' in html
    assert "대시보드 열기" in html and "> 대시보드</a>" in html
    assert "시트" not in html


def test_existing_sheet_links_still_render_and_link_metadata_is_escaped():
    html = _links_html({"url": "https://example.com/?a=1&b=2"})
    assert "a=1&amp;b=2" in html and "> 시트</a>" in html
    assert _links_html({"url": "javascript:alert(1)"}) == ""


def test_customer_support_group_links_to_dashboard_and_keys_still_come_from_registry():
    src = _js_source()
    start = src.index("var SOURCE_GROUPS =")
    groups = src[start:src.index("];", start) + 2]
    js = "\n".join([
        groups,
        "console.log(JSON.stringify(SOURCE_GROUPS.find(function(g) { return g.id === 'customer_support'; })));",
    ])
    group = _node(js)
    assert group["link"] == "http://34.64.99.254:8061/"
    assert group["linkLabel"] == "대시보드"
    assert group["keys"] == []


def test_picker_and_autocomplete_use_server_source_links_and_retired_cs_is_not_reinjected():
    src = _js_source()
    fill = _extract_js_function(src, "fillSourceGroups")
    assert "SOURCE_LINKS[d.key]" in fill
    assert "_statusSourceLinks(SOURCE_LINKS[key] || {})" in src
    assert "_statusSourceLinks(s)" in _extract_js_function(src, "_showDbDropdown")
    assert 'e.target.closest("a")' in _extract_js_function(src, "_showDbDropdown")
    poll = _extract_js_function(src, "pollSystemStatus")
    assert 'if (svcName === "CS") continue;' in poll
    icons = src.split("var SERVICE_ICONS =", 1)[1].split("};", 1)[0]
    assert '"CS":' not in icons and '"CS Q&A":' not in icons
    assert '"BP":' in icons


def test_cached_old_registry_cannot_restore_a_separate_cs_picker_row():
    src = _js_source()
    entries = [
        {"key": "CS", "group": "Notion", "route": "notion"},
        {"key": "BP", "group": "Notion", "route": "cs", "url": "https://example.com/qa"},
    ]
    result = _node("\n".join([
        "var SOURCE_GROUPS = [{id: 'notion', keys: []}];",
        "var GROUP_BY_NAME = {Notion: 'notion'}, SOURCE_LABELS = {}, SOURCE_LINKS = {}, SOURCE_ROUTE_MAP = {};",
        _extract_js_function(src, "fillSourceGroups"),
        "fillSourceGroups(" + json.dumps(entries) + ");",
        "console.log(JSON.stringify({keys: SOURCE_GROUPS[0].keys, links: SOURCE_LINKS}));",
    ]))
    assert result["keys"] == ["BP"]
    assert result["links"]["BP"]["url"] == "https://example.com/qa"
    assert 's.key !== "CS"' in _extract_js_function(src, "_showDbDropdown")
