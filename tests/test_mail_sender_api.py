import contextlib
import re
import shutil
import subprocess
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


# ---------------------------------------------------------------------------
# Task 7 — 발송 작업 엔드포인트
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def mem_store(monkeypatch):
    """작업 기록은 메모리 저장소로 — 테스트에서 실제 DB 를 건드리지 않는다."""
    from app.core import mail_sender_jobs as J
    s = J.MemoryStore()
    monkeypatch.setattr(J, "_store", s)
    return s


def _tpl():
    from app.core.mail_template import MailTemplate
    return MailTemplate(subject="s", html="<p>x</p>", images={})


def _cols():
    from app.core.mail_sender import Columns
    return Columns(3, 1, 6, 7, 0, {})


def _two_rows(statuses=("", "")):
    from app.core.mail_sender import Row
    return [Row(2 + i, "강하니", f"r{i}@x.com", s, {}) for i, s in enumerate(statuses)]


@contextlib.contextmanager
def _start_env(rows, load_side=None, sendas_side=None):
    cols, tpl = _cols(), _tpl()
    load = dict(side_effect=load_side) if load_side else dict(
        return_value=(rows, cols, {"강하니": tpl}, {"강하니": "t.a"}, None))
    sendas = dict(side_effect=sendas_side) if sendas_side else dict(
        return_value=[{"email": "me@x.com", "display": "", "default": True}])
    with patch("app.api.mail_sender_api._credentials", return_value="CREDS"), \
         patch("app.api.mail_sender_api.mail_google.missing_mail_scopes", return_value=[]), \
         patch("app.api.mail_sender_api.mail_google.send_as_addresses", **sendas), \
         patch("app.api.mail_sender_api._load", **load), \
         patch("app.api.mail_sender_api.jobs.start_job_thread") as th:
        yield th


def _start(client, count, sender="me@x.com"):
    return client.post("/api/mail-sender/jobs", json={"spreadsheet_id": "x", "sheet": "gmail_a",
                                                      "sender": sender, "confirm_count": count})


def test_start_job_rejects_changed_count(client):
    from app.core.mail_sender import Columns, Row
    from app.core.mail_template import MailTemplate
    tpl = MailTemplate(subject="s", html="<p>x</p>", images={})
    rows = [Row(2, "강하니", "a@x.com", "", {}), Row(3, "강하니", "b@x.com", "", {})]
    cols = Columns(3, 1, 6, 7, 0, {})
    with patch("app.api.mail_sender_api._credentials", return_value=object()), \
         patch("app.api.mail_sender_api.mail_google.missing_mail_scopes", return_value=[]), \
         patch("app.api.mail_sender_api.mail_google.send_as_addresses",
               return_value=[{"email": "me@x.com", "display": "", "default": True}]), \
         patch("app.api.mail_sender_api._load", return_value=(rows, cols, {"강하니": tpl}, {"강하니": "t.a"}, None)), \
         patch("app.api.mail_sender_api.jobs.sent_today", return_value=0), \
         patch("app.api.mail_sender_api.jobs.start_job_thread") as th:
        r = client.post("/api/mail-sender/jobs", json={"spreadsheet_id": "x", "sheet": "gmail_a",
                                                       "sender": "me@x.com", "confirm_count": 1})
    assert r.status_code == 409 and "바뀌었" in r.json()["detail"]
    th.assert_not_called()


def test_start_job_refuses_when_check_has_errors(client, mem_store):
    from app.core.mail_sender import Row
    rows = [Row(2, "강하니", "not-an-email", "", {})]
    with _start_env(rows) as th:
        r = _start(client, 1)
    assert r.status_code == 400 and "점검 오류" in r.json()["detail"]
    th.assert_not_called()
    assert mem_store.jobs == {}


