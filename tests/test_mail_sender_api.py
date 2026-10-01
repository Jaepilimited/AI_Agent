import re
from email import message_from_bytes
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.test_coa_finder_api import _User, client  # noqa: F401  (같은 로그인 픽스처 재사용)

ROOT = Path(__file__).resolve().parents[1]


def test_wiring():
    main = (ROOT / "app/main.py").read_text(encoding="utf-8")
    assert "include_router(mail_sender_router)" in main
    mw = (ROOT / "app/api/middleware.py").read_text(encoding="utf-8")
    assert '"/mail-sender"' in mw
    html = (ROOT / "app/frontend/chat.html").read_text(encoding="utf-8")
    assert 'id="btn-mail-sender"' in html
    js = (ROOT / "app/frontend/chat.js").read_text(encoding="utf-8")
    assert re.search(r'getElementById\("btn-mail-sender"\)[\s\S]{0,200}"/mail-sender"', js)


def test_page_redirects_without_session(client):
    client.cookies.clear()
    r = client.get("/mail-sender", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/login"


def test_status_not_connected(client):
    with patch("app.api.mail_sender_api._credentials", return_value=None):
        r = client.get("/api/mail-sender/status")
    assert r.status_code == 200 and r.json()["connected"] is False


def test_status_scope_check_failure_is_reported_not_reconnect(client):
    with patch("app.api.mail_sender_api._credentials", return_value=object()), \
         patch("app.api.mail_sender_api.mail_google.missing_mail_scopes",
               side_effect=RuntimeError("구글 권한 확인 실패 (일시 오류)")):
        r = client.get("/api/mail-sender/status")
    j = r.json()
    assert r.status_code == 200 and j["connected"] is True and j["missing_scopes"] == []
    assert "권한 확인 실패" in j["error"]


def test_check_blocks_missing_scopes(client):
    with patch("app.api.mail_sender_api._credentials", return_value=object()), \
         patch("app.api.mail_sender_api.mail_google.missing_mail_scopes",
               return_value=["https://www.googleapis.com/auth/gmail.send"]):
        r = client.post("/api/mail-sender/check", json={"spreadsheet_id": "x", "sheet": "gmail_a"})
    assert r.status_code == 403
    assert "gmail.send" in str(r.json()["detail"])


def test_scope_check_failure_is_503_not_403(client):
    with patch("app.api.mail_sender_api._credentials", return_value=object()), \
         patch("app.api.mail_sender_api.mail_google.missing_mail_scopes",
               side_effect=RuntimeError("구글 권한 확인 실패 (일시 오류)")):
        r = client.post("/api/mail-sender/check", json={"spreadsheet_id": "x", "sheet": "gmail_a"})
    assert r.status_code == 503
    assert "권한 확인 실패" in r.json()["detail"]


def test_google_failure_becomes_readable_502(client):
    with patch("app.api.mail_sender_api._credentials", return_value=object()), \
         patch("app.api.mail_sender_api.mail_google.missing_mail_scopes", return_value=[]), \
         patch("app.api.mail_sender_api.mail_google.sheet_titles",
               side_effect=RuntimeError("시트를 읽지 못했습니다")):
        r = client.post("/api/mail-sender/sheets", json={"spreadsheet": "abc"})
    assert r.status_code == 502
    assert "시트를 읽지 못했습니다" in r.json()["detail"]


def test_test_send_goes_only_to_self(client):
    from app.core.mail_template import MailTemplate
    tpl = MailTemplate(subject="[S] {kol id}", html="<p>{kol id}</p>", images={})
    rows_cols = ([__import__("app.core.mail_sender", fromlist=["Row"]).Row(2, "강하니", "kol@x.com", "", {"kol id": "k"})],
                 None, {"강하니": tpl}, {"강하니": "t.a"}, None)
    sent = {}
    with patch("app.api.mail_sender_api._credentials", return_value=object()), \
         patch("app.api.mail_sender_api.mail_google.missing_mail_scopes", return_value=[]), \
         patch("app.api.mail_sender_api.mail_google.send_as_addresses",
               return_value=[{"email": "me@skin1004korea.com", "display": "", "default": True}]), \
         patch("app.api.mail_sender_api._load", return_value=rows_cols), \
         patch("app.api.mail_sender_api.mail_google.send_raw",
               side_effect=lambda c, raw: sent.setdefault("raw", raw) and "id1"):
        r = client.post("/api/mail-sender/test-send",
                        json={"spreadsheet_id": "x", "sheet": "gmail_a", "row": 2, "sender": "me@skin1004korea.com"})
    assert r.status_code == 200
    assert b"kol@x.com" not in sent["raw"]
    assert message_from_bytes(sent["raw"])["To"] == _User.email


def test_sender_not_in_sendas_is_rejected(client):
    with patch("app.api.mail_sender_api._credentials", return_value=object()), \
         patch("app.api.mail_sender_api.mail_google.missing_mail_scopes", return_value=[]), \
         patch("app.api.mail_sender_api.mail_google.send_as_addresses",
               return_value=[{"email": "me@skin1004korea.com", "display": "", "default": True}]):
        r = client.post("/api/mail-sender/test-send",
                        json={"spreadsheet_id": "x", "sheet": "gmail_a", "row": 2, "sender": "marketing@x.com"})
    assert r.status_code == 400 and "보내는 주소" in r.json()["detail"]


def test_preview_iframe_is_sandboxed():
    js = (ROOT / "app/static/mail_sender.js").read_text(encoding="utf-8")
    assert re.search(r'setAttribute\("sandbox",\s*"[^"]*"\)', js)
    assert js.index('setAttribute("sandbox"') < js.index("srcdoc")
    assert "allow-scripts" not in js and "allow-same-origin" not in js


def test_js_handlers_are_guarded():
    js = (ROOT / "app/static/mail_sender.js").read_text(encoding="utf-8")
    for h in ("ms-load", "ms-check", "ms-send-all"):
        assert re.search(rf'\$\("{h}"\)\.onclick = guard\(', js), h
    assert "guard(() => preview(" in js and "guard(() => busy(ts, () => testSend(" in js


def _refresh_error():
    from google.auth.exceptions import RefreshError
    return RefreshError("invalid_grant")


def test_status_refresh_error_means_reconnect(client):
    with patch("app.api.mail_sender_api._credentials", return_value=object()), \
         patch("app.api.mail_sender_api.mail_google.missing_mail_scopes", side_effect=_refresh_error()):
        r = client.get("/api/mail-sender/status")
    j = r.json()
    assert r.status_code == 200 and j["connected"] is False and "다시 연결" in j["error"]


def test_check_refresh_error_is_409(client):
    with patch("app.api.mail_sender_api._credentials", return_value=object()), \
         patch("app.api.mail_sender_api.mail_google.missing_mail_scopes", side_effect=_refresh_error()):
        r = client.post("/api/mail-sender/check", json={"spreadsheet_id": "x", "sheet": "gmail_a"})
    assert r.status_code == 409 and "다시 연결" in r.json()["detail"]


def test_refresh_error_inside_route_is_409(client):
    with patch("app.api.mail_sender_api._credentials", return_value=object()), \
         patch("app.api.mail_sender_api.mail_google.missing_mail_scopes", return_value=[]), \
         patch("app.api.mail_sender_api.mail_google.sheet_titles", side_effect=_refresh_error()):
        r = client.post("/api/mail-sender/sheets", json={"spreadsheet": "abc"})
    assert r.status_code == 409 and "다시 연결" in r.json()["detail"]


def test_one_tab_timeout_is_that_managers_error_not_502(client):
    import requests
    header = ["Email", "담당자", "전송여부", "{kol id}"]
    sheet = [header, ["a@x.com", "강하니", "", "k1"]]
    tabs = [["담당자", "탭", "", ""], ["강하니", "t.a", "", ""]]
    with patch("app.api.mail_sender_api._credentials", return_value=object()), \
         patch("app.api.mail_sender_api.mail_google.missing_mail_scopes", return_value=[]), \
         patch("app.api.mail_sender_api.mail_google.read_sheet", side_effect=[sheet, tabs]), \
         patch("app.api.mail_sender_api.mail_google.export_tab_zip", side_effect=requests.Timeout("timeout")):
        r = client.post("/api/mail-sender/check", json={"spreadsheet_id": "x", "sheet": "gmail_a"})
    assert r.status_code == 200
    j = r.json()
    assert j["errors"] >= 1 and any(i["level"] == "error" and i["row"] == 2 for i in j["issues"])
