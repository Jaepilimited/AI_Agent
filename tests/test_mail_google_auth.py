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