def test_start_job_maps_google_errors(client, mem_store):
    from google.auth.exceptions import RefreshError
    with _start_env(_two_rows(), sendas_side=RefreshError("invalid_grant")) as th:
        r = _start(client, 2)
    assert r.status_code == 409 and "다시 연결" in r.json()["detail"]
    with _start_env(_two_rows(), load_side=RuntimeError("시트를 읽지 못했습니다")) as th2:
        r2 = _start(client, 2)
    assert r2.status_code == 502 and "시트를 읽지 못했습니다" in r2.json()["detail"]
    with _start_env(_two_rows()) as th3:
        r3 = _start(client, 2, sender="other@x.com")
    assert r3.status_code == 400 and "보내는 주소" in r3.json()["detail"]
    for t in (th, th2, th3):
        t.assert_not_called()
    assert mem_store.jobs == {}


def test_start_job_scope_missing_is_403(client):
    with patch("app.api.mail_sender_api._credentials", return_value=object()), \
         patch("app.api.mail_sender_api.mail_google.missing_mail_scopes",
               return_value=["https://www.googleapis.com/auth/gmail.send"]), \
         patch("app.api.mail_sender_api.jobs.start_job_thread") as th:
        r = _start(client, 1)
    assert r.status_code == 403
    th.assert_not_called()


def test_start_job_success_then_status_active_stop(client, mem_store):
    with _start_env(_two_rows()) as th:
        r = _start(client, 2)
    assert r.status_code == 200
    jid = r.json()["job_id"]
    args = th.call_args.args
    assert args[0] == jid and args[1] == "CREDS" and set(args[3]) == {"강하니"}
    assert [i["sheet_row"] for i in mem_store.items[jid]] == [2, 3]
    assert mem_store.jobs[jid]["sender"] == "me@x.com" and mem_store.jobs[jid]["owner"] == _User.email

    j = client.get(f"/api/mail-sender/jobs/{jid}").json()
    assert j["status"] == "running" and j["unknown_rows"] == [] and isinstance(j["created_at"], str)
    assert {"sent", "failed", "skipped", "unknown", "sheet_write_failed"} <= set(j)
    a = client.get("/api/mail-sender/jobs/active")
    assert a.status_code == 200 and a.json()["job"]["id"] == jid
    assert client.post(f"/api/mail-sender/jobs/{jid}/stop").json() == {"ok": True}
    assert client.get(f"/api/mail-sender/jobs/{jid}").json()["status"] == "stopping"
    # 같은 시트 두 번째 시작은 409
    with _start_env(_two_rows()) as th2:
        r2 = _start(client, 2)
    assert r2.status_code == 409 and "이미 발송 중" in r2.json()["detail"]
    th2.assert_not_called()


def test_jobs_active_route_is_not_shadowed(client):
    r = client.get("/api/mail-sender/jobs/active")
    assert r.status_code == 200 and r.json() == {"job": None}


def test_job_of_other_owner_is_404(client, mem_store):
    jid = mem_store.insert_job("other@x.com", "x", "gmail_a", "other@x.com", _two_rows())
    assert client.get(f"/api/mail-sender/jobs/{jid}").status_code == 404
    assert client.post(f"/api/mail-sender/jobs/{jid}/ack-unknown").status_code == 404
    assert client.post(f"/api/mail-sender/jobs/{jid}/stop").json() == {"ok": False}
    assert client.get("/api/mail-sender/jobs/999").status_code == 404


def _seed_unknown(mem_store, owner, rows=(3, 7)):
    from app.core.mail_sender import Row
    jid = mem_store.insert_job(owner, "x", "gmail_a", owner, [Row(r, "강하니", f"u{r}@x.com", "", {}) for r in rows])
    for r in rows:
        mem_store.set_item_status(jid, r, "sending")
    mem_store.interrupt_all()
    return jid


def _rows_at(*spec):
    from app.core.mail_sender import Row
    return [Row(r, "강하니", f"u{r}@x.com", s, {}) for r, s in spec]


