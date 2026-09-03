# -*- coding: utf-8 -*-
"""CSV 다운로드 링크는 재기동을 견뎌야 한다 (붐따 #160).

인메모리만이던 시절 TTL 은 1시간이라고 적혀 있었지만 **실제 수명은 「다음
배포까지」** 였다. 사용자가 링크를 누른 시각과 앱 재기동 시각이 3분 차이였고,
화면에는 원시 404 JSON 이 떴다.
"""
import datetime as dt
import importlib
from decimal import Decimal

import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    """디스크 경로를 임시 폴더로 돌린 새 모듈 인스턴스."""
    monkeypatch.setenv("SQL_RESULT_DIR", str(tmp_path / "sql_results"))
    import app.core.sql_result_store as mod
    return importlib.reload(mod)


COLS = ["country", "revenue", "d"]
ROWS = [{"country": "영국", "revenue": Decimal("1234.5"), "d": dt.date(2026, 9, 1)},
        {"country": "독일", "revenue": Decimal("99"), "d": dt.date(2026, 9, 2)}]


def _restart(mod):
    """프로세스 재기동 흉내 — 메모리만 비운다. 디스크는 그대로."""
    mod._store.clear()


def test_a_link_still_works_after_a_restart(store):
    token = store.save(7, COLS, ROWS, labels={"revenue": "매출"})
    _restart(store)
    entry = store.get(token, 7)
    assert entry is not None, "재기동 한 번에 링크가 죽으면 안 된다"
    assert [r["country"] for r in entry["rows"]] == ["영국", "독일"]
    assert entry["labels"]["revenue"] == "매출"


def test_the_csv_is_identical_before_and_after_a_restart(store):
    """⛔ 되살아나기만 하고 값이 달라지면 더 나쁘다 — 아무도 대조하지 않는다."""
    token = store.save(7, COLS, ROWS)
    before = store.to_csv_bytes(COLS, store.get(token, 7)["rows"])
    _restart(store)
    after = store.to_csv_bytes(COLS, store.get(token, 7)["rows"])
    assert before == after


def test_numbers_survive_as_numbers(store):
    """⚠️ Decimal 을 `str()` 로 굳히면 엑셀에서 문자열이 된다 — RAW 데이터로서
    계산이 안 되면 이 기능의 뜻이 없다."""
    token = store.save(7, COLS, ROWS)
    _restart(store)
    csv = store.to_csv_bytes(COLS, store.get(token, 7)["rows"]).decode("utf-8-sig")
    assert "1234.5" in csv and '"1234.5"' not in csv
    assert "2026-09-01" in csv


def test_another_user_cannot_read_it_after_a_restart(store):
    """⛔ 디스크에서 되살릴 때 소유자 확인을 빠뜨리면 방어선이 통째로 사라진다."""
    token = store.save(7, COLS, ROWS)
    _restart(store)
    assert store.get(token, 8) is None


def test_an_expired_entry_stays_dead(store):
    token = store.save(7, COLS, ROWS)
    _restart(store)
    path = store._path(token)
    import json
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["saved_at"] -= store._TTL_SECONDS + 60
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert store.get(token, 7) is None


def test_a_crafted_token_never_touches_the_filesystem(store):
    """⛔ 토큰이 파일 이름이 된다 — 형식 검사가 유일한 방어선이다."""
    for bad in ("../../etc/passwd", "a/b", "..", "", "x" * 200, "tok en"):
        assert store._path(bad) is None
        assert store.get(bad, 7) is None


def test_a_real_token_matches_the_format_check(store):
    """반대 방향 — 검사가 너무 좁으면 정상 토큰이 전부 디스크를 못 쓴다."""
    token = store.save(7, COLS, ROWS)
    assert store._path(token) is not None
    assert store._path(token).exists()


def test_a_corrupt_file_is_treated_as_missing(store):
    """부분 기록·손상 파일에 터지면 다운로드가 500 이 된다."""
    token = store.save(7, COLS, ROWS)
    _restart(store)
    store._path(token).write_text("{ this is not json", encoding="utf-8")
    assert store.get(token, 7) is None


def test_a_partial_write_is_never_visible(store):
    """⛔ 임시 파일에 쓰고 원자적으로 바꿔치기한다 — `.tmp` 가 남으면 안 된다."""
    store.save(7, COLS, ROWS)
    leftovers = list(store._DIR.glob("*.tmp"))
    assert leftovers == []


def test_the_ttl_outlives_a_working_session(store):
    """1시간이면 오전에 받은 링크가 오후에 죽는다."""
    assert store._TTL_SECONDS >= 12 * 3600


def test_results_are_never_shipped_to_the_server(store):
    """⛔ 이 파일들에는 매출·원가가 들어 있다 — 배포 전송 목록에서 뺀다."""
    with open("scripts/deploy_new_server.py", encoding="utf-8") as fh:
        src = fh.read()
    assert "data/sql_results" in src
