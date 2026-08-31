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


def test_search_drive_retries_transient_failures():
    """find_all 은 최대 8개를 동시에 조회한다 - Drive 가 간헐적으로 429/5xx 를
    주면 사람은 그걸 '진짜 없음' 과 구분할 수 없다 (2026-08-31 실측)."""
    svc = MagicMock()
    resp = MagicMock()
    resp.execute.return_value = {"files": []}
    svc.files.return_value.list.return_value = resp

    with patch("app.core.google_workspace.build", return_value=svc):
        search_drive(MagicMock(), "E08Z011")

    resp.execute.assert_called_once_with(num_retries=3)


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
    rows = cf.parse_pasted(PASTED).rows
    assert [r.sku for r in rows] == ["EUSKA022", "EUSKC017", "EUSKA024"]
    assert rows[2].lot == "F31C28 D"
    assert rows[0].line_no == 5


def test_parse_pasted_accepts_korean_headers():
    rows = cf.parse_pasted("품목\t제품명\t롯트\nEUSKA022\t앰플\tFE103C\n").rows
    assert rows[0].sku == "EUSKA022" and rows[0].lot == "FE103C"


def test_parse_pasted_reports_missing_column_instead_of_guessing():
    """⛔ 열을 못 찾으면 추측해서 진행하지 않는다."""
    with pytest.raises(cf.HeaderNotFound) as e:
        cf.parse_pasted("SKU\tDESCRIPTION\nEUSKA022\t앰플\n")
    assert "LOT" in e.value.missing


def test_parse_pasted_keeps_row_with_empty_lot():
    """롯트가 비어도 MSDS 는 제품명으로 찾을 수 있다 — 행을 버리지 않는다."""
    rows = cf.parse_pasted("SKU\tDESCRIPTION\tLOT\nEUSKA022\t앰플\t\n").rows
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

    rows = cf.parse_xlsx(buf.getvalue()).rows
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


def test_different_extensions_are_not_collapsed_as_copies():
    """같은 이름·같은 크기라도 확장자가 다르면 서로 다른 문서다 (dedup 대상 아님).

    ⛔ 확장자를 접었을 때 잃는 것: pdf 와 xlsx 는 진짜 서로 다른 두 증명서일 수 있는데,
       하나로 접히면 사용자는 하나만 있는 줄 안다 — '의 사본' 접기와 반대로,
       여기서는 접지 않는 것이 맞는 판정이다.
    """
    files = [
        _f("COA_FE200.pdf", size=82070, fid="a"),
        _f("COA_FE200.xlsx", size=82070, fid="b"),
    ]
    v = cf.classify_lot("FE200", files)
    assert v.status == cf.MANY
    assert len(v.files) == 2


def test_two_real_candidates_are_all_shown():
    files = [_f("COA_A_FE103C", size=100, fid="a"),
             _f("COA_B_FE103C", size=200, fid="b")]
    v = cf.classify_lot("FE103C", files)
    assert v.status == cf.MANY and len(v.files) == 2


def test_lot_matching_ignores_case():
    """⛔ 입력은 사람이 엑셀에 적은 값이다 — 소문자로 적었다고 없는 문서가 되면 안 된다.

    Drive 의 contains 는 대소문자를 무시하므로 조회는 파일을 찾아온다. 우리 필터가
    파이썬 `in` 이라 그 파일을 도로 버렸다 (실측: E08Z011 찾음 / e08z011 없음).
    """
    files = [_f("51082SEA-003H SKIN1004 MADAGASCAR CENTELLA "
                "POREMIZING FRESH AMPOULE COA (E08Z011)")]
    assert cf.classify_lot("e08z011", files).status == cf.FOUND
    assert cf.classify_lot("E08z011", files).status == cf.FOUND
    assert cf.classify_lot("E08Z011", files).status == cf.FOUND


def test_case_insensitive_matching_keeps_the_boundary_rule():
    """⛔ 대소문자를 무시하면서 경계 검사가 느슨해지면 FE161 이 FE1615 에 걸린다 —
    C4 를 고치다 이 기능의 존재 이유를 되돌리는 것이 가장 나쁜 결과다."""
    assert cf.classify_lot("fe161", [_f("COA_10116720_..._FE1615_15643EA")]).status \
        != cf.FOUND
    assert cf.classify_lot("fe161", [_f("COA_10116720_..._FE161_15643EA")]).status \
        == cf.FOUND


def test_case_insensitive_matching_keeps_near_miss_rejection():
    """소문자로 적어도 E07Z082 를 E07Z083 이라고 주면 안 된다."""
    assert cf.classify_lot("e07z083", [_f("... COA (E07Z082)")]).status == cf.NONE


