# -*- coding: utf-8 -*-
"""그룹 배정 대기 알림 — 막힌 사람은 아는데 관리자만 모르던 자리. 2026-09-09.

⛔ **사용자 제보**: Entra 로 처음 로그인한 사람 화면에

       데이터 조회 그룹 배정 대기
       관리자가 데이터 조회 그룹을 배정하면 질문을 이용할 수 있습니다.

   가 떴는데 **관리자에게 가는 신호가 없었다.** 본인은 막혀 있고 관리자는 모른다.
   사용자 지시: *"이런거 나오면 나한테 알림 뜨게 해야지"* · 대상은 *"나한테만"*.
"""
import inspect
from datetime import datetime
from pathlib import Path

import pytest

from app.core import group_alerts as GA

OWNER = GA.OWNER_EMAIL
_NOW = datetime(2026, 9, 9, 9, 0, 0)


@pytest.fixture()
def db(monkeypatch):
    """대기자 2명 · 소유자 1명. 조회는 조건을 실제로 흉내 내지 않고 목록을 준다."""
    state = {
        "waiting": [
            {"id": 41, "display_name": "임규연", "email": "kyuyeon@cravercorp.com",
             "created_at": _NOW, "department": "글로벌마케팅본부"},
            {"id": 42, "display_name": "", "email": "new@cravercorp.com",
             "created_at": _NOW, "department": ""},
        ],
        "seen": [],
        "emails": {7: OWNER, 8: "someone.else@cravercorp.com"},
        "written": [],
    }
    import app.db.mariadb as mdb

    def fetch_all(sql, params=None):
        if "group_alert_seen" in sql:
            return [dict(r) for r in state["seen"]]
        if "FROM users u" in sql:
            return [dict(r) for r in state["waiting"]]
        return []

    def fetch_one(sql, params=None):
        if "SELECT email FROM users" in sql:
            return {"email": state["emails"].get(int(params[0]))}
        return None

    def execute(sql, params=None):
        state["written"].append((sql, params))
        if "INSERT INTO group_alert_seen" in sql:
            state["seen"].append({"waiting_user_id": params[0], "seen_at": params[1]})
        return 1

    monkeypatch.setattr(mdb, "fetch_all", fetch_all)
    monkeypatch.setattr(mdb, "fetch_one", fetch_one)
    monkeypatch.setattr(mdb, "execute", execute)
    return state


# ── 판정 조건이 로그인 관문과 같은가 ───────────────────────────────────────

def test_the_condition_matches_the_login_gate():
    """⛔ 갈리면 **화면은 막혔다는데 알림은 안 온다**.

    `auth_middleware` 가 사용자를 막을 때 쓰는 조건과 글자까지 같아야 한다.
    같은 규칙을 두 곳에서 따로 적으면 한쪽만 고쳐진다 — 이 저장소가 반복해 겪은 실패다.
    """
    src = (Path(__file__).resolve().parent.parent
           / "app" / "api" / "auth_middleware.py").read_text(encoding="utf-8")
    # ⚠️ 원본은 파이썬 문자열 여러 조각으로 나뉘어 있다 — 따옴표와 공백을 걷고 본다.
    #    그러지 않으면 조각 사이의 `" "` 때문에 같은 SQL 이 다르게 보인다.
    def _bare(text):
        return "".join(text.replace('"', " ").split())

    assert _bare(GA.WAITING_CONDITION) in _bare(src), \
        "로그인 관문의 조건과 달라졌다 — 둘을 함께 고쳐야 한다"


# ── 나한테만 간다 ──────────────────────────────────────────────────────────

def test_only_the_named_recipient_sees_it(db):
    """⛔ 남의 계정 이름·부서가 새면 안 된다. 역할이 아니라 **사람**으로 정한다."""
    assert [i["waiting_user_id"] for i in GA.for_user(7)] == [41, 42]
    assert GA.for_user(8) == [], "지정된 사람이 아니면 빈 목록이어야 한다"
    assert GA.for_user(0) == []


def test_a_non_recipient_cannot_mark_seen(db):
    """⚠️ 판정은 서버가 한다 — 요청이 대상을 정하지 못한다."""
    assert GA.mark_seen(8) == 0
    assert db["seen"] == []


# ── 스스로 꺼지는가 ────────────────────────────────────────────────────────

