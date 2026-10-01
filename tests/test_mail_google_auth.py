import json
from unittest.mock import MagicMock, patch

from app.core import google_auth, mail_google


def test_mail_scopes_not_in_default_scopes():
    assert "https://www.googleapis.com/auth/gmail.send" in google_auth.MAIL_SCOPES
    assert not set(google_auth.MAIL_SCOPES) & set(google_auth.SCOPES)


def test_auth_url_requests_extra_scopes_incrementally(monkeypatch):
    m = google_auth.GoogleAuthManager()
    flow = MagicMock()
    flow.authorization_url.return_value = ("https://accounts.google.com/x", "s")
    with patch.object(google_auth.Flow, "from_client_config", return_value=flow) as fc:
        m.get_auth_url("a@b.com", state="s", redirect_uri="http://h/cb", extra_scopes=google_auth.MAIL_SCOPES)
    assert set(google_auth.MAIL_SCOPES) <= set(fc.call_args.kwargs["scopes"])
    assert flow.authorization_url.call_args.kwargs["include_granted_scopes"] == "true"


def test_credentials_keep_stored_scopes(tmp_path, monkeypatch):
    m = google_auth.GoogleAuthManager()
    stored = google_auth.SCOPES + google_auth.MAIL_SCOPES
    p = tmp_path / "a_at_b_com.json"
    p.write_text(json.dumps({"token": "t", "refresh_token": "r", "client_id": "c", "client_secret": "s",
                             "token_uri": "https://oauth2.googleapis.com/token", "scopes": stored}))
    monkeypatch.setattr(m, "_token_path", lambda email: p, raising=False)
    with patch("google.oauth2.credentials.Credentials.refresh"):
        creds = m.get_credentials("a@b.com")
    assert set(google_auth.MAIL_SCOPES) <= set(creds.scopes)


def test_missing_mail_scopes_uses_tokeninfo():
    creds = MagicMock(token="t")
    resp = MagicMock(ok=True)
    resp.json.return_value = {"scope": "https://www.googleapis.com/auth/gmail.send openid"}
    with patch("app.core.mail_google.requests.get", return_value=resp):
        assert mail_google.missing_mail_scopes(creds) == ["https://www.googleapis.com/auth/spreadsheets"]


def test_spreadsheet_id_from_url():
    url = "https://docs.google.com/spreadsheets/d/1n08f1VIna9hZH03Zdws-31-G7RFaYJ3T7Eg7vQk636c/edit?gid=0"
    assert mail_google.spreadsheet_id_from(url) == "1n08f1VIna9hZH03Zdws-31-G7RFaYJ3T7Eg7vQk636c"


def test_export_rejects_non_zip():
    sess = MagicMock()
    sess.get.return_value = MagicMock(status_code=200, content=b"<html>login</html>")
    with patch("app.core.mail_google.AuthorizedSession", return_value=sess):
        try:
            mail_google.export_tab_zip(MagicMock(), "doc", "t.a")
        except RuntimeError as e:
            assert "zip" in str(e)
        else:
            raise AssertionError("expected RuntimeError")


# ---- Fix round 1 ----
import pytest
import requests as _requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import auth_routes
from app.api.auth_middleware import get_current_user
from app.db.models import User


def _exchange(tmp_path, monkeypatch, granted):
    from google.oauth2.credentials import Credentials
    m = google_auth.GoogleAuthManager()
    p = tmp_path / "tok.json"
    monkeypatch.setattr(m, "_token_path", lambda email: p)
    requested = google_auth.SCOPES + google_auth.MAIL_SCOPES
    flow = MagicMock()
    flow.credentials = Credentials(token="t", refresh_token="r", token_uri="https://oauth2.googleapis.com/token",
                                   client_id="c", client_secret="s", scopes=requested)
    flow.oauth2session.token = {"scope": granted}
    monkeypatch.setattr(google_auth.Flow, "from_client_config", lambda *a, **k: flow)
    monkeypatch.setattr("httpx.get", MagicMock(side_effect=RuntimeError("no net")))
    m.exchange_code("code", "a@b.com", redirect_uri="http://h/cb")
    return m, p


def test_exchange_saves_only_granted_scopes_default(tmp_path, monkeypatch):
    m, p = _exchange(tmp_path, monkeypatch, list(google_auth.SCOPES))
    saved = json.loads(p.read_text())["scopes"]
    assert set(saved) == set(google_auth.SCOPES)
    # (c) load uses exactly the saved scopes; refresh must not ask for mail scopes
    seen = {}

    def fake_refresh(self, request):
        seen["scopes"] = set(self.scopes)
    with patch("google.oauth2.credentials.Credentials.refresh", fake_refresh):
        creds = m.get_credentials("a@b.com")
    assert set(creds.scopes) == set(google_auth.SCOPES)
    assert seen["scopes"] == set(google_auth.SCOPES)


