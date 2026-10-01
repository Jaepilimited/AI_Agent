from datetime import datetime, timedelta, timezone

import pytest

from app.core.mail_sender import (
    MAX_MESSAGE_BYTES, check, parse_rows, parse_tab_directory, pending,
)
from app.core.mail_template import MailTemplate

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
SHEET = [
    ["Date", "담당자", "{KOL ID}", "Email", "{value1}", "{value2}", "전송여부", "비고"],
    ["", "강하니 ", "maria_jflores", "maria@gmail.com", "skin1004", "", "", ""],
    ["2026-09-30", "강하니", "done", "done@gmail.com", "", "", "O", ""],
    ["", "고도연", "bob", "bob@x", "", "", "", ""],
    ["", "없는사람", "c", "c@x.com", "", "", "", ""],
    ["", "강하니", "dup", "maria@gmail.com", "", "", "", ""],
]
TAB_DIR = [["탭 제목", "탭 ID", "상위 탭", "갱신 시각"],
           ["강하니", "t.a", "자동화 컨택 Template", "2026-10-01T08:55:00Z"],
           ["고도연", "t.b", "자동화 컨택 Template", "2026-10-01T08:55:00Z"],
           ["강하늬", "t.c", "", "2026-10-01T08:55:00Z"]]
TPL = MailTemplate(subject="[SKIN1004 X {KOL ID}] Offer", html="<p>Hi {kol id} {value1}</p>", images={})


def test_parse_rows_by_header_and_strip():
    rows, cols = parse_rows(SHEET)
    assert (cols.manager, cols.email, cols.status, cols.note, cols.date) == (1, 3, 6, 7, 0)
    assert set(cols.variables) == {"kol id", "value1", "value2"}
    assert rows[0].row == 2 and rows[0].manager == "강하니" and rows[0].values["kol id"] == "maria_jflores"


def test_parse_rows_reads_by_position_like_apps_script():
    """헤더 이름이 달라도(`문서명`, `전송 여부`) 위치로 읽는다 — 2026-10-01 혜진 시트."""
    sheet = [["", "문서명", "{name}", "Email", "{value1}", "{value2}", "전송 여부", "비고"],
             ["2026. 9. 9", "박혜진_템플릿", "vera", "vera@gmail.com", "", "", "O", ""],
             ["", "박혜진_템플릿", "siu", "siu@gmail.com", "x", "", "", ""]]
    rows, cols = parse_rows(sheet)
    assert (cols.manager, cols.email, cols.status, cols.note, cols.date) == (1, 3, 6, 7, 0)
    assert [r.row for r in pending(rows)] == [3]
    assert rows[1].manager == "박혜진_템플릿" and rows[1].values == {"name": "siu", "value1": "x", "value2": ""}


def test_parse_rows_empty_header_raises():
    with pytest.raises(ValueError, match="헤더"):
        parse_rows([["", ""], ["a", "b"]])
    with pytest.raises(ValueError, match="헤더"):
        parse_rows([])


def test_pending_skips_O_and_blank_email():
    rows, _ = parse_rows(SHEET)
    assert [r.row for r in pending(rows)] == [2, 4, 5, 6]


def test_tab_directory():
    tabs, updated = parse_tab_directory(TAB_DIR)
    assert tabs["강하니"] == "t.a" and updated == datetime(2026, 10, 1, 8, 55, tzinfo=timezone.utc)


def _issues(**kw):
    rows, _ = parse_rows(SHEET)
    tabs, updated = parse_tab_directory(TAB_DIR)
    args = dict(rows=pending(rows), tab_dir=tabs, dir_updated_at=updated,
                templates={"강하니": TPL, "고도연": TPL}, now=NOW)
    args.update(kw)
    return check(**args)


def test_manager_trailing_space_matches():
    assert not [i for i in _issues() if i.row == 2 and i.level == "error"]


def test_check_reports_missing_tab_with_suggestion_and_refresh_time():
    msg = [i.message for i in _issues() if i.row == 5][0]
    assert "탭 없음" in msg and "17:55" in msg


def test_check_bad_email_and_duplicate():
    issues = _issues()
    assert any(i.row == 4 and "이메일" in i.message and i.level == "error" for i in issues)
    assert any(i.row == 6 and "중복" in i.message and i.level == "warn" for i in issues)


def test_check_reports_unreplaced():
    tpl = MailTemplate(subject="s", html="<p>{KOL </p><p>ID}</p>", images={})
    issues = _issues(templates={"강하니": tpl, "고도연": tpl})
    assert any(i.row == 2 and "{KOL" in i.message for i in issues)


def test_check_template_error_is_error():
    issues = _issues(templates={"강하니": ValueError("탭 첫 줄이 '제목:' 으로 시작하지 않습니다"), "고도연": TPL})
    assert any(i.row == 2 and "제목:" in i.message and i.level == "error" for i in issues)


def test_check_stale_directory_warns():
    issues = _issues(now=NOW + timedelta(hours=2))
    assert any(i.row is None and "탭 목록" in i.message for i in issues)


def test_check_rejects_oversized_template():
    big = MailTemplate(subject="s", html="<p>x</p>", images={"a@m": (b"0" * (MAX_MESSAGE_BYTES + 1), "image/png")})
    issues = _issues(templates={"강하니": big, "고도연": TPL})
    assert any(i.row == 2 and "용량" in i.message and i.level == "error" for i in issues)