def test_suffix_fallback_ignores_case_too():
    """접미 롯트의 기저 비교도 같은 규칙을 따라야 한다."""
    v = cf.classify_lot("f31c28 d", [_f("COA_..._F31C28")])
    assert v.status == cf.CHECK and "접미" in v.note


def test_search_drive_exact_name_filter_ignores_case():
    """⛔ 조회는 파일을 찾아왔는데 우리 후필터가 대소문자로 버리면 조용한 0건이다."""
    hit = {"id": "1", "name": "... COA (E08Z011)", "mimeType": "application/pdf",
           "modifiedTime": "", "webViewLink": ""}
    svc = MagicMock()
    resp = MagicMock()
    resp.execute.return_value = {"files": [hit]}
    svc.files.return_value.list.return_value = resp

    with patch("app.core.google_workspace.build", return_value=svc):
        got = search_drive(MagicMock(), "e08z011", exact_name="e08z011")

    assert [f["id"] for f in got] == ["1"]


def test_search_drive_exact_name_still_drops_near_miss_when_case_folded():
    """⛔ 반대 방향 — casefold 를 넣다 근접 롯트 거절이 풀리면 C4 수정이 사고가 된다."""
    near = {"id": "1", "name": "... COA (E07Z082)", "mimeType": "application/pdf",
            "modifiedTime": "", "webViewLink": ""}
    svc = MagicMock()
    resp = MagicMock()
    resp.execute.return_value = {"files": [near]}
    svc.files.return_value.list.return_value = resp

    with patch("app.core.google_workspace.build", return_value=svc):
        got = search_drive(MagicMock(), "e07z083", exact_name="e07z083")

    assert got == []


def test_rows_with_empty_sku_are_counted_not_silently_dropped():
    """⛔ 40행을 넣고 38행을 받아도 세어보지 않으면 모른다 (재현: 4행 입력 → 3행)."""
    parsed = cf.parse_pasted(
        "SKU\tDESCRIPTION\tLOT\n"
        "A\t앰플\tFE103C\n"
        "\t크림\t416022\n"          # SKU 만 비었다 — 버릴 거면 버렸다고 말해야 한다
        "C\t로션\tE08Z011\n"
    )
    assert [r.sku for r in parsed.rows] == ["A", "C"]
    assert parsed.skipped_no_sku == 1


def test_entirely_blank_rows_are_not_counted_as_skipped():
    """빈 줄은 원래 거르던 것이다 — 그것까지 세면 숫자가 소음이 된다."""
    parsed = cf.parse_pasted("SKU\tDESCRIPTION\tLOT\nA\t앰플\tFE103C\n\t\t\n\n")
    assert len(parsed.rows) == 1
    assert parsed.skipped_no_sku == 0


def test_parse_xlsx_counts_skipped_rows_too():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["SKU", "DESCRIPTION", "LOT"])
    ws.append(["A", "앰플", "FE103C"])
    ws.append([None, "크림", "416022"])
    buf = io.BytesIO()
    wb.save(buf)

    parsed = cf.parse_xlsx(buf.getvalue())
    assert [r.sku for r in parsed.rows] == ["A"]
    assert parsed.skipped_no_sku == 1


def test_query_failure_has_its_own_status():
    """⛔ 조회실패를 확인필요로 뭉개면 '애매한 매칭 8건' 과 '조회가 안 된 8건' 이
    화면에서 구분되지 않는다. C1 수정 이후 확인필요는 이미 다른 뜻을 지고 있다."""
    rows = [cf.Row("A", "앰플", "LOT1", 1), cf.Row("B", "앰플", "LOT2", 2)]

    def fake_search(creds, query, **k):
        if query == "LOT1":
            raise RuntimeError("drive down")
        return []

    got = list(cf.find_all(MagicMock(), rows, search=fake_search))
    assert cf.FAILED == "조회실패"
    assert got[0].coa.status == cf.FAILED and "조회" in got[0].coa.note
    assert got[0].coa.files == ()          # 실패 행은 파일을 달고 나가지 않는다
    assert got[1].coa.status == cf.NONE    # 나머지 행은 계속 진행한다


def test_msds_query_failure_has_its_own_status():
    rows = [cf.Row("A", "SKIN1004 Madagascar Centella Ampoule 100ml", "", 1)]

    def fake_search(creds, query, **k):
        raise RuntimeError("drive down")

    got = list(cf.find_all(MagicMock(), rows, search=fake_search))
    assert got[0].msds.status == cf.FAILED


def test_whitespace_only_lot_does_not_masquerade_as_a_query_failure():
    """⛔ try 는 네트워크 호출만 감싼다. 접미 계산(`lot.split()[0]`)이 그 안에 있어
    공백뿐인 롯트가 IndexError 를 내면 '조회실패' 로 위장된다 — 진짜 결함이
    실패 뒤에 숨는 그 모양이다."""
    rows = [cf.Row("A", "", "   ", 1)]

    got = list(cf.find_all(MagicMock(), rows, search=lambda *a, **k: []))
    assert got[0].coa.status != cf.FAILED
    assert got[0].coa.status == cf.NONE


