# -*- coding: utf-8 -*-
"""엑셀·CSV 를 올리면 표로 읽는다 (붐따 #161).

⛔ 여기서 가장 중요한 성질은 **구분자가 탭**이라는 것이다. 서버의
   `_has_pasted_data` 가 탭을 보고 "사용자가 가져온 표" 로 판정하고, 그 판정이
   붙여넣은 표를 조회로 덮어쓰는 것을 막는다 (2026-08-18 실측 사고). 쉼표로
   바꾸면 그 방어가 통째로 풀린다 — 에러 없이.
"""
import io

import pytest
from openpyxl import Workbook

from app.core import table_attachment as TA


def _xlsx(rows, sheets=()):
    wb = Workbook()
    ws = wb.active
    ws.title = "데이터"
    for r in rows:
        ws.append(r)
    for name in sheets:
        wb.create_sheet(name)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── 읽기 ────────────────────────────────────────────────────────────────

def test_an_xlsx_becomes_tab_separated_text():
    """⛔ 탭이어야 한다 — `_has_pasted_data` 가 탭을 본다."""
    data = _xlsx([["신고번호", "주문번호"], ["13164-26-302095X", "202606290002"]])
    tsv = TA.parse("면장.xlsx", data).to_tsv()
    assert tsv.splitlines()[0] == "신고번호\t주문번호"
    assert "," not in tsv.splitlines()[0]


def test_a_small_attached_table_is_still_treated_as_pasted_data():
    """⛔ 이게 이 설계 전체가 기대는 성질이다 — 안 걸리면 첨부한 표를 무시하고
    BigQuery 를 조회해 **묻지도 않은 데이터**가 답으로 나간다 (2026-08-18 사고).

    ⚠️ `_has_pasted_data` 에는 **200자 문턱**이 있어서, 8행 × 3열짜리 첨부는
       표식이 없으면 그 문턱에 걸려 그냥 통과한다. 표식이 그것을 막는다.
    """
    from app.agents.orchestrator import OrchestratorAgent as O

    data = _xlsx([["a", "b", "c"], [1, 2, 3], [4, 5, 6], [7, 8, 9]])
    block = TA.parse("t.xlsx", data).to_block("t.xlsx")
    assert len(block) < 200, "이 검사가 뜻을 가지려면 문턱보다 짧아야 한다"
    agent = O.__new__(O)
    assert agent._has_pasted_data("이 표 정리해줘\n" + block)


def test_the_marker_is_not_duplicated_in_the_frontend():
    """⛔ 표식을 프론트가 따로 조립하면 사본이 갈리고, 갈리는 순간 위 판정이
    조용히 풀린다 (`@@` 목록이 두 벌이라 어긋났던 그 사고와 같은 종류)."""
    with open("app/frontend/chat.js", encoding="utf-8") as fh:
        js = fh.read()
    assert TA.ATTACHMENT_MARKER not in js
    assert "data.block" in js


def test_a_korean_csv_saved_by_excel_is_decoded():
    """⚠️ 한국어 엑셀이 내보낸 CSV 는 cp949 인 경우가 흔하다 — utf-8 로만 읽으면
    깨지거나 터진다 (이 프로젝트 콘솔이 겪는 것과 같은 함정)."""
    raw = "국가,매출\n일본,100\n미국,200\n".encode("cp949")
    tsv = TA.parse("a.csv", raw).to_tsv()
    assert "일본\t100" in tsv


def test_a_utf8_bom_csv_is_decoded():
    raw = "국가,매출\n일본,100\n".encode("utf-8-sig")
    assert "국가\t매출" in TA.parse("a.csv", raw).to_tsv()


def test_tabs_and_newlines_inside_a_cell_do_not_shift_the_table():
    """⛔ 칸 안의 탭·줄바꿈을 그대로 두면 행이 어긋나 표가 조용히 밀린다."""
    data = _xlsx([["a", "b"], ["줄1\n줄2", "탭\t포함"]])
    lines = TA.parse("t.xlsx", data).to_tsv().splitlines()
    assert len(lines) == 2
    assert lines[1].count("\t") == 1


# ── 자른 것은 말한다 ────────────────────────────────────────────────────