def test_check_daily_limit():
    issues = _issues(sent_today=1999)
    assert any(i.row is None and "한도" in i.message and i.level == "error" for i in issues)


# Fix Round 1 Tests

def test_pending_case_insensitive_status():
    """Status 'o' and ' O ' should be treated as sent and excluded from pending."""
    sheet = [
        ["Date", "담당자", "", "Email", "", "", "전송여부"],
        ["", "강하니", "", "a@x.com", "", "", "o"],
        ["", "강하니", "", "b@x.com", "", "", " O "],
        ["", "강하니", "", "c@x.com", "", "", ""],
    ]
    rows, _ = parse_rows(sheet)
    pending_rows = pending(rows)
    assert [r.row for r in pending_rows] == [4]  # Only row 4 (empty status) is pending


def test_parse_tab_directory_unparseable_time():
    """Unparseable 갱신 시각 should be treated as None without raising."""
    tab_dir = [["탭 제목", "탭 ID", "상위 탭", "갱신 시각"],
               ["강하니", "t.a", "", "garbage"]]
    tabs, updated = parse_tab_directory(tab_dir)
    assert tabs["강하니"] == "t.a"
    assert updated is None


def test_parse_tab_directory_naive_timestamp():
    """Naive ISO timestamp (no timezone) should be parsed as UTC."""
    tab_dir = [["탭 제목", "탭 ID", "상위 탭", "갱신 시각"],
               ["강하니", "t.a", "", "2026-10-01T08:55:00"]]
    tabs, updated = parse_tab_directory(tab_dir)
    assert tabs["강하니"] == "t.a"
    assert updated == datetime(2026, 10, 1, 8, 55, tzinfo=timezone.utc)


def test_check_with_unparseable_time_no_crash():
    """check() should not crash with unparseable time and should warn about staleness."""
    sheet = [
        ["Date", "담당자", "", "Email", "", "", "전송여부"],
        ["", "강하니", "", "a@x.com", "", "", ""],
    ]
    tab_dir = [["탭 제목", "탭 ID", "상위 탭", "갱신 시각"],
               ["강하니", "t.a", "", "garbage"]]
    rows, _ = parse_rows(sheet)
    tabs, updated = parse_tab_directory(tab_dir)
    issues = check(rows=pending(rows), tab_dir=tabs, dir_updated_at=updated,
                  templates={"강하니": TPL}, now=NOW)
    # Should have staleness warning with "기록 없음"
    assert any(i.row is None and "탭 목록" in i.message and "기록 없음" in i.message for i in issues)


def test_parse_tab_directory_uses_oldest_timestamp():
    """Should use the oldest (minimum) timestamp across all rows."""
    tab_dir = [["탭 제목", "탭 ID", "상위 탭", "갱신 시각"],
               ["강하니", "t.a", "", "2026-10-01T10:00:00Z"],
               ["고도연", "t.b", "", "2026-10-01T08:55:00Z"],
               ["미영", "t.c", "", "2026-10-01T09:30:00Z"]]
    tabs, updated = parse_tab_directory(tab_dir)
    # Should be the minimum: 08:55:00Z
    assert updated == datetime(2026, 10, 1, 8, 55, tzinfo=timezone.utc)


def test_parse_rows_variable_header_with_spaces():
    """Variable header key should strip outer spaces: ' {KOL ID} ' → 'kol id'."""
    sheet = [
        ["Date", "담당자", " {KOL ID} ", "Email", "", "", "전송여부"],
        ["", "강하니", "value1", "a@x.com", "", "", ""],
    ]
    rows, cols = parse_rows(sheet)
    assert "kol id" in cols.variables
    assert cols.variables["kol id"] == 2


# ---------------------------------------------------------------------------
# Final review — 제목 줄바꿈, 이메일 형식, 확인 창 제목
# ---------------------------------------------------------------------------
def _one(email="a@x.com", values=None):
    from app.core.mail_sender import Row
    return [Row(2, "강하니", email, "", values or {"kol id": "k", "value1": "v"})]


@pytest.mark.parametrize("bad", ["k\nBcc: evil@x.com", "k\r\nX", "k\rY"])
def test_subject_with_line_break_is_error(bad):
    issues = check(_one(values={"kol id": bad, "value1": "v"}), {"강하니": "t.a"}, NOW, {"강하니": TPL}, NOW)
    assert any(i.level == "error" and i.row == 2 and "줄바꿈" in i.message for i in issues)


@pytest.mark.parametrize("bad", ["a@x.com,b", "a@x.com;b@y.com", "a b@x.com", "a@x.com b@y.com", "a@x .com"])
def test_email_with_separator_or_space_is_error(bad):
    issues = check(_one(email=bad), {"강하니": "t.a"}, NOW, {"강하니": TPL}, NOW)
    assert any(i.level == "error" and "이메일 형식" in i.message for i in issues), bad


def test_first_subjects_per_manager():
    from app.core.mail_sender import Row, first_subjects
    rows = [Row(2, "강하니", "a@x.com", "", {"kol id": "first"}), Row(3, "강하니", "b@x.com", "", {"kol id": "second"}),
            Row(4, "고도연", "c@x.com", "", {"kol id": "c"}), Row(5, "없는사람", "d@x.com", "", {})]
    got = first_subjects(rows, {"강하니": TPL, "고도연": ValueError("bad")})
    assert got == {"강하니": "[SKIN1004 X first] Offer"}