def test_it_disappears_once_the_group_is_assigned(db):
    """⛔ 해소된 뒤에도 남으면 **이미 끝난 일로 매번 알린다**.

    대기 목록을 저장하지 않고 조회할 때마다 실제 조건을 보기 때문에 저절로 꺼진다
    (물류 이상치 공시가 원본이 고쳐지면 사라지는 것과 같은 모양).
    """
    assert GA.for_user(7)
    db["waiting"].clear()                      # 관리자가 그룹을 배정했다
    assert GA.for_user(7) == []


# ── 한 번만 울리는가 ───────────────────────────────────────────────────────

def test_one_alert_per_account_not_per_poll(db):
    """⛔ 해소될 때까지 참인 조건이라, 매번 밀면 곧 아무도 안 읽는다."""
    keys = [i["dedup_key"] for i in GA.for_user(7)]
    assert keys == ["group_assign:41", "group_assign:42"]
    assert len(set(keys)) == len(keys)


def test_seen_is_remembered(db):
    assert all(not i["seen"] for i in GA.for_user(7))
    GA.mark_seen(7, 41)
    got = {i["waiting_user_id"]: i["seen"] for i in GA.for_user(7)}
    assert got[41] is True and got[42] is False


# ── 이름이 없어도 사람을 알아볼 수 있는가 ──────────────────────────────────

def test_the_title_never_comes_out_empty(db):
    """⚠️ 누가 기다리는지 모르면 알림이 할 일을 못 한다."""
    titles = {i["waiting_user_id"]: i["title"] for i in GA.for_user(7)}
    assert titles[41] == "임규연 (글로벌마케팅본부)"
    assert titles[42] == "new@cravercorp.com", "이름이 비면 메일로 대신한다"


# ── 조회가 죽어도 조용하지 않은가 ──────────────────────────────────────────

def test_a_failed_lookup_leaves_a_trace(monkeypatch):
    """⛔ 조용히 빈손을 주면 '대기자가 없다' 로 읽힌다."""
    import app.db.mariadb as mdb

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(mdb, "fetch_all", boom)
    seen = []
    monkeypatch.setattr(GA.logger, "warning", lambda *a, **k: seen.append(a))
    assert GA.waiting() == []
    assert seen, "실패를 흔적 없이 삼키면 안 된다"


# ── 배선 ───────────────────────────────────────────────────────────────────

def test_it_reaches_both_the_inbox_and_jandi():
    """사용자 결정: 알림함 + 잔디 (2026-09-09)."""
    from app.core import jandi_notify
    from app.api import notifications_api

    names = [f.__name__ for f in jandi_notify._PERSONAL_SOURCES]
    assert "_group_assignments" in names, "잔디 경로에 안 붙었다"
    src = inspect.getsource(notifications_api)
    assert "group_alerts.for_user" in src, "알림함에 안 붙었다"
    assert "group_alerts.mark_seen" in src, "읽음 처리에 안 붙었다"


def test_the_label_exists_in_both_copies():
    """⛔ 종류 표가 서버와 릴레이에 각각 있다 — 한쪽만 고치면 색·제목이 어긋난다."""
    from app.core.jandi_briefing import KIND_META, SECTION_KEYS

    relay = (Path(__file__).resolve().parent.parent
             / "scripts" / "jandi_briefing_relay.py").read_text(encoding="utf-8")
    assert GA.KIND in KIND_META
    assert f'"{GA.KIND}"' in relay, "릴레이 목록에 없다 — 색·제목이 어긋난다"
    assert KIND_META[GA.KIND][0] in relay, "라벨 문구가 서버와 다르다"
    # 끌 수 있어야 한다 — 못 끄는 알림은 결국 전체 알림을 무시하게 만든다
    assert GA.KIND in SECTION_KEYS


def test_jandi_renders_it_without_a_special_case():
    """⚠️ 기본 분기가 제목·설명을 그린다 — 종류마다 코드를 늘리지 않는다."""
    from app.core import jandi_notify

    title, body = jandi_notify.render({
        "kind": GA.KIND, "title": "임규연 (글로벌마케팅본부)",
        "note": "데이터 조회 그룹을 배정해야 질문을 이용할 수 있습니다.",
    })
    assert title == "임규연 (글로벌마케팅본부)"
    assert "임규연" in body and "배정" in body
