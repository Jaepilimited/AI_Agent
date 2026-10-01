"""메일 발송 첨부 — 공통 첨부(화면 업로드)·수신자별 첨부(시트 `첨부` 열의 Drive 링크). 네트워크 없음."""
import base64
import contextlib
from email import message_from_bytes, policy
from unittest.mock import MagicMock, patch

import pytest

from app.core import mail_google
from app.core import mail_sender_jobs as J
from app.core.mail_mime import build_mime
from app.core.mail_sender import Columns, Row, drive_ids, parse_rows
from app.core.mail_template import MailTemplate
from tests.test_coa_finder_api import _User, client  # noqa: F401  (같은 로그인 픽스처 재사용)

ID1 = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
ID2 = "1ZyXwVuTsRqPoNmLkJiHgFeDcBa_-9876"
PNG = b"\x89PNG\r\n\x1a\n" + b"1" * 10
TPL = MailTemplate(subject="[S] {kol id}", html="<p>{kol id}</p>", images={})


def _parse(raw):
    return message_from_bytes(raw, policy=policy.default)


# ---- drive_ids --------------------------------------------------------------
def test_drive_ids_link_forms_and_bare_id():
    cell = (f"https://drive.google.com/file/d/{ID1}/view?usp=sharing\n"
            f"https://docs.google.com/document/d/{ID2}/edit, "
            "https://docs.google.com/spreadsheets/d/SHEETID_aaaaaaaaaaaaaaa/edit#gid=0 "
            "https://docs.google.com/presentation/d/SLIDEID_aaaaaaaaaaaaaaa/edit "
            "https://drive.google.com/open?id=OPENID_aaaaaaaaaaaaaaaaaa "
            "https://drive.google.com/uc?export=download&id=UCID_aaaaaaaaaaaaaaaaaaaa "
            f"{ID1}")
    ids, bad = drive_ids(cell)
    assert ids == [ID1, ID2, "SHEETID_aaaaaaaaaaaaaaa", "SLIDEID_aaaaaaaaaaaaaaa",
                   "OPENID_aaaaaaaaaaaaaaaaaa", "UCID_aaaaaaaaaaaaaaaaaaaa"]  # 중복 제거, 순서 유지
    assert bad == []


def test_drive_ids_unknown_pieces_and_empty():
    ids, bad = drive_ids(f"{ID1}, 계약서 https://example.com/a.pdf short_id")
    assert ids == [ID1] and bad == ["계약서", "https://example.com/a.pdf", "short_id"]
    assert drive_ids("") == ([], [])
    assert drive_ids(" \n , ") == ([], [])


# ---- parse_rows -------------------------------------------------------------
def _sheet(header_i="첨부", attach=""):
    head = ["날짜", "담당자", "{kol id}", "이메일", "", "", "전송여부", "비고", header_i]
    return [head, ["", "강하니", "k", "a@x.com", "", "", "", "", attach]]


def test_parse_rows_finds_attach_column_by_header():
    rows, cols = parse_rows(_sheet(" Attachments ", f"https://drive.google.com/file/d/{ID1}/view"))
    assert cols.attach == 8 and rows[0].attachments == [ID1] and rows[0].attach_bad == []
    rows, cols = parse_rows(_sheet("첨부파일", "모름"))
    assert cols.attach == 8 and rows[0].attachments == [] and rows[0].attach_bad == ["모름"]


def test_parse_rows_without_attach_column():
    rows, cols = parse_rows(_sheet("메모", ID1))
    assert cols.attach is None and rows[0].attachments == [] and rows[0].attach_bad == []


def test_parse_rows_ignores_attach_name_on_fixed_columns():
    sheet = [["첨부", "첨부", "{kol id}", "첨부", "", "", "첨부", "첨부"],
             [ID1, "강하니", "k", "a@x.com", "", "", "", ID2]]
    rows, cols = parse_rows(sheet)
    assert cols.attach is None and rows[0].attachments == []


