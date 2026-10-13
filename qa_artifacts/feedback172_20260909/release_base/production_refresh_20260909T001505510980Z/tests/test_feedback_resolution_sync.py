from __future__ import annotations

from datetime import date

import pytest

from app.core import feedback_inbox


def test_set_status_rejects_ascii_pipe_corruption_before_db_write(monkeypatch):
    """PowerShell의 us-ascii 파이프가 한글을 `?`로 바꾼 #151 재발을 막는다."""
    writes = []
    monkeypatch.setattr(feedback_inbox, "execute", lambda *args, **kwargs: writes.append(args))

    with pytest.raises(ValueError, match="인코딩"):
        feedback_inbox.set_status(
            151,
            "done",
            "Codex",
            "??? ?? ?? ??? ?? ?? ?? ? ??? ??? ??? ?? ????? ??????.",
            notify=False,
        )

    assert writes == []


def test_set_status_keeps_normal_korean_question_punctuation(monkeypatch):
    """정상적인 물음표 한두 개까지 오탐으로 막으면 관리자가 회신을 못 쓴다."""
    writes = []
    monkeypatch.setattr(feedback_inbox, "execute", lambda *args, **kwargs: writes.append(args) or 1)
    note = "왜 합계가 안 보였나요? 원인을 수정했고 정상 표시를 확인했습니다."

    feedback_inbox.set_status(151, "done", "admin@example.com", note, notify=False)

    assert writes and writes[0][1][2] == note


def test_deployed_resolution_sync_closes_only_exact_feedback_match():
    """Catch a fix deployment leaving its matching feedback in new/ack."""
    apply = getattr(feedback_inbox, "apply_deployed_resolutions", None)
    assert callable(apply), "deployed feedback resolutions are not synchronized"

    rows = {
        150: {"id": 150, "created_on": date(2026, 8, 25), "status": "new"},
        152: {"id": 152, "created_on": date(2026, 8, 25), "status": "new"},
    }

    def fetcher(feedback_id: int, created_on: str):
        row = rows.get(feedback_id)
        if row and str(row["created_on"]) == created_on:
            return dict(row)
        return None

    def setter(feedback_id: int, status: str, who: str, note: str):
        rows[feedback_id]["status"] = status
        rows[feedback_id]["who"] = who
        rows[feedback_id]["note"] = note

    result = apply(
        [
            {"id": 150, "created_on": "2026-08-25", "note": "fixed and verified"},
            # Wrong date must never close a possibly unrelated row with a reused ID.
            {"id": 152, "created_on": "2026-08-24", "note": "wrong row"},
        ],
        fetcher=fetcher,
        setter=setter,
    )

    assert rows[150]["status"] == "done"
    assert rows[150]["who"] == "system:deployed-resolution"
    assert rows[152]["status"] == "new"
    assert result == {"done": 1, "already_closed": 0, "missing_or_mismatched": 1}


def test_deployed_resolution_sync_is_idempotent():
    """Catch every process restart sending the same resolution again."""
    apply = getattr(feedback_inbox, "apply_deployed_resolutions", None)
    assert callable(apply), "deployed feedback resolutions are not synchronized"

    row = {"id": 105, "created_on": date(2026, 7, 8), "status": "done"}
    setter_calls = []

    result = apply(
        [{"id": 105, "created_on": "2026-07-08", "note": "fixed"}],
        fetcher=lambda feedback_id, created_on: dict(row),
        setter=lambda *args: setter_calls.append(args),
    )

    assert setter_calls == []
    assert result == {"done": 0, "already_closed": 1, "missing_or_mismatched": 0}


def test_current_resolution_manifest_closes_the_verified_feedback_backlog():
    """Catch any deployed and verified feedback item reappearing after restart."""
    apply = getattr(feedback_inbox, "apply_deployed_resolutions", None)
    manifest = getattr(feedback_inbox, "DEPLOYED_FEEDBACK_RESOLUTIONS", None)
    assert callable(apply), "deployed feedback resolutions are not synchronized"
    assert manifest is not None, "deployed feedback resolution manifest is missing"

    open_ids = {
        34, 35, 36, 37, 38, 39, 40, 42, 43, 44, 45, 46, 47, 48, 55, 57,
        66, 68, 69, 70, 75, 76, 77, 81, 82, 83, 85, 86, 88, 89, 90, 105,
        110, 112, 113, 115, 136, 150, 152, 166, 167,
    }
    rows = {feedback_id: {"id": feedback_id, "status": "ack"} for feedback_id in open_ids}
    rows[150]["status"] = "new"
    rows[152]["status"] = "new"
    manifest_dates = {item["id"]: item["created_on"] for item in manifest}

    def fetcher(feedback_id: int, created_on: str):
        row = rows.get(feedback_id)
        if row and manifest_dates.get(feedback_id) == created_on:
            return dict(row)
        return None

    def setter(feedback_id: int, status: str, who: str, note: str):
        rows[feedback_id]["status"] = status

    apply(manifest, fetcher=fetcher, setter=setter)

    assert {feedback_id for feedback_id, row in rows.items() if row["status"] in {"new", "ack"}} == set()
