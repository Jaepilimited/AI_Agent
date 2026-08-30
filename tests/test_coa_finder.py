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


def _f(name, size=1000, fid="x"):
    return cf.DriveFile(id=fid, name=name, size=size, web_link="http://d/" + fid)


def test_near_miss_lot_is_not_a_match():
    """⛔ E07Z083 을 물었는데 E07Z082 를 주면 규제 문서가 잘못 나간다 (실측값)."""
    files = [_f("51082SEA-001W SKIN1004 MADAGASCAR CENTELLA "
                "POREMIZING FRESH AMPOULE COA (E07Z082)")]
    v = cf.classify_lot("E07Z083", files)
    assert v.status == cf.NONE
    assert v.files == ()


def test_numeric_near_miss_lot_is_not_a_match():
    """416022 를 물었는데 416006 을 주면 안 된다 (실측값)."""
    files = [_f("SKIN1004 Madagascar Centella Cream COA (SKMC) 416006.pdf의 사본")]
    assert cf.classify_lot("416022", files).status == cf.NONE


def test_lot_between_underscores_matches():
    """FE161 처럼 언더바 사이에 낀 롯트는 정상 매칭이다 (실측값)."""
    files = [_f("COA_10116720_SCA1-MHWSCM(F)N4(A)_SKIN1004 MADAGASCAR CENTELLA "
                "HYALU-CICA WATER-FIT SUN SERUM_FE161_15643EA")]
    v = cf.classify_lot("FE161", files)
    assert v.status == cf.FOUND and len(v.files) == 1


def test_suffixed_lot_without_suffix_in_filename_is_check_needed():
    """F31C28 D 를 물었는데 접미 없는 파일만 있으면 사람에게 넘긴다."""
    files = [_f("COA_SKIN1004 ... TONE BRIGHTENING CAPSULE AMPOULE 100ml(N3)_F31C28")]
    v = cf.classify_lot("F31C28 D", files)
    assert v.status == cf.CHECK
    assert "접미" in v.note


def test_suffixed_lot_with_exact_filename_is_found():
    files = [_f("COA_SKIN1004 ... PROBIO-CICA Glow SUN AMPOULE 50ml(N3)_F14D40 D")]
    assert cf.classify_lot("F14D40 D", files).status == cf.FOUND


def test_short_lot_is_check_needed():
    """4자 이하는 다른 코드의 머리에 걸릴 수 있다."""
    v = cf.classify_lot("FF21", [_f("COA_...FF21...")])
    assert v.status == cf.CHECK


def test_copies_collapse_to_one_row():
    """'의 사본' 과 원본, 같은 파일이 두 폴더에 있는 경우를 한 줄로 접는다."""
    files = [
        _f("COA (SKMC) 416006.pdf", size=82070, fid="a"),
        _f("COA (SKMC) 416006.pdf의 사본", size=82070, fid="b"),
        _f("COA (SKMC) 416006.pdf", size=82070, fid="c"),
    ]
    v = cf.classify_lot("416006", files)
    assert v.status == cf.FOUND
    assert len(v.files) == 1


def test_two_real_candidates_are_all_shown():
    files = [_f("COA_A_FE103C", size=100, fid="a"),
             _f("COA_B_FE103C", size=200, fid="b")]
    v = cf.classify_lot("FE103C", files)
    assert v.status == cf.MANY and len(v.files) == 2


def test_empty_lot_is_not_searched():
    v = cf.classify_lot("", [_f("아무거나")])
    assert v.status == cf.NONE and "롯트" in v.note


def test_substring_lot_without_boundary_is_not_found():
    """⛔ FE161 이 FE1615 에도 걸리면 다른 롯트의 증명서가 나간다 (경계 없는 매칭)."""
    files = [_f("COA_10116720_..._FE1615_15643EA")]
    v = cf.classify_lot("FE161", files)
    assert v.status != cf.FOUND


def test_delimited_real_filename_still_found_with_boundary_check():
    """경계 검사를 넣은 뒤에도 실제 파일명(언더바로 구분된 롯트)은 그대로 찾는다."""
    files = [_f("COA_10116720_SCA1-MHWSCM(F)N4(A)_SKIN1004 MADAGASCAR CENTELLA "
                "HYALU-CICA WATER-FIT SUN SERUM_FE161_15643EA")]
    v = cf.classify_lot("FE161", files)
    assert v.status == cf.FOUND and len(v.files) == 1


def test_parenthesis_delimited_lot_is_found():
    """괄호로 감싼 롯트도 경계로 인정한다 (실측값)."""
    files = [_f("51082SEA-003H SKIN1004 MADAGASCAR CENTELLA "
                "POREMIZING FRESH AMPOULE COA (E08Z011)")]
    v = cf.classify_lot("E08Z011", files)
    assert v.status == cf.FOUND and len(v.files) == 1


def test_delimited_match_wins_over_undelimited_match():
    """경계가 있는 매칭과 없는 매칭이 함께 있으면 경계가 있는 쪽으로 판정한다."""
    files = [
        _f("COA_10116720_..._FE1615_15643EA", fid="undelimited"),
        _f("COA_10116720_..._FE161_15643EA", fid="delimited"),
    ]
    v = cf.classify_lot("FE161", files)
    assert v.status == cf.FOUND
    assert len(v.files) == 1
    assert v.files[0].id == "delimited"