class _Httpish(Exception):
    """googleapiclient.HttpError 처럼 resp.status 를 갖는 예외."""

    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.resp = type("R", (), {"status": status})()


def test_query_failure_note_says_what_kind_of_failure():
    """⛔ 재시도하면 되는 것과 권한 문제가 한 문장으로 똑같이 보이면
    사용자는 무엇을 해야 할지 알 수 없다."""
    def _note_for(exc):
        rows = [cf.Row("A", "", "LOT1", 1)]

        def boom(*a, **k):
            raise exc

        return list(cf.find_all(MagicMock(), rows, search=boom))[0].coa.note

    assert "잠시" in _note_for(_Httpish(429))
    assert "권한" in _note_for(_Httpish(403))
    assert "일시" in _note_for(_Httpish(503))
    # 원인을 모르면 아는 척하지 않는다
    unknown = _note_for(RuntimeError("boom"))
    assert "권한" not in unknown and "잠시" not in unknown

    # ⛔ 예외 원문을 화면에 흘리지 마라 — URL·토큰이 섞여 나온다
    assert "boom" not in unknown


def test_none_verdict_says_what_was_searched():
    """⛔ 빈 note 로 '없음' 만 찍으면 네 가지가 글자 그대로 똑같이 보인다:
    정말 없는 것 · 토큰이 죽은 것 · 드라이브 멤버가 아닌 것 · 롯트를 잘못 적은 것.
    이 기능의 가치는 '없음' 을 믿어도 된다는 것인데, 믿을 근거를 화면이 줘야 한다."""
    v = cf.classify_lot("E07Z083", [_f("COA (E07Z082)")])
    assert v.status == cf.NONE
    assert v.note, "없음의 근거가 비어 있다"
    assert "파일명" in v.note and "본문" in v.note


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
    """한 행의 조회가 죽어도 나머지 행은 계속 간다.

    ⚠️ 상태는 확인필요가 아니라 조회실패다 — 판정과 실패는 다른 것이다
    (C6). 이 테스트가 지키는 사실은 '런이 안 죽는다' 쪽이다.
    """
    rows = [cf.Row("A", "앰플", "LOT1", 1), cf.Row("B", "앰플", "LOT2", 2)]

    def fake_search(creds, query, **k):
        if query == "LOT1":
            raise RuntimeError("drive down")
        return []

    got = list(cf.find_all(MagicMock(), rows, search=fake_search))
    assert got[0].coa.status == cf.FAILED and "조회" in got[0].coa.note
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


def test_find_all_coa_fallback_reaches_the_check_needed_branch():
    """⛔ exact_name=row.lot 이 접미까지 통째로 요구해, classify_lot 의 확인필요
    분기(접미 없는 파일)가 실제 흐름에서는 죽어 있었다 (2026-08-31 실측).
    이 테스트는 고치기 전엔 없음(NONE)을 내야 실패한다."""
    rows = [cf.Row("A", "앰플", "F31C28 D", 1)]
    calls = []

    def fake_search(creds, query, max_results=10, mime_contains=None,
                    exact_name=None, **kwargs):
        calls.append((query, exact_name))
        if query == "F31C28 D":
            return []
        if query == "F31C28":
            return [{"id": "1", "name": "COA_10116720_F31C28", "size": 1,
                     "webViewLink": "http://d/1"}]
        return []

    got = list(cf.find_all(MagicMock(), rows, search=fake_search))
    assert got[0].coa.status == cf.CHECK
    assert "접미" in got[0].coa.note
    assert ("F31C28", "F31C28") in calls, "접미 없는 롯트로 재조회하지 않았다"


def test_find_all_coa_skips_fallback_when_full_lot_already_matches():
    """접미가 붙은 롯트 그대로 찾아지면 재조회할 필요가 없다.

    description 은 비워 둔다 — MSDS 쪽도 같은 fake_search 를 부르므로,
    채우면 COA 재조회 여부와 무관하게 호출 수가 2가 된다."""
    rows = [cf.Row("A", "", "F31C28 D", 1)]
    calls = []

    def fake_search(creds, query, max_results=10, mime_contains=None,
                    exact_name=None, **kwargs):
        calls.append((query, exact_name))
        return [{"id": "1", "name": "COA_10116720_F31C28 D", "size": 1,
                 "webViewLink": "http://d/1"}]

    got = list(cf.find_all(MagicMock(), rows, search=fake_search))
    assert got[0].coa.status == cf.FOUND
    assert len(calls) == 1, "찾았는데도 재조회했다"