def test_exchange_saves_mail_scopes_when_granted(tmp_path, monkeypatch):
    _, p = _exchange(tmp_path, monkeypatch, google_auth.SCOPES + google_auth.MAIL_SCOPES)
    saved = set(json.loads(p.read_text())["scopes"])
    assert set(google_auth.MAIL_SCOPES) <= saved


def test_exchange_granted_as_string(tmp_path, monkeypatch):
    _, p = _exchange(tmp_path, monkeypatch, " ".join(google_auth.SCOPES))
    assert set(json.loads(p.read_text())["scopes"]) == set(google_auth.SCOPES)


def test_tokeninfo_non_ok_raises():
    creds = MagicMock(token="t")
    resp = MagicMock(ok=False, status_code=503)
    with patch("app.core.mail_google.requests.get", return_value=resp):
        with pytest.raises(RuntimeError, match="HTTP 503"):
            mail_google.granted_scopes(creds)


def test_tokeninfo_network_error_is_runtime_error():
    creds = MagicMock(token="t")
    with patch("app.core.mail_google.requests.get", side_effect=_requests.ConnectionError("x")):
        with pytest.raises(RuntimeError, match="구글 권한 확인 실패"):
            mail_google.granted_scopes(creds)


def test_tokeninfo_ok_returns_set():
    creds = MagicMock(token="t")
    resp = MagicMock(ok=True, status_code=200)
    resp.json.return_value = {"scope": "a b"}
    with patch("app.core.mail_google.requests.get", return_value=resp):
        assert mail_google.granted_scopes(creds) == {"a", "b"}


@pytest.mark.parametrize("status,content,needle", [
    (404, b"x", "찾을 수 없습니다"),
    (403, b"x", "접근 권한이 없습니다"),
    (401, b"x", "접근 권한이 없습니다"),
    (200, b"<html>", "다시 연결"),
    (500, b"x", "HTTP 500"),
])
def test_export_error_messages(status, content, needle):
    sess = MagicMock()
    sess.get.return_value = MagicMock(status_code=status, content=content)
    with patch("app.core.mail_google.AuthorizedSession", return_value=sess):
        with pytest.raises(RuntimeError, match=needle):
            mail_google.export_tab_zip(MagicMock(), "doc", "t.a")


def test_sheet_title_with_quote_is_escaped():
    svc = MagicMock()
    get = svc.spreadsheets.return_value.values.return_value.get
    get.return_value.execute.return_value = {"values": [["x"]]}
    with patch("app.core.mail_google._sheets", return_value=svc):
        mail_google.read_sheet(MagicMock(), "id", "Bob's")
        mail_google.read_cell(MagicMock(), "id", "Bob's", 2, 0)
        mail_google.write_cells(MagicMock(), "id", "Bob's", 2, {1: "v"})
    assert get.call_args_list[0].kwargs["range"] == "'Bob''s'"
    assert get.call_args_list[1].kwargs["range"] == "'Bob''s'!A2"
    bu = svc.spreadsheets.return_value.values.return_value.batchUpdate
    assert bu.call_args.kwargs["body"]["data"][0]["range"] == "'Bob''s'!B2"


class _Mgr:
    def __init__(self):
        self.kwargs = None

    def get_auth_url(self, user_email, **kw):
        self.kwargs = kw
        return "https://accounts.google.com/o/oauth2/auth?state=" + kw["state"]


def _login(monkeypatch, path):
    mgr = _Mgr()
    monkeypatch.setattr(auth_routes, "_get_auth_manager", lambda: mgr)
    monkeypatch.setattr(auth_routes, "issue_state", lambda _u, _e: "signed")
    app = FastAPI()
    app.include_router(auth_routes.auth_router)

    async def owner():
        return User(id=7, email="owner@example.com")
    app.dependency_overrides[get_current_user] = owner
    r = TestClient(app, raise_server_exceptions=False).get(path, follow_redirects=False,
                                                           headers={"Host": "10.1.100.5"})
    assert r.status_code == 307
    return mgr


def test_login_feature_mail_requests_mail_scopes(monkeypatch):
    mgr = _login(monkeypatch, "/auth/google/login?feature=mail")
    assert mgr.kwargs["extra_scopes"] == google_auth.MAIL_SCOPES


def test_login_plain_has_no_extra_scopes(monkeypatch):
    mgr = _login(monkeypatch, "/auth/google/login")
    assert "extra_scopes" not in mgr.kwargs
