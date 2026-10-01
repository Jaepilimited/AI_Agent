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
         patch("app.api.mail_sender_api.mail_google.sheet_gids",
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
    for h in ("ms-check", "ms-send-all"):
        assert re.search(rf'\$\("{h}"\)\.onclick = guard\(', js), h
    assert "const loadSheets = guard(" in js and '$("ms-book").onchange = loadSheets' in js
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
         patch("app.api.mail_sender_api.mail_google.sheet_gids", side_effect=_refresh_error()):
        r = client.post("/api/mail-sender/sheets", json={"spreadsheet": "abc"})
    assert r.status_code == 409 and "다시 연결" in r.json()["detail"]


def test_one_tab_timeout_is_that_managers_error_not_502(client):
    import requests
    header = ["Date", "담당자", "{kol id}", "Email", "", "", "전송여부"]
    sheet = [header, ["", "강하니", "k1", "a@x.com", "", "", ""]]
    tabs = [["담당자", "탭", "", ""], ["강하니", "t.a", "", ""]]
    with patch("app.api.mail_sender_api._credentials", return_value=object()), \
         patch("app.api.mail_sender_api.mail_google.missing_mail_scopes", return_value=[]), \
         patch("app.api.mail_sender_api.mail_google.read_sheet", side_effect=[sheet, tabs]), \
         patch("app.api.mail_sender_api.mail_google.read_template_cache_index", return_value={}), \
         patch("app.api.mail_sender_api.mail_google.cached_tab_zip", side_effect=requests.Timeout("timeout")):
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


def _check(client):
    return client.post("/api/mail-sender/check", json={"spreadsheet_id": "x", "sheet": "gmail_a"})


def _seed_sent(mem_store, owner, row):
    """앞선 작업이 보냈지만 시트에 O 를 못 남긴 행 (DB 에만 sent)."""
    from app.core.mail_sender import Row
    jid = mem_store.insert_job(owner, "x", "gmail_a", owner, [Row(row, "강하니", f"u{row}@x.com", "", {})])
    mem_store.set_item_status(jid, row, "sent", message_id="m")
    mem_store.set_job(jid, status="done")
    return jid


def test_unknown_rows_are_excluded_until_ack(client, mem_store):
    jid = _seed_unknown(mem_store, _User.email)
    assert client.get(f"/api/mail-sender/jobs/{jid}").json()["unknown_rows"] == [3, 7]
    rows = _rows_at((3, ""), (7, ""), (8, ""))
    with _start_env(rows):
        c = _check(client).json()
    assert c["pending"] == 1 and c["errors"] == 0
    summary = [i["message"] for i in c["issues"] if i["row"] is None and "불확실" in i["message"]]
    assert summary and "발송 여부 불확실 2행(3, 7)" in summary[0] and "확인했음" in summary[0]
    assert {i["row"] for i in c["issues"] if i["row"] is not None and i["level"] == "warn"} == {3, 7}
    with _start_env(rows) as th:
        r = _start(client, 3)  # 제외 전 숫자로는 시작하지 않는다
    assert r.status_code == 409 and "바뀌었" in r.json()["detail"]
    th.assert_not_called()

    a = client.post(f"/api/mail-sender/jobs/{jid}/ack-unknown")
    assert a.status_code == 200 and a.json() == {"acknowledged": 2}
    assert client.get(f"/api/mail-sender/jobs/{jid}").json()["unknown_rows"] == []
    with _start_env(rows):
        assert _check(client).json()["pending"] == 3
    with _start_env(rows) as th2:
        r2 = _start(client, 3)
    assert r2.status_code == 200
    th2.assert_called_once()
    assert [i["sheet_row"] for i in mem_store.items[r2.json()["job_id"]]] == [3, 7, 8]


def test_sent_in_db_with_empty_G_is_excluded_for_colleague(client, mem_store):
    _seed_sent(mem_store, "other@x.com", 4)  # 동료가 보냈고 O 기록은 실패했다
    rows = _rows_at((4, ""), (5, ""))
    with _start_env(rows):
        c = _check(client).json()
    assert c["pending"] == 1
    assert any(i["row"] is None and "발송 기록 있음 1행(4) — 다시 보내지 않습니다" in i["message"] for i in c["issues"])
    with _start_env(rows) as th:
        r = _start(client, 1)
    assert r.status_code == 200
    th.assert_called_once()
    assert [i["sheet_row"] for i in mem_store.items[r.json()["job_id"]]] == [5]


def test_blocked_row_appearing_between_check_and_start_is_409(client, mem_store):
    from app.core import mail_sender_jobs as J
    real = J.create_job

    def late(*a, **kw):  # 경로가 대상을 계산한 뒤, 잠금 안 재판단 전에 불확실 행이 생긴다
        _seed_unknown(mem_store, "other@x.com", rows=(3,))
        return real(*a, **kw)
    with _start_env(_two_rows()) as th, patch("app.api.mail_sender_api.jobs.create_job", side_effect=late):
        r = _start(client, 2)
    assert r.status_code == 409 and "대상이 바뀌었습니다" in r.json()["detail"]
    th.assert_not_called()
    assert all(j["owner"] == "other@x.com" for j in mem_store.jobs.values())


@pytest.mark.parametrize("route", ["check", "start", "status", "active", "ack", "stop"])
def test_db_errors_are_readable_503(client, mem_store, route):
    import pymysql
    boom = pymysql.err.OperationalError(2003, "Can't connect")
    target = {"check": "sent_today", "start": "sheet_history", "status": "get_job", "active": "active_job",
              "ack": "ack_unknown", "stop": "request_stop"}[route]
    with _start_env(_two_rows()), patch(f"app.api.mail_sender_api.jobs.{target}", side_effect=boom):
        r = {"check": lambda: _check(client), "start": lambda: _start(client, 2),
             "status": lambda: client.get("/api/mail-sender/jobs/1"),
             "active": lambda: client.get("/api/mail-sender/jobs/active"),
             "ack": lambda: client.post("/api/mail-sender/jobs/1/ack-unknown"),
             "stop": lambda: client.post("/api/mail-sender/jobs/1/stop")}[route]()
    assert r.status_code == 503
    assert r.json()["detail"] == "발송 기록 DB 에 연결하지 못했습니다 — 잠시 뒤 다시 시도"


def test_unknown_row_already_marked_O_does_not_block(client, mem_store):
    _seed_unknown(mem_store, _User.email, rows=(3,))
    rows = _rows_at((2, ""), (3, "o"))  # 사용자가 확인하고 O 를 적었다
    with _start_env(rows):
        r = _start(client, 1)
    assert r.status_code == 200
    jid = r.json()["job_id"]
    assert [i["sheet_row"] for i in mem_store.items[jid]] == [2]  # O 행은 대상에서 빠진다


def test_unknown_rows_of_other_owner_are_excluded_not_409(client, mem_store):
    _seed_unknown(mem_store, "other@x.com", rows=(4,))
    with _start_env(_rows_at((4, ""), (5, ""))) as th:
        r = _start(client, 1)
    assert r.status_code == 200
    th.assert_called_once()
    assert [i["sheet_row"] for i in mem_store.items[r.json()["job_id"]]] == [5]


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


# ---------------------------------------------------------------------------
# Fix Round 2 (after redo) — 행·이메일 기준 발송 기록, 중단된 작업의 '확인했음'
# ---------------------------------------------------------------------------
def _row(r, email, status=""):
    from app.core.mail_sender import Row
    return Row(r, "강하니", email, status, {})


def test_sorted_sheet_sent_person_moved_is_excluded(client, mem_store):
    _seed_sent(mem_store, "other@x.com", 4)  # u4@x.com 에게 보냈고 O 기록 실패
    rows = [_row(4, "new@x.com"), _row(6, " U4@X.com ")]  # 정렬 뒤: 4행은 새 사람, 보낸 사람은 6행
    with _start_env(rows):
        c = _check(client).json()
    assert c["pending"] == 1
    per_row = {i["row"]: i["message"] for i in c["issues"] if i["row"] is not None and i["level"] == "warn"}
    assert set(per_row) == {6} and "이메일" in per_row[6]
    with _start_env(rows) as th:
        r = _start(client, 1)
    assert r.status_code == 200
    assert [i["sheet_row"] for i in mem_store.items[r.json()["job_id"]]] == [4]


def test_unknown_blocks_row_and_email(client, mem_store):
    _seed_unknown(mem_store, _User.email, rows=(3,))  # u3@x.com, 3행
    rows = [_row(3, "other3@x.com"), _row(9, "u3@x.com"), _row(10, "z@x.com")]
    with _start_env(rows):
        c = _check(client).json()
    assert c["pending"] == 1
    per_row = {i["row"]: i["message"] for i in c["issues"] if i["row"] is not None and i["level"] == "warn"}
    assert "행" in per_row[3] and "이메일" in per_row[9]


def test_check_lists_own_unknown_jobs_and_ack_interrupted_job(client, mem_store):
    jid = _seed_unknown(mem_store, _User.email)  # 재시작으로 중단된 작업의 3, 7행
    assert mem_store.jobs[jid]["status"] == "interrupted"
    rows = _rows_at((3, ""), (7, ""))
    with _start_env(rows):
        c = _check(client).json()
    assert c["unknown_jobs"] == [{"job_id": jid, "rows": [3, 7]}] and c["pending"] == 0
    assert client.get("/api/mail-sender/jobs/active").json() == {"job": None}  # 활성 작업으로는 못 닿는다
    assert client.post(f"/api/mail-sender/jobs/{jid}/ack-unknown").json() == {"acknowledged": 2}
    with _start_env(rows):
        c2 = _check(client).json()
    assert c2["unknown_jobs"] == [] and c2["pending"] == 2


def test_orphaned_sending_is_listed_and_cleared_by_ack(client, mem_store):
    from app.core.mail_sender import Row
    jid = mem_store.insert_job(_User.email, "x", "gmail_a", _User.email, [Row(5, "강하니", "u5@x.com", "", {})])
    mem_store.set_item_status(jid, 5, "sending")
    mem_store.set_job(jid, status="stopped")  # 정리되지 못한 'sending'
    rows = _rows_at((5, ""))
    with _start_env(rows):
        c = _check(client).json()
    assert c["unknown_jobs"] == [{"job_id": jid, "rows": [5]}] and c["pending"] == 0
    assert client.post(f"/api/mail-sender/jobs/{jid}/ack-unknown").json() == {"acknowledged": 1}
    with _start_env(rows):
        assert _check(client).json()["pending"] == 1


def test_other_owners_unknown_rows_say_who_must_confirm(client, mem_store):
    _seed_unknown(mem_store, "other@x.com", rows=(4,))
    with _start_env(_rows_at((4, ""), (5, ""))):
        c = _check(client).json()
    assert c["unknown_jobs"] == [] and c["pending"] == 1
    msgs = [i["message"] for i in c["issues"]]
    assert any("다른 사람(other@x.com)의 작업에서 발송 여부 불확실 — 그 사람이 확인해야 합니다" in m for m in msgs)


def test_js_shows_unknown_jobs_with_ack_buttons():
    js = (ROOT / "app/static/mail_sender.js").read_text(encoding="utf-8")
    assert "unknown_jobs" in js and 'getElementById(id)' in js and '$("ms-unknown-jobs")' in js
    assert re.search(r"guard\(\(\) => busy\(b, async \(\) => \{", js)
    assert "await runCheck()" in js and '$("ms-check").onclick = guard(runCheck)' in js
    html = (ROOT / "app/static/mail_sender.html").read_text(encoding="utf-8")
    assert 'id="ms-unknown-jobs"' in html


# ---------------------------------------------------------------------------
# Final review
# ---------------------------------------------------------------------------
def _http_error(status):
    import httplib2
    from googleapiclient.errors import HttpError
    resp = httplib2.Response({"status": status})
    resp.reason = "insufficientPermissions"
    return HttpError(resp, b'{"error": {"message": "x"}}')


@pytest.mark.parametrize("exc", ["http403", "auth", "net"])
def test_status_google_error_in_sendas_is_200_with_error(client, exc):
    import requests
    from google.auth.exceptions import TransportError
    err = {"http403": _http_error(403), "auth": TransportError("t"), "net": requests.ConnectionError("c")}[exc]
    with patch("app.api.mail_sender_api._credentials", return_value=object()), \
         patch("app.api.mail_sender_api.mail_google.missing_mail_scopes", return_value=[]), \
         patch("app.api.mail_sender_api.mail_google.send_as_addresses", side_effect=err):
        r = client.get("/api/mail-sender/status")
    j = r.json()
    assert r.status_code == 200 and j["senders"] == [] and j["error"]


def test_status_loads_credentials_off_the_event_loop():
    src = (ROOT / "app/api/mail_sender_api.py").read_text(encoding="utf-8")
    body = src[src.index("async def status("):src.index('@router.post("/api/mail-sender/sheets")')]
    assert "await asyncio.to_thread(_credentials, user.email)" in body
    assert "creds = _credentials(" not in body


def test_check_returns_first_subject_per_manager_with_targets(client, mem_store):
    from app.core.mail_sender import Row
    from app.core.mail_template import MailTemplate
    tpl = MailTemplate(subject="[S X {kol id}] Offer", html="<p>{kol id}</p>", images={})
    rows = [Row(2, "강하니", "a@x.com", "O", {"kol id": "sent"}), Row(3, "강하니", "b@x.com", "", {"kol id": "first"}),
            Row(4, "강하니", "c@x.com", "", {"kol id": "second"}), Row(5, "고도연", "d@x.com", "O", {"kol id": "x"})]
    with _start_env(rows, load_side=lambda *a: (rows, _cols(), {"강하니": tpl, "고도연": tpl},
                                                 {"강하니": "t.a", "고도연": "t.b"}, None)):
        j = _check(client).json()
    assert j["subjects"] == {"강하니": "[S X first] Offer"}  # 대상 없는 고도연은 없다


def test_js_confirm_lists_subjects_and_sender():
    js = (ROOT / "app/static/mail_sender.js").read_text(encoding="utf-8")
    assert "state.subjects = j.subjects" in js
    body = js[js.index('$("ms-send-all").onclick'):js.index("function renderJob")]
    assert "state.subjects" in body and "confirm(" in body
    assert "명 · " in body and '$("ms-sender").value' in body and "innerHTML" not in body



def test_test_send_and_job_use_sendas_display_name(client, mem_store):
    from email import policy
    from app.core.mail_sender import Row
    rows = [Row(2, "강하니", "kol@x.com", "", {})]
    alias = [{"email": "mk@x.com", "display": "SKIN1004 마케팅", "reply_to": "r@x.com", "default": True}]
    sent = {}
    with _start_env(rows, sendas_side=lambda c: alias) as th, \
         patch("app.api.mail_sender_api.mail_google.send_raw", side_effect=lambda c, raw: sent.setdefault("raw", raw) and "id"):
        r = client.post("/api/mail-sender/test-send",
                        json={"spreadsheet_id": "x", "sheet": "gmail_a", "row": 2, "sender": "mk@x.com"})
        assert r.status_code == 200
        frm = message_from_bytes(sent["raw"], policy=policy.default)["From"]
        assert frm.addresses[0].display_name == "SKIN1004 마케팅"
        assert _start(client, 1, sender="mk@x.com").status_code == 200
    assert th.call_args.kwargs.get("sender_name") == "SKIN1004 마케팅"


def test_js_shows_reply_to_of_alias():
    js = (ROOT / "app/static/mail_sender.js").read_text(encoding="utf-8")
    html = (ROOT / "app/static/mail_sender.html").read_text(encoding="utf-8")
    assert 'id="ms-reply-to"' in html
    assert "reply_to" in js and re.search(r'text\(\$\("ms-reply-to"\)', js)



def _sheet_and_tabs():
    sheet = [["Date", "담당자", "{kol id}", "Email", "", "", "전송여부"], ["", "강하니", "k1", "a@x.com", "", "", ""]]
    tabs = [["담당자", "탭", "", ""], ["강하니", "t.a", "", ""]]
    return sheet, tabs


def test_corrupt_zip_is_that_managers_error_not_500(client):
    sheet, tabs = _sheet_and_tabs()
    with patch("app.api.mail_sender_api._credentials", return_value=object()), \
         patch("app.api.mail_sender_api.mail_google.missing_mail_scopes", return_value=[]), \
         patch("app.api.mail_sender_api.mail_google.read_sheet", side_effect=[sheet, tabs]), \
         patch("app.api.mail_sender_api.mail_google.read_template_cache_index", return_value={}), \
         patch("app.api.mail_sender_api.mail_google.cached_tab_zip", return_value=b"PK\x03\x04 broken"):
        r = _check(client)
    assert r.status_code == 200
    j = r.json()
    assert any(i["level"] == "error" and i["row"] == 2 and "템플릿 오류" in i["message"] for i in j["issues"])


def test_missing_tab_directory_sheet_is_readable_400(client):
    sheet, _ = _sheet_and_tabs()
    with patch("app.api.mail_sender_api._credentials", return_value=object()), \
         patch("app.api.mail_sender_api.mail_google.missing_mail_scopes", return_value=[]), \
         patch("app.api.mail_sender_api.mail_google.read_sheet", side_effect=[sheet, _http_error(400)]):
        r = _check(client)
    assert r.status_code == 400
    assert "_탭목록" in r.json()["detail"] and "gmailv2" in r.json()["detail"]


def test_js_popup_blocked_is_reported():
    js = (ROOT / "app/static/mail_sender.js").read_text(encoding="utf-8")
    body = js[js.index('$("ms-connect-btn").onclick'):]
    body = body[:body.index("location.reload")]
    assert "팝업이 차단되었습니다 — 팝업을 허용해 주세요" in body
    assert body.index("if (!w)") < body.index("setInterval")


def test_middleware_page_list_is_formatted():
    mw = (ROOT / "app/api/middleware.py").read_text(encoding="utf-8")
    assert re.search(r'"/mail-sender", "/', mw)


def test_sheet_format_problem_is_readable_400_not_500(client):
    """2026-10-01: 양식 오류(ValueError)가 원시 500 으로 나가 '오류: 500' 만 보였다."""
    with patch("app.api.mail_sender_api._credentials", return_value=object()),          patch("app.api.mail_sender_api.mail_google.missing_mail_scopes", return_value=[]),          patch("app.api.mail_sender_api.mail_google.read_sheet", return_value=[]):
        r = _check(client)
    assert r.status_code == 400 and "헤더" in r.json()["detail"]


def test_tab_missing_from_cache_is_that_managers_error(client):
    """캐시에 아직 없는 탭(새로 만든 탭) — 전체 실패가 아니라 그 담당자 행의 오류로 보인다."""
    sheet, tabs = _sheet_and_tabs()
    with patch("app.api.mail_sender_api._credentials", return_value=object()), \
         patch("app.api.mail_sender_api.mail_google.missing_mail_scopes", return_value=[]), \
         patch("app.api.mail_sender_api.mail_google.read_sheet", side_effect=[sheet, tabs]), \
         patch("app.api.mail_sender_api.mail_google.read_template_cache_index", return_value={"tabs": {}}):
        r = _check(client)
    assert r.status_code == 200
    assert any(i["row"] == 2 and "캐시" in i["message"] for i in r.json()["issues"])


def test_spreadsheet_is_chosen_from_list_with_gmail_v2_default(client):
    """2026-10-01 요청: 링크 붙여넣기 대신 목록에서 고른다 — 기본값은 gmail_v2."""
    from app.core.mail_sender import MAIL_SPREADSHEETS
    assert MAIL_SPREADSHEETS[0]["id"] == "1n08f1VIna9hZH03Zdws-31-G7RFaYJ3T7Eg7vQk636c"
    with patch("app.api.mail_sender_api._credentials", return_value=None):
        r = client.get("/api/mail-sender/status")
    assert r.json()["spreadsheets"] == MAIL_SPREADSHEETS
    html = (ROOT / "app/static/mail_sender.html").read_text(encoding="utf-8")
    assert 'id="ms-book"' in html and 'id="ms-url"' not in html


def test_sheets_returns_gids_for_open_link(client):
    """'시트 열기' 링크 — gmail_ 시트만, 이름 → gid."""
    with patch("app.api.mail_sender_api._credentials", return_value=object()),          patch("app.api.mail_sender_api.mail_google.missing_mail_scopes", return_value=[]),          patch("app.api.mail_sender_api.mail_google.sheet_gids",
               return_value={"gmail_a": 11, "_탭목록": 22, "gmail_b": 0}):
        j = client.post("/api/mail-sender/sheets", json={"spreadsheet": "abc"}).json()
    assert j["sheets"] == ["gmail_a", "gmail_b"] and j["gids"] == {"gmail_a": 11, "gmail_b": 0}
    js = (ROOT / "app/static/mail_sender.js").read_text(encoding="utf-8")
    assert "#gid=${gid}" in js and 'id="ms-open"' in (ROOT / "app/static/mail_sender.html").read_text(encoding="utf-8")