def test_find_all_coa_skips_fallback_for_unsuffixed_lot():
    """공백이 없는 롯트는 접미가 없으므로 재조회 대상이 아니다.

    description 은 비워 둔다 — MSDS 쪽 호출과 섞이지 않게 한다."""
    rows = [cf.Row("A", "", "FE103C", 1)]
    calls = []

    def fake_search(creds, query, max_results=10, mime_contains=None,
                    exact_name=None, **kwargs):
        calls.append((query, exact_name))
        return []

    got = list(cf.find_all(MagicMock(), rows, search=fake_search))
    assert got[0].coa.status == cf.NONE
    assert len(calls) == 1, "접미 없는 롯트인데 재조회했다"


def test_find_all_coa_fallback_search_uses_exact_name_on_base_lot():
    """⛔ 재조회에 exact_name 이 없으면 본문에만 롯트가 스친 문서(재고 현황표 등)가
    확인필요 후보로 올라온다 — 팀 리드 실측."""
    rows = [cf.Row("A", "앰플", "F31C28 D", 1)]
    calls = []

    def fake_search(creds, query, max_results=10, mime_contains=None,
                    exact_name=None, **kwargs):
        calls.append((query, exact_name))
        return []

    list(cf.find_all(MagicMock(), rows, search=fake_search))
    fallback_calls = [c for c in calls if c[0] == "F31C28"]
    assert fallback_calls, "접미 없는 롯트로 재조회하지 않았다"
    assert all(exact == "F31C28" for _, exact in fallback_calls), \
        "재조회에 exact_name 을 넘기지 않았다"


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


def test_self_check_has_drive_access_probe():
    """토큰이 만료되면 이 기능은 에러가 아니라 '전부 없음' 으로 보인다."""
    from app.core.self_check import CHECKS

    assert "drive_shared_access" in {c.id for c in CHECKS}


def test_drive_access_probe_reports_disconnected(monkeypatch):
    """한 번도 연결한 적 없는 사람 — '미연결' 이라고 말해야 한다."""
    from app.core import google_auth, self_check

    monkeypatch.setattr(
        google_auth.GoogleAuthManager, "load_credentials",
        lambda self, email: google_auth.CredentialLoadOutcome(
            "disconnected", error_code="oauth_missing"),
    )
    r = self_check._check_drive_shared_access()
    assert r.ok is False
    assert "미연결" in r.detail


def test_drive_access_probe_reports_expired_token_distinctly(monkeypatch):
    """⛔ 예전엔 연결했다가 토큰이 죽은 사람에게 '미연결'이라고 하면 안 된다 —
    이 검사가 존재하는 이유가 바로 이 구분이다."""
    from app.core import google_auth, self_check

    monkeypatch.setattr(
        google_auth.GoogleAuthManager, "load_credentials",
        lambda self, email: google_auth.CredentialLoadOutcome(
            "invalid", error_code="oauth_expired"),
    )
    r = self_check._check_drive_shared_access()
    assert r.ok is False
    assert "미연결" not in r.detail
    assert "만료" in r.detail or "무효" in r.detail


def test_drive_access_probe_reports_transient_error(monkeypatch):
    """구글 쪽 일시 오류는 재연결 안내가 아니라 '다시 확인' 으로 말해야 한다."""
    from app.core import google_auth, self_check

    monkeypatch.setattr(
        google_auth.GoogleAuthManager, "load_credentials",
        lambda self, email: google_auth.CredentialLoadOutcome(
            "transient_error", error_code="google_error"),
    )
    r = self_check._check_drive_shared_access()
    assert r.ok is False
    assert "일시" in r.detail


def test_drive_access_probe_search_failure_is_reported(monkeypatch):
    from app.core import google_auth, google_workspace, self_check

    monkeypatch.setattr(
        google_auth.GoogleAuthManager, "load_credentials",
        lambda self, email: google_auth.CredentialLoadOutcome(
            "ready", credentials=object()),
    )

    def _boom(*a, **k):
        raise RuntimeError("timeout talking to drive")

    monkeypatch.setattr(google_workspace, "search_drive", _boom)
    r = self_check._check_drive_shared_access()
    assert r.ok is False
    assert "timeout talking to drive" in r.detail


def test_drive_access_probe_empty_result_is_not_a_failure(monkeypatch):
    """검색어에 맞는 파일이 없는 것은 고장이 아니다 — 응답이 왔으면 통과."""
    from app.core import google_auth, google_workspace, self_check

    monkeypatch.setattr(
        google_auth.GoogleAuthManager, "load_credentials",
        lambda self, email: google_auth.CredentialLoadOutcome(
            "ready", credentials=object()),
    )
    monkeypatch.setattr(google_workspace, "search_drive", lambda *a, **k: [])
    r = self_check._check_drive_shared_access()
    assert r.ok is True