# ---- build_mime -------------------------------------------------------------
def test_build_mime_attachments_mixed_with_korean_name_and_inline_images():
    raw = build_mime(sender="a@x.com", to="b@y.com", subject="s", html='<p><img src="cid:img1@m"></p>',
                     images={"img1@m": (PNG, "image/png")},
                     attachments=[("계약서 초안.pdf", "application/pdf", b"%PDF-1"), ("x.bin", "이상한값", b"\x00\x01")])
    assert raw.decode("ascii")  # 원문은 ASCII 만
    m = _parse(raw)
    assert m.get_content_type() == "multipart/mixed"
    parts = list(m.iter_parts())
    assert parts[0].get_content_type() == "multipart/related"
    rel = list(parts[0].iter_parts())
    assert rel[0].get_content_type() == "text/html" and rel[1]["Content-ID"] == "<img1@m>"
    att = list(m.iter_attachments())
    assert [a.get_filename() for a in att] == ["계약서 초안.pdf", "x.bin"]
    assert att[0].get_content_type() == "application/pdf" and att[0].get_content() == b"%PDF-1"
    assert att[1].get_content_type() == "application/octet-stream"


def test_build_mime_attachment_without_images():
    m = _parse(build_mime(sender="a@x.com", to="b@y.com", subject="s", html="<p>x</p>", images={},
                          attachments=[("a.txt", "text/plain", b"hi")]))
    assert m.get_content_type() == "multipart/mixed"
    assert next(m.iter_parts()).get_content_type() == "text/html"


# ---- mail_google.drive_meta / drive_attachment ------------------------------
def _resp(status, content=b"", js=None):
    return MagicMock(status_code=status, content=content, json=MagicMock(return_value=js or {}))


def _sess(*responses):
    sess = MagicMock()
    sess.get.side_effect = list(responses)
    return sess


def test_drive_meta_url_and_params():
    sess = _sess(_resp(200, js={"name": "a.pdf", "mimeType": "application/pdf", "size": "10"}))
    with patch("app.core.mail_google.AuthorizedSession", return_value=sess):
        meta = mail_google.drive_meta(MagicMock(), "F1")
    assert meta["name"] == "a.pdf"
    assert sess.get.call_args.args[0] == "https://www.googleapis.com/drive/v3/files/F1"
    assert sess.get.call_args.kwargs["params"] == {"fields": "name,mimeType,size", "supportsAllDrives": "true"}


@pytest.mark.parametrize("status,needle", [(404, "찾을 수 없"), (403, "접근 권한이 없습니다"),
                                           (401, "접근 권한이 없습니다"), (500, "HTTP 500")])
def test_drive_meta_errors(status, needle):
    with patch("app.core.mail_google.AuthorizedSession", return_value=_sess(_resp(status))):
        with pytest.raises(RuntimeError, match=needle):
            mail_google.drive_meta(MagicMock(), "F1")


def test_drive_attachment_plain_file_uses_alt_media():
    sess = _sess(_resp(200, js={"name": "사진.png", "mimeType": "image/png", "size": "3"}), _resp(200, b"PNG"))
    with patch("app.core.mail_google.AuthorizedSession", return_value=sess):
        got = mail_google.drive_attachment(MagicMock(), "F1")
    assert got == ("사진.png", "image/png", b"PNG")
    call = sess.get.call_args_list[1]
    assert call.args[0] == "https://www.googleapis.com/drive/v3/files/F1"
    assert call.kwargs["params"]["alt"] == "media"


def test_drive_attachment_google_doc_exports_pdf():
    sess = _sess(_resp(200, b"%PDF"))
    meta = {"name": "제안서", "mimeType": "application/vnd.google-apps.document"}
    with patch("app.core.mail_google.AuthorizedSession", return_value=sess):
        got = mail_google.drive_attachment(MagicMock(), "D1", meta=meta)
    assert got == ("제안서.pdf", "application/pdf", b"%PDF")
    assert sess.get.call_args.args[0] == "https://www.googleapis.com/drive/v3/files/D1/export"
    assert sess.get.call_args.kwargs["params"] == {"mimeType": "application/pdf"}
    assert all("googleusercontent" not in c.args[0] for c in sess.get.call_args_list)


