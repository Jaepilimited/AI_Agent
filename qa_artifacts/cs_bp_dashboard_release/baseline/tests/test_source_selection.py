# -*- coding: utf-8 -*-
"""SYSTEM STATUS 의 `전체 해제` 는 **눌린 대로 남아 있어야 한다**.

2026-09-03 사용자 제보: *"SYSTEM STATUS에서 전체선택 해제가 안되는데.."*

원인은 한 줄이 아니라 **"빈 선택" 을 "고르지 않음" 으로 읽는 자리가 둘**이었다.

1. `pollSystemStatus()` 가 매 폴링(30초)마다 발견한 Notion 팀 키를
   `enabledSources` 에 **무조건 되집어넣었다.** `전체 해제` 핸들러가 스스로
   `pollSystemStatus()` 를 부르므로, 누른 그 자리에서 되살아난다
2. `_reconcileEnabledSources()` 가 *비었으면 전체* 로 되돌렸다 — 새로고침하면
   꺼 둔 것이 전부 다시 켜진다

⛔ 둘 다 **에러가 아니다.** 체크박스가 조용히 다시 켜질 뿐이라, 사용자가
   눌러 보기 전까지 아무도 모른다.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHAT_JS = ROOT / "app/frontend/chat.js"

SOURCES_KEY = "skin1004_enabled_sources"
KNOWN_KEY = "skin1004_known_sources"
LOGISTICS_MIGRATION_KEY = "skin1004_logistics_source_default_v1"


def _js_source():
    return CHAT_JS.read_text(encoding="utf-8")


def _extract_js_function(src, name):
    """`function <name>(` 부터 짝이 맞는 닫는 중괄호까지 떼어낸다.

    ⚠️ 대상 함수 안에 중괄호가 든 문자열 리터럴을 두지 마라 — 세는 것이 어긋난다.
    """
    marker = "function " + name + "("
    assert marker in src, "chat.js 에 " + name + " 이(가) 없다"
    start = src.index(marker)
    depth, i = 0, src.index("{", start)
    while True:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1


_STORAGE_STUB = """
var __store = {};
var localStorage = {
  getItem: function (k) { return Object.prototype.hasOwnProperty.call(__store, k) ? __store[k] : null; },
  setItem: function (k, v) { __store[k] = String(v); },
  removeItem: function (k) { delete __store[k]; }
};
"""


def _node(driver):
    node = shutil.which("node")
    if not node:
        pytest.skip("node 없음 — 개발 환경 전용 검사")
    r = subprocess.run([node, "-e", driver], capture_output=True, text=True,
                       timeout=20, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _seed(key, value):
    return "__store[" + json.dumps(key) + "] = " + json.dumps(json.dumps(value)) + ";\n"


def _run_reconcile(keys, selected, stored, known=None, logistics_migrated=False):
    """`_reconcileEnabledSources` 를 node 로 **실제 실행**한다."""
    js = _js_source()
    seed = ""
    if stored is not None:
        seed += _seed(SOURCES_KEY, stored)
    if known is not None:
        seed += _seed(KNOWN_KEY, known)
    if logistics_migrated:
        seed += _seed(LOGISTICS_MIGRATION_KEY, "1")
    driver = "\n".join([
        _STORAGE_STUB,
        seed,
        "var _SOURCES_STORAGE_KEY = " + json.dumps(SOURCES_KEY) + ";",
        "var _KNOWN_SOURCES_KEY = " + json.dumps(KNOWN_KEY) + ";",
        "var _LOGISTICS_MIGRATION_KEY = " + json.dumps(LOGISTICS_MIGRATION_KEY) + ";",
        "var DATA_SOURCE_KEYS = " + json.dumps(keys) + ";",
        "var enabledSources = " + json.dumps(selected) + ";",
        _extract_js_function(js, "saveEnabledSources"),
        _extract_js_function(js, "_hasSavedSourcePrefs"),
        _extract_js_function(js, "_loadKnownSourceKeys"),
        _extract_js_function(js, "_saveKnownSourceKeys"),
        _extract_js_function(js, "_migrateLogisticsSourceDefault"),
        _extract_js_function(js, "_reconcileEnabledSources"),
        "_reconcileEnabledSources();",
        "console.log(JSON.stringify(enabledSources));",
    ])
    return _node(driver)


def _run_adopt(all_keys, known, selected):
    """`_adoptNewSourceKeys` 를 node 로 **실제 실행**한다."""
    src = _js_source()
    driver = (
        _extract_js_function(src, "_adoptNewSourceKeys") + "\n"
        + "console.log(JSON.stringify(_adoptNewSourceKeys("
        + json.dumps(all_keys) + ", "
        + ("null" if known is None else json.dumps(known)) + ", "
        + json.dumps(selected) + ")));\n"
    )
    return _node(driver)


def _run_logistics_migration(keys, selected, migrated=False, remove_then_rerun=False):
    """기존 브라우저의 물류 기본 선택 마이그레이션을 실제 JS로 실행한다."""
    src = _js_source()
    seed = _seed(LOGISTICS_MIGRATION_KEY, "1") if migrated else ""
    steps = [
        _STORAGE_STUB,
        seed,
        "var _LOGISTICS_MIGRATION_KEY = " + json.dumps(LOGISTICS_MIGRATION_KEY) + ";",
        "var DATA_SOURCE_KEYS = " + json.dumps(keys) + ";",
        "var enabledSources = " + json.dumps(selected) + ";",
        _extract_js_function(src, "_migrateLogisticsSourceDefault"),
        "_migrateLogisticsSourceDefault();",
    ]
    if remove_then_rerun:
        steps.extend([
            'enabledSources = enabledSources.filter(function(k) { return k !== "물류"; });',
            "_migrateLogisticsSourceDefault();",
        ])
    steps.append("console.log(JSON.stringify(enabledSources));")
    return _node("\n".join(steps))


# ── 신규 물류 소스의 일회성 기본 선택 보정 ──────────────────────

def test_previous_all_selection_adopts_the_new_logistics_source_once():
    """기존 소스를 전부 쓰던 브라우저에서 새 물류만 빠진 상태를 복구한다."""
    got = _run_logistics_migration(
        ["매출", "제품", "물류"], ["매출", "제품"])
    assert got == ["매출", "제품", "물류"]


def test_logistics_migration_respects_explicit_deselect_all():
    got = _run_logistics_migration(["매출", "제품", "물류"], [])
    assert got == []


def test_logistics_migration_preserves_a_partial_filter():
    got = _run_logistics_migration(
        ["매출", "제품", "물류"], ["매출"])
    assert got == ["매출"]


def test_user_can_turn_logistics_off_after_the_migration():
    got = _run_logistics_migration(
        ["매출", "제품", "물류"], ["매출", "제품"],
        remove_then_rerun=True,
    )
    assert got == ["매출", "제품"]


# ── 저장된 빈 선택은 "전부 끄기" 다 ──────────────────────────────

def test_deselect_all_survives_a_reload():
    """⛔ 저장된 `[]` 는 사용자의 결정이다. 새로고침이 그것을 되돌리면 안 된다."""
    got = _run_reconcile(["매출", "물류", "OP"], [], stored=[])
    assert got == []


def test_a_partial_selection_survives_a_reload():
    got = _run_reconcile(["매출", "물류", "OP"], ["매출"], stored=["매출"])
    assert got == ["매출"]


def test_first_login_turns_everything_on():
    """저장된 것이 **없으면** 전체 선택이 기본값이다 (이건 그대로 지킨다)."""
    got = _run_reconcile(["매출", "물류", "OP"], [], stored=None)
    assert got == ["매출", "물류", "OP"]


def test_a_selection_that_went_completely_stale_falls_back_to_everything():
    """저장분이 통째로 낡아 비게 된 것은 '전부 끄기' 가 아니다 — 전체로 되돌린다."""
    got = _run_reconcile(["매출", "물류"], ["없어진소스"], stored=["없어진소스"],
                         known=["매출", "물류"])
    assert got == ["매출", "물류"]


def test_an_upgrading_browser_keeps_old_selection_and_enables_new_logistics():
    """⛔ 대장이 없던 시절부터 쓰던 사람 — 판단 근거가 없으니 **아무것도 버리지 않는다.**
    버리면 이미 골라 둔 Notion 팀이 배포 직후 한 번 통째로 사라진다.
    기존 목록을 전부 쓰던 사람에게는 이번에 추가된 물류만 기본으로 보탠다."""
    got = _run_reconcile(["매출", "물류"], ["매출", "CBT"],
                         stored=["매출", "CBT"], known=None)
    assert got == ["매출", "CBT", "물류"]


def test_a_first_time_browser_opens_the_ledger_empty():
    """⛔ 이 대장이 없으면 뒤늦게 오는 Notion 팀이 **첫 방문자에게 전부 꺼진 채로** 보인다."""
    src = _js_source()
    fn = _extract_js_function(src, "_reconcileEnabledSources")
    assert "_saveKnownSourceKeys([])" in fn


def test_a_team_source_that_has_not_arrived_yet_is_not_treated_as_gone():
    """⛔ Notion 팀 키는 상태 응답이 도착해야 목록에 생긴다. 그전에 잘라내면
    사용자가 골라 둔 팀이 새로고침마다 지워진다 (대장에 있으면 남긴다)."""
    got = _run_reconcile(["매출", "물류"], ["매출", "CBT"], stored=["매출", "CBT"],
                         known=["매출", "물류", "CBT"], logistics_migrated=True)
    assert got == ["매출", "CBT"]


def test_a_source_that_really_disappeared_is_dropped():
    got = _run_reconcile(["매출", "물류"], ["매출", "없어진팀"],
                         stored=["매출", "없어진팀"], known=["매출", "물류"],
                         logistics_migrated=True)
    assert got == ["매출"]


# ── 폴링이 꺼 둔 것을 되살리지 않는다 ────────────────────────────

def test_polling_never_re_enables_what_the_user_turned_off():
    """⛔ 이것이 제보된 증상 그 자체다 — 30초마다, 그리고 누른 그 자리에서 되살아났다."""
    known = ["매출", "CBT", "JBT"]
    assert _run_adopt(known, known, []) == []


def test_a_genuinely_new_source_arrives_enabled():
    """새로 생긴 소스는 켜져서 온다 — 안 그러면 아무도 모르게 빠진다."""
    got = _run_adopt(["매출", "CBT", "신규팀"], ["매출", "CBT"], ["매출", "CBT"])
    assert got == ["신규팀"]


def test_the_very_first_ledger_adopts_nothing():
    """⛔ 목록을 처음 보는 순간(배포 직후)에 전부 '새것' 으로 세면,
    사용자가 꺼 둔 것이 한 번 통째로 되살아난다."""
    assert _run_adopt(["매출", "CBT"], None, []) == []


# ── 배선 (문자열 검사로만 지킬 수 있는 것) ───────────────────────

def test_the_poll_adopts_through_the_ledger_not_a_raw_push():
    """⛔ `pollSystemStatus` 안에서 팀 키를 맨손으로 push 하면 원래 사고가 되살아난다."""
    src = _js_source()
    block = src.split("function pollSystemStatus(")[1].split("// Toolbar")[0]
    assert "_adoptNewSourceKeys" in block
    assert "enabledSources.indexOf(k) < 0) enabledSources.push(k)" not in block


def test_storage_keys_are_named_once():
    src = _js_source()
    assert '_SOURCES_STORAGE_KEY = "' + SOURCES_KEY + '"' in src
    assert '_KNOWN_SOURCES_KEY = "' + KNOWN_KEY + '"' in src


def test_loading_does_not_require_a_non_empty_selection():
    """⛔ `parsed.length > 0` 은 저장된 '전부 끄기' 를 버린다."""
    fn = _extract_js_function(_js_source(), "loadEnabledSources")
    assert "parsed.length > 0" not in fn


# ── 고른 것이 실제 질문에 닿는다 ─────────────────────────────────
#
# ⛔ 2026-09-03 이전까지 이 체크박스는 **화면 표시 전용**이었다. `_sendSources` 가
#    `@@`·슬래시 지정이 없으면 무조건 `null` 이라, 화면은 `0/N 소스 활성` 이라고
#    말하는데 답변은 전부 조회했다. 배지가 거짓말을 하는 상태가 가장 나쁘다.

def _run_send(at_at, slash, selected, all_keys):
    src = _js_source()
    driver = "\n".join([
        _extract_js_function(src, "_sourcesForSend"),
        "console.log(JSON.stringify(_sourcesForSend("
        + json.dumps(at_at) + ", "
        + ("null" if slash is None else json.dumps(slash)) + ", "
        + json.dumps(selected) + ", " + json.dumps(all_keys) + ")));",
    ])
    return _node(driver)


def test_deselect_all_reaches_the_server_as_an_empty_list():
    """⛔ `[]` 를 `null` 로 뭉개면 서버는 기본값(BQ+GWS+Direct)을 쓴다 —
    화면은 껐다고 말하는데 답변은 조회한다. 서버는 빈 목록을 `직접 대화만` 으로 읽는다."""
    assert _run_send([], None, [], ["매출", "물류", "OP"]) == []


def test_everything_on_stays_on_the_server_default():
    """⛔ 전부 켜졌다고 통째로 보내면 notion·cs 경로가 기본으로 열려
    **손대지 않은 사람의 라우팅까지** 바뀐다."""
    keys = ["매출", "물류", "OP"]
    assert _run_send([], None, keys[:], keys) is None


def test_a_narrowed_selection_is_sent():
    assert _run_send([], None, ["매출"], ["매출", "물류", "OP"]) == ["매출"]


def test_at_at_and_slash_still_win():
    keys = ["매출", "물류"]
    assert _run_send(["물류"], None, [], keys) == ["물류"]
    assert _run_send([], ["보고서"], [], keys) == ["보고서"]


def test_an_unloaded_source_list_falls_back_to_the_server_default():
    """⚠️ 목록이 아직 안 왔으면 좁힌 것인지 알 수 없다 — 지어내지 않는다."""
    assert _run_send([], None, [], []) is None


def test_the_send_path_goes_through_the_decision_function():
    src = _js_source()
    block = src.split("var _sendSources")[1][:400]
    assert "_sourcesForSend(" in block