def test_row_truncation_is_announced():
    """⛔ 300행만 싣고 말하지 않으면, 올린 사람은 다 반영된 줄 안다."""
    data = _xlsx([["n"]] + [[i] for i in range(TA.MAX_ROWS + 50)])
    table = TA.parse("big.xlsx", data)
    assert table.truncated_rows > 0
    note = table.note("big.xlsx")
    assert "생략" in note and str(table.total_rows) in note


def test_other_sheets_are_named():
    """⛔ 첫 시트만 읽는다 — 조용히 하나만 읽으면 나머지가 없는 줄 안다."""
    data = _xlsx([["a"], [1]], sheets=("2월", "메모"))
    note = TA.parse("t.xlsx", data).note("t.xlsx")
    assert "첫 시트만" in note and "2월" in note and "메모" in note


def test_the_note_states_the_shape():
    data = _xlsx([["a", "b"], [1, 2]])
    assert "2행 × 2열" in TA.parse("t.xlsx", data).note("t.xlsx")


# ── 못 읽을 때는 무엇을 하면 되는지 말한다 ──────────────────────────────

def test_legacy_xls_says_what_to_do():
    """⛔ "열지 못했습니다" 로 뭉개면 파일이 깨진 줄 안다 — 형식 문제라고 말한다."""
    with pytest.raises(TA.UnsupportedFile) as e:
        TA.parse("old.xls", b"\xd0\xcf\x11\xe0")
    assert ".xlsx" in str(e.value)


def test_an_oversized_file_is_refused_with_a_number():
    with pytest.raises(TA.UnsupportedFile) as e:
        TA.parse("big.xlsx", b"x" * (TA.MAX_BYTES + 1))
    assert "MB" in str(e.value)


def test_an_unknown_extension_lists_what_is_accepted():
    with pytest.raises(TA.UnsupportedFile) as e:
        TA.parse("doc.pdf", b"%PDF-1.4")
    assert ".xlsx" in str(e.value) and ".csv" in str(e.value)


def test_an_empty_sheet_is_refused():
    with pytest.raises(TA.UnsupportedFile):
        TA.parse("empty.xlsx", _xlsx([]))


def test_a_broken_xlsx_points_at_pasting():
    with pytest.raises(TA.UnsupportedFile) as e:
        TA.parse("bad.xlsx", b"not really a workbook")
    assert "붙여넣" in str(e.value)


# ── 프론트 배선 ─────────────────────────────────────────────────────────

def _js():
    with open("app/frontend/chat.js", encoding="utf-8") as fh:
        return fh.read()


def test_the_frontend_uploads_spreadsheets_instead_of_refusing_them():
    src = _js()
    assert "/api/attachments/table" in src
    assert "addTableFile" in src and "isSpreadsheet" in src


def test_the_table_is_prepended_to_the_question():
    """⛔ 뒤에 붙이면 질문이 표 수백 줄 뒤로 밀려 라우터가 표를 먼저 본다."""
    src = _js()
    assert "text = tableBlock + text" in src


def test_sending_waits_for_a_table_that_is_still_loading():
    """⛔ 읽는 중에 보내면 그 표 없이 나가고, 보낸 사람은 붙은 줄 안다."""
    assert "아직 읽는 중입니다" in _js()


def test_the_chip_shows_what_was_read():
    """읽은 결과(행·열·시트·자른 만큼)를 그대로 보여준다 — 사진으로 올렸을 때
    없던 성질이다."""
    src = _js()
    assert "renderTableChips" in src and "t.note" in src


def test_the_strip_is_in_the_markup_not_injected_by_js():
    """⛔ JS 가 만들면 스크립트가 낡을 때 통째로 사라지고 에러는 안 난다."""
    with open("app/frontend/chat.html", encoding="utf-8") as fh:
        html = fh.read()
    assert 'id="table-preview-strip"' in html
    assert ".xlsx" in html and ".csv" in html      # 파일 선택창이 제안한다


def test_the_chip_styles_use_real_theme_tokens():
    """⛔ 없는 CSS 변수는 에러가 아니라 폴백이라 테마 전환에서 조용히 빠진다."""
    import re
    with open("app/static/style.css", encoding="utf-8") as fh:
        css = fh.read()
    block = css[css.index(".table-preview-strip"):css.index(".table-chip-remove:hover")]
    for var in set(re.findall(r"var\((--[a-z-]+)", block)):
        assert re.search(re.escape(var) + r"\s*:", css), f"{var} 가 정의돼 있지 않다"
