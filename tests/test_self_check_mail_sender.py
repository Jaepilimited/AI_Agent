"""메일 발송 탭 목록 신선도 자가 점검 (`mail_sender_tab_directory`) — 네트워크 없음."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core import self_check as sc
from app.core.google_auth import CredentialLoadOutcome


def _install(monkeypatch, status="ready", values=None, raises=None):
    import app.core.google_auth as ga_mod
    import app.core.mail_google as mg

    class _Mgr:
        def load_credentials(self, email):
            return CredentialLoadOutcome(status=status, credentials=object() if status == "ready" else None)

    monkeypatch.setattr(ga_mod, "GoogleAuthManager", lambda: _Mgr())
    monkeypatch.setenv("CELLA_MAIL_TAB_DIR_SPREADSHEET_ID", "sheet-id")

    def _read(creds, sid, title):
        if raises:
            raise raises
        return values

    monkeypatch.setattr(mg, "read_sheet", _read)


def _dir(age: timedelta):
    ts = (datetime.now(timezone.utc) - age).isoformat()
    return [["탭 제목", "탭 ID", "상위", "갱신 시각"], ["홍길동", "t.1", "", ts]]


def test_check_is_registered():
    chk = {c.id: c for c in sc.CHECKS}["mail_sender_tab_directory"]
    assert chk.severity == sc.SEV_WARNING


def test_stale_directory_warns(monkeypatch):
    _install(monkeypatch, values=_dir(timedelta(hours=3)))
    r = sc._check_mail_sender_tab_directory()
    assert not r.ok
    assert "메일 발송 탭 목록 갱신 멈춤 — gmailv2 트리거 확인" in r.detail


def test_fresh_directory_ok(monkeypatch):
    _install(monkeypatch, values=_dir(timedelta(minutes=5)))
    r = sc._check_mail_sender_tab_directory()
    assert r.ok and "건너뜀" not in r.detail


def test_no_admin_token_skips_with_reason(monkeypatch):
    _install(monkeypatch, status="disconnected", values=_dir(timedelta(hours=3)))
    r = sc._check_mail_sender_tab_directory()
    assert r.ok
    assert r.detail.startswith("건너뜀") and "토큰" in r.detail


def test_unset_sheet_id_skips_with_reason(monkeypatch):
    _install(monkeypatch, values=_dir(timedelta(hours=3)))
    monkeypatch.delenv("CELLA_MAIL_TAB_DIR_SPREADSHEET_ID")
    r = sc._check_mail_sender_tab_directory()
    assert r.ok and "건너뜀" in r.detail and "CELLA_MAIL_TAB_DIR_SPREADSHEET_ID" in r.detail