def test_drive_attachment_folder_is_refused_without_download():
    sess = _sess()
    with patch("app.core.mail_google.AuthorizedSession", return_value=sess):
        with pytest.raises(RuntimeError, match="첨부할 수 없는 종류"):
            mail_google.drive_attachment(MagicMock(), "D1",
                                         meta={"name": "폴더", "mimeType": "application/vnd.google-apps.folder"})
    sess.get.assert_not_called()


def test_drive_attachment_download_403_is_readable():
    sess = _sess(_resp(403))
    with patch("app.core.mail_google.AuthorizedSession", return_value=sess):
        with pytest.raises(RuntimeError, match="공유받았는지"):
            mail_google.drive_attachment(MagicMock(), "F1", meta={"name": "a.pdf", "mimeType": "application/pdf"})


# ---- API --------------------------------------------------------------------
@pytest.fixture(autouse=True)
def mem_store(monkeypatch):
    s = J.MemoryStore()
    monkeypatch.setattr(J, "_store", s)
    return s


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def test_decode_attachments_limits_and_names():
    from fastapi import HTTPException
    from app.api.mail_sender_api import AttachmentIn, _decode_attachments
    got = _decode_attachments([AttachmentIn(name="../a\\b\x07.pdf", mime="", data=_b64(b"x")),
                               AttachmentIn(name=" / ", mime="image/png", data=_b64(b"y"))])
    assert got == [("..ab.pdf", "application/octet-stream", b"x"), ("첨부파일", "image/png", b"y")]
    with pytest.raises(HTTPException) as e:
        _decode_attachments([AttachmentIn(name="a", data="@@not base64@@")])
    assert e.value.status_code == 400 and "base64" in e.value.detail
    with pytest.raises(HTTPException) as e:
        _decode_attachments([AttachmentIn(name=str(i), data=_b64(b"x")) for i in range(11)])
    assert e.value.status_code == 400 and "10개" in e.value.detail
    big = _b64(b"\0" * (8 * 1024 * 1024))
    with pytest.raises(HTTPException) as e:
        _decode_attachments([AttachmentIn(name="a", data=big), AttachmentIn(name="b", data=big)])
    assert e.value.status_code == 400 and "15MB" in e.value.detail


def _cols():
    return Columns(3, 1, 6, 7, 0, {}, attach=8)


@contextlib.contextmanager
def _env(rows, metas=None):
    metas = metas or {}

    def meta(creds, fid):
        m = metas.get(fid)
        if isinstance(m, Exception):
            raise m
        return m or {"name": f"{fid}.pdf", "mimeType": "application/pdf", "size": "100"}
    with patch("app.api.mail_sender_api._credentials", return_value="CREDS"), \
         patch("app.api.mail_sender_api.mail_google.missing_mail_scopes", return_value=[]), \
         patch("app.api.mail_sender_api.mail_google.send_as_addresses",
               return_value=[{"email": "me@x.com", "display": "", "default": True}]), \
         patch("app.api.mail_sender_api._load",
               return_value=(rows, _cols(), {"강하니": TPL}, {"강하니": "t.a"}, None)), \
         patch("app.api.mail_sender_api.mail_google.drive_meta", side_effect=meta) as dm, \
         patch("app.api.mail_sender_api.jobs.start_job_thread") as th:
        yield dm, th


def _row(r, ids=(), bad=()):
    return Row(r, "강하니", f"u{r}@x.com", "", {"kol id": f"k{r}"}, attachments=list(ids), attach_bad=list(bad))


def test_test_send_attaches_common_and_drive_files(client):
    rows = [_row(2, [ID1])]
    sent = {}
    with _env(rows), \
         patch("app.api.mail_sender_api.mail_google.drive_attachment",
               return_value=("드라이브.pdf", "application/pdf", b"%PDF")) as da, \
         patch("app.api.mail_sender_api.mail_google.send_raw",
               side_effect=lambda c, raw: sent.setdefault("raw", raw) and "id1"):
        r = client.post("/api/mail-sender/test-send", json={
            "spreadsheet_id": "x", "sheet": "gmail_a", "row": 2, "sender": "me@x.com",
            "attachments": [{"name": "공통.txt", "mime": "text/plain", "data": _b64(b"hello")}]})
    assert r.status_code == 200, r.text
    assert da.call_args.args == ("CREDS", ID1)
    m = _parse(sent["raw"])
    assert m["To"] == _User.email
    assert [a.get_filename() for a in m.iter_attachments()] == ["공통.txt", "드라이브.pdf"]


