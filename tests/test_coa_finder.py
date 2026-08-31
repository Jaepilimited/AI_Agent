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


def test_product_terms_drops_brand_words():
    """브랜드어만 남으면 MSDS 검색이 전 제품을 긁는다."""
    terms = cf.product_terms("SKIN1004 Madagascar Centella Poremizing Fresh Ampoule 50ml")
    assert "Poremizing" in terms and "Fresh" in terms
    assert not any(t.lower() in ("skin1004", "madagascar", "centella") for t in terms)


def test_product_terms_strips_cpnp_marker():
    assert "CPNP" not in cf.product_terms("SKIN1004 Madagascar Centella Ampoule 100ml_CPNP")


def test_find_all_never_widens_the_query():
    """⛔ expand()/llm_variants() 를 부르면 이 기능은 오답 생성기가 된다."""
    rows = [cf.Row("EUSKA032", "SKIN1004 Madagascar Centella Poremizing Fresh Ampoule 50ml",
                   "E07Z083", 1)]
    calls = []

    def fake_search(creds, query, max_results=10, mime_contains=None,
                    exact_name=None, **kwargs):
        calls.append((query, exact_name))
        return [{"id": "1", "name": "...POREMIZING FRESH AMPOULE COA (E07Z082)",
                 "size": 100, "webViewLink": "http://d/1"}]

    with patch("app.core.query_keywords.expand") as ex, \
         patch("app.core.query_keywords.llm_variants") as lv:
        results = list(cf.find_all(MagicMock(), rows, search=fake_search))

    ex.assert_not_called()
    lv.assert_not_called()
    assert results[0].coa.status == cf.NONE
    assert all(c[1] for c in calls if c[0] == "E07Z083"), "exact_name 을 넘기지 않았다"


def test_find_all_keeps_row_order():
    rows = [cf.Row(f"S{i}", "앰플", f"LOT{i:04d}", i) for i in range(5)]

    def fake_search(*a, **k):
        return []

    got = list(cf.find_all(MagicMock(), rows, search=fake_search))
    assert [r.row.sku for r in got] == [f"S{i}" for i in range(5)]


def test_find_all_marks_query_failure_without_killing_the_run():
    rows = [cf.Row("A", "앰플", "LOT1", 1), cf.Row("B", "앰플", "LOT2", 2)]

    def fake_search(creds, query, **k):
        if query == "LOT1":
            raise RuntimeError("drive down")
        return []

    got = list(cf.find_all(MagicMock(), rows, search=fake_search))
    assert got[0].coa.status == cf.CHECK and "조회" in got[0].coa.note
    assert got[1].coa.status == cf.NONE


def test_find_all_disables_internal_widening_for_msds():
    """⛔ MSDS 는 여러 낱말로 찾으므로 search_drive 내부 확장이 열려 있다."""
    rows = [cf.Row("EUSKA032", "SKIN1004 Madagascar Centella Poremizing Fresh Ampoule 50ml",
                   "E07Z083", 1)]
    seen = []

    def fake_search(creds, query, max_results=10, mime_contains=None,
                    exact_name=None, **kwargs):
        seen.append((query, kwargs.get("widen")))
        return []

    list(cf.find_all(MagicMock(), rows, search=fake_search))
    msds_calls = [s for s in seen if "MSDS" in s[0]]
    assert msds_calls, "MSDS 조회가 일어나지 않았다"
    assert all(w is False for _, w in msds_calls), "MSDS 조회가 widen 을 끄지 않았다"


def test_msds_search_filters_out_body_only_matches():
    """실측 사고 (2026-08-31 라이브 조회): 검색어가 본문에만 스친 무관 문서가
    최상위로 나왔다 — 인증 서류 리스트·등록 현황표는 MSDS 가 아니다."""
    rows = [cf.Row("EUSKA032", "SKIN1004 Madagascar Centella Ampoule 100ml", "", 1)]

    def fake_search(creds, query, max_results=10, mime_contains=None,
                    exact_name=None, **kwargs):
        if "MSDS" not in query:
            return []
        return [
            {"id": "hit", "name": "MSDS_SKIN1004 MADAGASCAR CENTELLA AMPOULE 100ML.pdf",
             "size": 100, "webViewLink": "http://d/1"},
            {"id": "cert-list", "name": "★ [SK] 인증 서류 리스트",
             "size": 200, "webViewLink": "http://d/2"},
            {"id": "registration", "name": "Good brands Morocco - Registration.xlsx",
             "size": 300, "webViewLink": "http://d/3"},
            {"id": "other-msds", "name": "MSDS_OTHER PRODUCT LINE.pdf",
             "size": 400, "webViewLink": "http://d/4"},
        ]

    got = list(cf.find_all(MagicMock(), rows, search=fake_search))
    assert got[0].msds.status == cf.FOUND
    assert [f.id for f in got[0].msds.files] == ["hit"]


