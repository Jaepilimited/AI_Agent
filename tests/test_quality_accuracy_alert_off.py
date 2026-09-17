# -*- coding: utf-8 -*-
"""정확도(👍 비율) 경고는 끈다 — 2026-09-17 사용자 결정.

이 비율은 정확도가 아니었다. 사람들은 틀렸을 때 👎 를 누르고 괜찮으면 아무것도 안 누른다:

    2026-09-16: 요청 56건 · 피드백 3건(전부 👎) · 나머지 53건 무반응 → "정확도 0%"

표본 하한도 없어 👎 한 건이면 0% 였고, 9/3 이후 피드백이 있던 6일 전부 경고가 떴다.
매일 뜨는 경고는 곧 아무도 안 읽고, 그러면 진짜 경고(속도)까지 함께 무시당한다.

⚠️ 끈 것은 **경고**뿐이다 — 값은 계속 저장한다 (`growth_report.py` 가 읽는다).
⚠️ 속도 기준 12s 는 **그대로** 둔다 (같은 날 사용자 결정).
"""

import re
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _run_snapshot(monkeypatch, *, up: int, down: int, route_ms: int = 5000):
    """DB 없이 `compute_snapshot` 을 돌려 무엇이 저장·경고되는지 본다."""
    from app.core import quality_monitor as qm

    writes: list = []

    def fake_fetch_all(sql, params=None):
        if "message_feedback" in sql:
            return [{"thumbs_up": up, "thumbs_down": down}]
        return [{"route": "direct", "request_count": 19,
                 "avg_response_ms": route_ms, "avg_context_len": 2212}]

    monkeypatch.setattr(qm, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(qm, "execute", lambda sql, params=None: writes.append((sql, params)))

    return qm.compute_daily_snapshot(), writes


def test_all_thumbs_down_no_longer_raises_an_accuracy_flag(monkeypatch):
    """⛔ 9/16 그 자체 — 👎 3 · 👍 0 이 경고가 되면 안 된다."""
    result, writes = _run_snapshot(monkeypatch, up=0, down=3)

    assert result["routes"]["_all"]["flag_accuracy"] is False
    assert not any(route == "_all" for route, _ in result["flags"]), result["flags"]

    global_write = next(p for s, p in writes if p and "_all" in p)
    # (target_date, route, accuracy, fb_count, request_count, flag_accuracy)
    assert global_write[-1] == 0, "flag_accuracy 가 1 로 저장되면 경고 막대가 읽는다"


def test_the_accuracy_value_is_still_recorded(monkeypatch):
    """끈 것은 경고뿐이다 — `growth_report.py` 가 이 값을 읽는다."""
    result, writes = _run_snapshot(monkeypatch, up=1, down=3)

    assert result["routes"]["_all"]["accuracy_rate"] == pytest.approx(0.25)
    assert result["routes"]["_all"]["feedback_count"] == 4
    global_write = next(p for s, p in writes if p and "_all" in p)
    assert global_write[2] == pytest.approx(0.25)


def test_speed_is_still_flagged_at_12s(monkeypatch):
    """⚠️ 속도 기준은 그대로다 — 정확도를 끄다 속도까지 꺼지면 안 된다."""
    result, _ = _run_snapshot(monkeypatch, up=0, down=0, route_ms=13_792)

    assert result["routes"]["direct"]["flag_speed"] is True
    assert any(route == "direct" for route, _ in result["flags"])


def test_speed_threshold_is_unchanged():
    from app.core import quality_monitor as qm

    assert qm._RESPONSE_MAX_MS == 12_000


def test_no_accuracy_threshold_survives():
    """임계 상수가 남아 있으면 다음 사람이 '되살리면 되겠네' 한다."""
    from app.core import quality_monitor as qm

    assert not hasattr(qm, "_ACCURACY_MIN")


# ── 읽는 쪽도 막는다 ─────────────────────────────────────────────────────────

def test_flags_api_does_not_select_accuracy():
    """⛔ 계산만 끄면 **이미 저장된 행**의 flag_accuracy=1 때문에 그날 하루 계속 뜬다."""
    src = (_ROOT / "app" / "api" / "admin_api.py").read_text(encoding="utf-8")
    body = src.split('@admin_router.get("/quality-flags")')[1].split("@admin_router")[0]
    sql = body.split('"""SELECT')[1].split('"""')[0]

    assert "flag_accuracy" not in sql, "경고 조회가 아직 정확도를 읽는다"
    assert "flag_speed = 1" in sql and "flag_context = 1" in sql


def test_frontend_no_longer_renders_accuracy():
    src = (_ROOT / "app" / "frontend" / "chat.js").read_text(encoding="utf-8")
    block = src.split('fetch("/api/admin/quality-flags")')[1].split(".catch(")[0]

    assert "f.flag_accuracy" not in block
    assert "기준 65%" not in block
    assert "기준 12s" in block, "속도 경고 문구까지 사라졌다"