def test_unknown_rows_block_new_job_until_ack(client, mem_store):
    jid = _seed_unknown(mem_store, _User.email)
    assert client.get(f"/api/mail-sender/jobs/{jid}").json()["unknown_rows"] == [3, 7]
    rows = _rows_at((3, ""), (7, ""))
    with _start_env(rows) as th:
        r = _start(client, 2)
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["message"] == ("발송 여부 불확실한 행이 있습니다: 3, 7행 — 보낸편지함을 확인한 뒤 "
                            "'확인했음' 을 눌러 주세요")
    assert d["unknown_job_ids"] == [jid]
    th.assert_not_called()

    a = client.post(f"/api/mail-sender/jobs/{jid}/ack-unknown")
    assert a.status_code == 200 and a.json() == {"acknowledged": 2}
    assert client.get(f"/api/mail-sender/jobs/{jid}").json()["unknown_rows"] == []
    with _start_env(rows) as th2:
        r2 = _start(client, 2)
    assert r2.status_code == 200
    th2.assert_called_once()


def test_unknown_row_already_marked_O_does_not_block(client, mem_store):
    _seed_unknown(mem_store, _User.email, rows=(3,))
    rows = _rows_at((2, ""), (3, "o"))  # 사용자가 확인하고 O 를 적었다
    with _start_env(rows):
        r = _start(client, 1)
    assert r.status_code == 200
    jid = r.json()["job_id"]
    assert [i["sheet_row"] for i in mem_store.items[jid]] == [2]  # O 행은 대상에서 빠진다


def test_unknown_rows_of_other_owner_still_block(client, mem_store):
    _seed_unknown(mem_store, "other@x.com", rows=(4,))
    with _start_env(_rows_at((4, ""))) as th:
        r = _start(client, 1)
    assert r.status_code == 409 and "4행" in r.json()["detail"]["message"]
    assert r.json()["detail"]["unknown_job_ids"] == []
    th.assert_not_called()


def test_check_uses_real_sent_today(client):
    with _start_env(_two_rows()), patch("app.api.mail_sender_api.jobs.sent_today", return_value=1999):
        r = client.post("/api/mail-sender/check", json={"spreadsheet_id": "x", "sheet": "gmail_a"})
    assert r.status_code == 200 and any("한도" in i["message"] for i in r.json()["issues"])


def test_sent_today_is_not_a_stub():
    src = (ROOT / "app/api/mail_sender_api.py").read_text(encoding="utf-8")
    assert "return await asyncio.to_thread(jobs.sent_today, email)" in src
    assert src.index('"/api/mail-sender/jobs/active"') < src.index('"/api/mail-sender/jobs/{job_id}"')


def test_lifespan_creates_mail_tables_without_blocking_startup():
    main = (ROOT / "app/main.py").read_text(encoding="utf-8")
    assert re.search(
        r"try:\s*\n\s*await asyncio\.to_thread\(ensure_mail_sender_tables\)\s*\n"
        r"\s*except Exception as e:[^\n]*\n\s*logger\.warning\(\"mail_sender_tables_failed\", error=str\(e\)\)",
        main)
    assert main.index("ensure_self_check_tables)") < main.index("ensure_mail_sender_tables)")


def test_js_job_watch_is_robust():
    js = (ROOT / "app/static/mail_sender.js").read_text(encoding="utf-8")
    assert re.search(r'\$\("ms-stop"\)\.onclick = guard\(', js)
    assert "err.status = r.status" in js
    assert re.search(r"status === 404", js) and "setTimeout(tick, 5000)" in js
    assert re.search(r'TERMINAL = \["done", "stopped", "interrupted"\]', js)
    for word in ("성공", "실패", "건너뜀", "불확실", "시트 기록 실패", "stop_reason", "unknown_rows",
                 "ack-unknown", "확인했음", "/api/mail-sender/jobs/active"):
        assert word in js, word
    assert "innerHTML" not in js
    assert 'busy($("ms-send-all")' not in js  # busy 는 finally 에서 버튼을 다시 켠다


def test_js_parses():
    node = shutil.which("node")
    if not node:
        pytest.skip("node 없음")
    r = subprocess.run([node, "--check", str(ROOT / "app/static/mail_sender.js")], capture_output=True)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
