"""COA·MSDS 롯트 찾기 — 판정 회귀. 네트워크를 타지 않는다."""
import io
import pytest
from unittest.mock import MagicMock, patch

from app.core import coa_finder as cf
from app.core.google_workspace import search_drive


def _fake_service(captured):
    svc = MagicMock()

    def _list(**params):
        captured.append(params)
        resp = MagicMock()
        resp.execute.return_value = {"files": []}
        return resp

    svc.files.return_value.list.side_effect = _list
    return svc


def test_search_drive_includes_shared_drives():
    """공유드라이브가 빠지면 COA 는 통째로 안 보인다 (2026-08-28)."""
    captured = []
    with patch("app.core.google_workspace.build",
               return_value=_fake_service(captured)):
        search_drive(MagicMock(), "E08Z011")

    assert captured, "files().list() 가 불리지 않았다"
    p = captured[0]
    assert p.get("includeItemsFromAllDrives") is True
    assert p.get("supportsAllDrives") is True
    assert p.get("corpora") == "allDrives"


def test_search_drive_exact_name_drops_near_miss():
    """E07Z083 을 물었는데 E07Z082 가 남으면 안 된다."""
    near = {"id": "1", "name": "…POREMIZING FRESH AMPOULE COA (E07Z082)",
            "mimeType": "application/pdf", "modifiedTime": "", "webViewLink": ""}
    svc = MagicMock()
    resp = MagicMock()
    resp.execute.return_value = {"files": [near]}
    svc.files.return_value.list.return_value = resp

    with patch("app.core.google_workspace.build", return_value=svc):
        got = search_drive(MagicMock(), "E07Z083", exact_name="E07Z083")

    assert got == []


def test_search_drive_can_disable_internal_widening():
    """⛔ 낱말을 줄여 다시 찾는 경로가 열려 있으면 무관한 파일이 '찾음' 으로 나간다."""
    calls = []

    def _list(**params):
        calls.append(params)
        resp = MagicMock()
        resp.execute.return_value = {"files": []}
        return resp

    svc = MagicMock()
    svc.files.return_value.list.side_effect = _list
    with patch("app.core.google_workspace.build", return_value=svc):
        search_drive(MagicMock(), "poremizing fresh ampoule MSDS", widen=False)
    assert len(calls) == 1, "widen=False 인데 재조회가 일어났다"


PASTED = """이 시트는 OP팀이 관리합니다
갱신: 2026-08-27

SKU\tDESCRIPTION\tLOT
EUSKA022\tSKIN1004 Madagascar Centella Ampoule 100ml_CPNP\tFE103C
EUSKC017\tSKIN1004 Madagascar Centella Cream 75ml_CPNP\t416022
EUSKA024\tSKIN1004 Madagascar Centella Tone Brightening Capsule Ampoule 100ml_CPNP\tF31C28 D
"""


def test_parse_pasted_finds_header_below_preamble():
    """⛔ 헤더를 몇 번째 행이라고 박지 마라 — 안내문이 한 줄 늘면 조용히 0건이 난다."""
    rows = cf.parse_pasted(PASTED)
    assert [r.sku for r in rows] == ["EUSKA022", "EUSKC017", "EUSKA024"]
    assert rows[2].lot == "F31C28 D"
    assert rows[0].line_no == 5


def test_parse_pasted_accepts_korean_headers():
    rows = cf.parse_pasted("품목\t제품명\t롯트\nEUSKA022\t앰플\tFE103C\n")
    assert rows[0].sku == "EUSKA022" and rows[0].lot == "FE103C"


def test_parse_pasted_reports_missing_column_instead_of_guessing():
    """⛔ 열을 못 찾으면 추측해서 진행하지 않는다."""
    with pytest.raises(cf.HeaderNotFound) as e:
        cf.parse_pasted("SKU\tDESCRIPTION\nEUSKA022\t앰플\n")
    assert "LOT" in e.value.missing


def test_parse_pasted_keeps_row_with_empty_lot():
    """롯트가 비어도 MSDS 는 제품명으로 찾을 수 있다 — 행을 버리지 않는다."""
    rows = cf.parse_pasted("SKU\tDESCRIPTION\tLOT\nEUSKA022\t앰플\t\n")
    assert rows[0].lot == ""


def test_parse_xlsx_uses_the_same_header_logic():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["이 시트는 OP팀이 관리합니다"])
    ws.append([])
    ws.append(["SKU", "DESCRIPTION", "LOT"])
    ws.append(["EUSKA022", "앰플 100ml", "FE103C"])
    buf = io.BytesIO()
    wb.save(buf)

    rows = cf.parse_xlsx(buf.getvalue())
    assert rows[0].sku == "EUSKA022" and rows[0].lot == "FE103C"