def test_msds_search_matches_real_werc_filename():
    """AMPOULE 100ML 은 잡아야 하는 실제 파일명 (팀 리드 실측)."""
    rows = [cf.Row("EUSKB050", "SKIN1004 Madagascar Centella Cream 75ml", "", 1)]

    def fake_search(creds, query, max_results=10, mime_contains=None,
                    exact_name=None, **kwargs):
        if "MSDS" not in query:
            return []
        return [{"id": "1",
                 "name": "SKIN1004 Madagascar Centella Cream 75ML_MSDS(WERCS).pdf",
                 "size": 100, "webViewLink": "http://d/1"}]

    got = list(cf.find_all(MagicMock(), rows, search=fake_search))
    assert got[0].msds.status == cf.FOUND


def test_msds_search_allows_legitimate_ambiguity():
    """'Cream 75ml' 이 'Soothing Cream 75ml' MSDS 에도 걸리는 것은 정상 여러건이다
    — 잡음은 걸러도 정당한 모호함까지 지우면 안 된다."""
    rows = [cf.Row("EUSKB050", "SKIN1004 Madagascar Centella Cream 75ml", "", 1)]

    def fake_search(creds, query, max_results=10, mime_contains=None,
                    exact_name=None, **kwargs):
        if "MSDS" not in query:
            return []
        return [
            {"id": "1", "name": "SKIN1004 Madagascar Centella Cream 75ML_MSDS(WERCS).pdf",
             "size": 100, "webViewLink": "http://d/1"},
            {"id": "2", "name": "MSDS_SKIN1004 MADAGASCAR CENTELLA SOOTHING CREAM 75ml.pdf",
             "size": 200, "webViewLink": "http://d/2"},
        ]

    got = list(cf.find_all(MagicMock(), rows, search=fake_search))
    assert got[0].msds.status == cf.MANY
    assert len(got[0].msds.files) == 2


def test_classify_lot_bug_surfaces_instead_of_masquerading_as_a_network_failure():
    """⛔ try 를 조회 함수 하나로만 좁혀야 한다 — classify_lot 버그를
    '조회에 실패했습니다' 로 감추면 진짜 결함이 확인필요 뒤에 숨는다."""
    rows = [cf.Row("A", "앰플", "LOT1", 1)]

    def fake_search(creds, query, **k):
        return [{"id": "1", "name": "LOT1.pdf", "size": 1, "webViewLink": "http://d/1"}]

    with patch("app.core.coa_finder.classify_lot", side_effect=AttributeError("boom")):
        with pytest.raises(AttributeError):
            list(cf.find_all(MagicMock(), rows, search=fake_search))


def test_msds_note_reflects_unusable_description_not_emptiness():
    """제품명이 브랜드어뿐이라 검색어가 안 나온 것과, 제품명 자체가 빈 것은
    다른 사실이다 — 사용자가 쓴 제품명을 '비어 있다' 고 답하면 안 된다."""
    rows = [cf.Row("A", "SKIN1004 Madagascar Centella", "", 1)]

    got = list(cf.find_all(MagicMock(), rows, search=lambda *a, **k: []))
    assert got[0].msds.status == cf.NONE
    assert got[0].msds.note == "제품명에서 검색에 쓸 낱말을 찾지 못했습니다"


def test_msds_note_for_truly_empty_description():
    rows = [cf.Row("A", "", "", 1)]

    got = list(cf.find_all(MagicMock(), rows, search=lambda *a, **k: []))
    assert got[0].msds.status == cf.NONE
    assert got[0].msds.note == "제품명이 비어 있습니다"