def test_test_send_bad_base64_is_400(client):
    with _env([_row(2)]), patch("app.api.mail_sender_api.mail_google.send_raw") as send:
        r = client.post("/api/mail-sender/test-send", json={
            "spreadsheet_id": "x", "sheet": "gmail_a", "row": 2, "sender": "me@x.com",
            "attachments": [{"name": "a", "data": "%%%"}]})
    assert r.status_code == 400 and "base64" in r.json()["detail"]
    send.assert_not_called()


def test_preview_lists_drive_attachment_names(client):
    rows = [_row(2, [ID1, ID2])]
    metas = {ID2: {"name": "문서", "mimeType": "application/vnd.google-apps.spreadsheet"}}
    with _env(rows, metas):
        j = client.post("/api/mail-sender/preview", json={"spreadsheet_id": "x", "sheet": "gmail_a", "row": 2}).json()
    assert j["attachments"] == [f"{ID1}.pdf", "문서.pdf"]


def _check(client, common_bytes=0):
    return client.post("/api/mail-sender/check", json={"spreadsheet_id": "x", "sheet": "gmail_a",
                                                       "common_bytes": common_bytes})


def test_check_reports_inaccessible_and_bad_links_per_row(client):
    rows = [_row(2, [ID1]), _row(3, [ID2], bad=["계약서"]), _row(4, [ID1]), _row(5)]
    metas = {ID1: RuntimeError("첨부 파일에 접근 권한이 없습니다 — 파일을 공유받았는지 확인하세요"),
             ID2: {"name": "폴더", "mimeType": "application/vnd.google-apps.folder"}}
    with _env(rows, metas) as (dm, _):
        j = _check(client).json()
    assert dm.call_count == 2  # 같은 ID 는 한 번만
    errs = {(i["row"], i["message"]) for i in j["issues"] if i["level"] == "error"}
    assert any(r == 2 and "접근 권한" in m for r, m in errs) and any(r == 4 and "접근 권한" in m for r, m in errs)
    assert any(r == 3 and "첨부 링크를 알아볼 수 없습니다: 계약서" in m for r, m in errs)
    assert any(r == 3 and "첨부할 수 없는 종류" in m for r, m in errs)
    assert not any(r == 5 for r, _ in errs)
    assert [r["attachments"] for r in j["rows"]] == [1, 1, 1, 0]


def test_check_size_overflow_with_common_and_drive(client):
    rows = [_row(2, [ID1]), _row(3)]
    metas = {ID1: {"name": "big.zip", "mimeType": "application/zip", "size": str(10 * 1024 * 1024)}}
    with _env(rows, metas):
        j = _check(client, common_bytes=9 * 1024 * 1024).json()
    errs = [i for i in j["issues"] if i["level"] == "error"]
    assert [i["row"] for i in errs] == [2] and "메일 용량 초과 예상" in errs[0]["message"]
    with _env(rows, metas):
        j = _check(client, common_bytes=20 * 1024 * 1024).json()
    assert {i["row"] for i in j["issues"] if i["level"] == "error"} == {2, 3}


def test_jobs_refuses_on_attachment_errors_and_passes_common_on_success(client, mem_store):
    rows = [_row(2, [ID1])]
    with _env(rows, {ID1: RuntimeError("첨부 파일을 찾을 수 없습니다")}) as (_, th):
        r = client.post("/api/mail-sender/jobs", json={"spreadsheet_id": "x", "sheet": "gmail_a",
                                                       "sender": "me@x.com", "confirm_count": 1})
    assert r.status_code == 400 and "점검 오류" in r.json()["detail"] and "찾을 수 없" in r.json()["detail"]
    th.assert_not_called()
    assert mem_store.jobs == {}
    with _env(rows) as (_, th):
        r = client.post("/api/mail-sender/jobs", json={
            "spreadsheet_id": "x", "sheet": "gmail_a", "sender": "me@x.com", "confirm_count": 1,
            "attachments": [{"name": "a.txt", "mime": "text/plain", "data": _b64(b"hi")}]})
    assert r.status_code == 200, r.text
    assert th.call_args.kwargs["attachments"] == [("a.txt", "text/plain", b"hi")]
    jid = r.json()["job_id"]
    assert mem_store.items[jid][0]["row_values"]["__attach__"] == ID1


# ---- jobs -------------------------------------------------------------------
COLS = Columns(email=3, manager=1, status=6, note=7, date=0, variables={"kol id": 2})


def _deps(sent, fetch):
    def read(c, sid, sh, row, cols):
        vals = {COLS.status: "", COLS.email: f"u{row}@x.com"}
        return {k: vals.get(k, "") for k in cols}
    return J.JobDeps(read_cells=read, write_cells=MagicMock(),
                     send_raw=lambda c, raw: sent.append(raw) or f"id{len(sent)}",
                     sleep=lambda s: None, drive_attachment=fetch)


def test_insert_job_stores_attach_ids_only_when_present(mem_store):
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", [_row(2, [ID1, ID2]), _row(3)])
    vals = [i["row_values"] for i in mem_store.items[jid]]
    assert vals[0]["__attach__"] == f"{ID1},{ID2}" and "__attach__" not in vals[1]


def test_compose_attaches_drive_files_once_per_id_and_common(mem_store):
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", [_row(2, [ID1]), _row(3, [ID1, ID2]), _row(4)])
    fetch = MagicMock(side_effect=lambda c, fid: (f"{fid}.pdf", "application/pdf", b"%PDF"))
    sent = []
    J.run_job(jid, "CREDS", COLS, {"강하니": TPL}, _deps(sent, fetch),
              attachments=(("공통.txt", "text/plain", b"c"),))
    assert J.get_job(jid, "me@x.com")["sent"] == 3
    assert sorted(c.args[1] for c in fetch.call_args_list) == [ID1, ID2]  # 같은 ID 는 한 번만
    names = [[a.get_filename() for a in _parse(raw).iter_attachments()] for raw in sent]
    assert names == [["공통.txt", f"{ID1}.pdf"], ["공통.txt", f"{ID1}.pdf", f"{ID2}.pdf"], ["공통.txt"]]
    assert "__attach__" not in _parse(sent[0]).get_body(("html",)).get_content()


def test_download_failure_marks_row_failed(mem_store):
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", [_row(2, [ID1]), _row(3)])
    deps = _deps(sent := [], MagicMock(side_effect=RuntimeError("첨부 파일에 접근 권한이 없습니다")))
    J.run_job(jid, "CREDS", COLS, {"강하니": TPL}, deps)
    items = mem_store.items[jid]
    assert items[0]["status"] == "failed" and "접근 권한" in items[0]["error"]
    assert items[1]["status"] == "sent" and len(sent) == 1
    x = [c for c in deps.write_cells.call_args_list if c.args[3] == 2]
    assert x and x[0].args[4][COLS.status] == "X"


def test_oversized_message_is_not_sent(mem_store, monkeypatch):
    import app.core.mail_sender as ms
    monkeypatch.setattr(ms, "MAX_MESSAGE_BYTES", 2000)
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", [_row(2)])
    sent = []
    J.run_job(jid, "CREDS", COLS, {"강하니": TPL}, _deps(sent, MagicMock()),
              attachments=(("big.bin", "application/octet-stream", b"\0" * 5000),))
    item = mem_store.items[jid][0]
    assert sent == [] and item["status"] == "failed" and "메일 용량 초과" in item["error"]


def test_start_job_thread_passes_attachments(monkeypatch):
    seen = {}

    class FakeThread:
        def __init__(self, target=None, args=(), daemon=None, name=None):
            seen["args"] = args

        def start(self):
            pass
    monkeypatch.setattr(J.threading, "Thread", FakeThread)
    files = [("a.txt", "text/plain", b"x")]
    J.start_job_thread(1, "C", COLS, {}, sender_name="n", attachments=files)
    assert seen["args"][-1] == tuple(files)
