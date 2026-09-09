"""인증서류 찾기 화면 — 종류별 받기와 'COA 없는 목록' 을 실제 브라우저에서.

여기서 잡으려는 것은 전부 **에러 없이 잘못 보이는** 종류다:
- 핸들러에 kindFilter 를 안 넘기면 이벤트 객체가 그 자리에 들어가 모든 파일이
  걸러진다 — 화면엔 "받을 파일을 선택해주세요" 만 뜨고 콘솔은 조용하다
- 'COA만 받기' 가 MSDS 를 함께 담아도 ZIP 을 열기 전엔 아무도 모른다
  (그게 이 작업이 고치려는 원래 증상이다)
- 없음 목록이 조회실패 행을 담아도 CSV 는 멀쩡해 보인다
"""

import json
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "app/static/coa_finder.html"
SCRIPT = ROOT / "app/static/coa_finder.js"


def _verdict(status, files=(), note=""):
    return {"status": status, "note": note,
            "files": [{"id": i, "name": n, "link": "", "size": 10}
                      for i, n in files]}


ROWS = [
    {"index": 1, "total": 3, "sku": "A", "description": "앰플 100ml",
     "lot": "FE103C",
     "coa": _verdict("찾음", [("c1", "COA_AMPOULE_FE103C.pdf")]),
     "msds": _verdict("찾음", [("m1", "AMPOULE_MSDS.pdf")]),
     "product_coa": _verdict("없음")},
    {"index": 2, "total": 3, "sku": "B", "description": "크림, 75ml",
     "lot": "F31C28",
     "coa": _verdict("없음", note="파일명에 이 롯트가 든 파일이 없습니다"),
     "msds": _verdict("찾음", [("m2", "CREAM_MSDS.pdf")]),
     "product_coa": _verdict("없음")},
    {"index": 3, "total": 3, "sku": "C", "description": "선세럼",
     "lot": "MO388",
     "coa": _verdict("조회실패", note="드라이브 조회에 실패했습니다"),
     "msds": _verdict("없음"),
     "product_coa": _verdict("없음")},
]

DONE = {"total": 3,
        "counts": {"찾음": 1, "여러건": 0, "없음": 1, "확인필요": 0, "조회실패": 1},
        "msds_failed": 0, "product_coa_failed": 0, "skipped_no_sku": 0,
        "inferred": False, "layout": "",
        "max_download_items": 200, "max_download_bytes": 500 * 1024 * 1024}


# 후보가 여러 건인 화면. ⛔ 위 ROWS 에 섞지 마라 — 기존 테스트가 그 행 수와
# 파일 수를 세고 있어, 한 줄만 늘려도 무엇이 깨졌는지 알 수 없게 된다
MANY_ROWS = [
    {"index": 1, "total": 2, "sku": "KRSKSO14", "description": "선세럼",
     "lot": "FE221",
     "coa": _verdict("여러건",
                     [("c1", "SUN_SERUM_FE221_6150EA.pdf"),
                      ("c2", "SUN_SERUM_FE221_9450EA.pdf")],
                     note="2건 — 어느 것인지 확인이 필요합니다"),
     "msds": _verdict("찾음", [("m1", "SUN_SERUM_MSDS.pdf")]),
     "product_coa": _verdict("없음")},
    {"index": 2, "total": 2, "sku": "KRSKCO12", "description": "젤크림",
     "lot": "E04Z040",
     "coa": _verdict("없음"),
     "msds": _verdict("없음"),
     "product_coa": _verdict("여러건",
                             [("p1", "GEL_CREAM_COA_A.pdf"),
                              ("p2", "GEL_CREAM_COA_B.pdf")])},
]
# 후보 줄의 크기·수정일. ⛔ 마지막 후보는 수정일을 일부러 비워 둔다 —
# 없는 값을 지어내지 않는지 그 자리에서 확인한다
MANY_ROWS[0]["coa"]["files"][0].update(size=2 * 1024 * 1024,
                                       modified="2026-08-14T01:02:03.000Z")
MANY_ROWS[0]["coa"]["files"][1].update(size=51200, modified="")

MANY_COUNTS = {"찾음": 0, "여러건": 1, "없음": 1, "확인필요": 0, "조회실패": 0}


# The server has already resolved these duplicates. The browser must keep
# that selection and its explanation while retaining the other-lot opt-in.
LATEST_ROWS = [
    {"index": 1, "total": 2, "sku": "KRSKSO14", "description": "선세럼",
     "lot": "FE221",
     "coa": _verdict("찾음", [("c-latest", "SUN_SERUM_FE221_9450EA.pdf")],
                     note="검색된 3개 중 최종 수정일 기준 최신 1개 (2026-09-09 14:30 KST)"),
     "msds": _verdict("찾음", [("m-latest", "SUN_SERUM_MSDS.pdf")],
                      note="검색된 2개 중 최종 수정일 기준 최신 1개 (2026-09-08 10:15 KST)"),
     "product_coa": _verdict("없음")},
    {"index": 2, "total": 2, "sku": "KRSKCO12", "description": "젤크림",
     "lot": "E04Z040",
     "coa": _verdict("없음"),
     "msds": _verdict("없음"),
     "product_coa": _verdict("확인필요", [("p-other-lot", "GEL_CREAM_COA_OTHER_LOT.pdf")],
                             note="다른 롯트의 증명서입니다")},
]
LATEST_ROWS[0]["coa"]["files"][0]["modified"] = "2026-09-09T05:30:00Z"
LATEST_ROWS[0]["msds"]["files"][0]["modified"] = "2026-09-08T01:15:00Z"
LATEST_ROWS[1]["product_coa"]["files"][0]["modified"] = "2026-09-09T06:00:00Z"
LATEST_COUNTS = {"찾음": 1, "여러건": 0, "없음": 1, "확인필요": 0, "조회실패": 0}


def _frames():
    out = [f"event: row\ndata: {json.dumps(r, ensure_ascii=False)}\n\n" for r in ROWS]
    out.append(f"event: done\ndata: {json.dumps(DONE, ensure_ascii=False)}\n\n")
    return out


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    current = context.new_page()
    yield current
    context.close()


_STUBS = """
(frames) => {
  window.__downloads = [];
  window.__saved = [];
  URL.createObjectURL = (blob) => { window.__saved.push(blob); return "blob:stub"; };
  URL.revokeObjectURL = () => {};
  // saveBlob 은 <a download> 를 눌러 저장한다 — 헤드리스에서 실제 저장을 걸지
  // 않도록 앵커 클릭만 막는다. 담긴 Blob 은 위에서 이미 붙잡았다
  HTMLAnchorElement.prototype.click = function () {};
  window.fetch = async (url, opts) => {
    if (String(url).indexOf("/search") !== -1) {
      const enc = new TextEncoder();
      let i = 0;
      return {
        ok: true,
        body: { getReader: () => ({
          read: async () => (i < frames.length
            ? { done: false, value: enc.encode(frames[i++]) }
            : { done: true }),
        }) },
      };
    }
    window.__downloads.push(JSON.parse(opts.body));
    return { ok: true, headers: { get: () => "0" },
             blob: async () => new Blob(["zip"]) };
  };
}
"""


def _open_rows(page, rows, counts):
    html = PAGE.read_text(encoding="utf-8").replace(
        '<script src="/static/coa_finder.js"></script>',
        "<script>" + SCRIPT.read_text(encoding="utf-8") + "</script>")
    page.set_content(html)
    frames = [f"event: row\ndata: {json.dumps(r, ensure_ascii=False)}\n\n"
              for r in rows]
    frames.append("event: done\ndata: " + json.dumps(
        dict(DONE, total=len(rows), counts=counts), ensure_ascii=False) + "\n\n")
    page.evaluate(_STUBS, frames)
    page.fill("#cf-paste", "x")
    page.click("#cf-run")
    page.wait_for_function("() => !document.getElementById('cf-download').disabled")
    return page


def _open(page):
    return _open_rows(page, ROWS, DONE["counts"])


def _many(page):
    return _open_rows(page, MANY_ROWS, MANY_COUNTS)


def _cell(page, row, col):
    """0-based. 열 순서: 선택 · SKU · 제품명 · 롯트 · COA · MSDS · COA(제품)"""
    return page.locator("#cf-body tr").nth(row).locator("td").nth(col)


def _ids(page, index=0):
    items = page.evaluate("(i) => window.__downloads[i].items", index)
    return [it["file_id"] for it in items]


def _kinds(page, index=0):
    items = page.evaluate("(i) => window.__downloads[i].items", index)
    return [it["kind"] for it in items]


def test_latest_singletons_show_the_selection_reason_without_extra_checkboxes(page):
    _open_rows(page, LATEST_ROWS, LATEST_COUNTS)
    assert _cell(page, 0, 0).locator("input.cf-pick").is_checked()
    for col, kind in [(4, "coa"), (5, "msds")]:
        cell = _cell(page, 0, col)
        assert cell.locator("a.cf-file").count() == 1
        assert cell.locator("input.cf-pick-file").count() == 0
        assert cell.locator(".cf-note").inner_text() == LATEST_ROWS[0][kind]["note"]
        assert cell.locator(".st-found").inner_text() == "찾음"


@pytest.mark.parametrize("select_all", [False, True])
@pytest.mark.parametrize("button, expected_kinds", [
    ("#cf-download", ["coa", "msds"]),
    ("#cf-dl-coa", ["coa"]),
    ("#cf-dl-msds", ["msds"]),
])
def test_latest_downloads_send_only_selected_ids_with_their_original_verdicts(
    page, button, expected_kinds, select_all,
):
    _open_rows(page, LATEST_ROWS, LATEST_COUNTS)
    if select_all:
        page.check("#cf-all")
    page.click(button)
    page.wait_for_function("() => window.__downloads.length === 1")
    items = page.evaluate("() => window.__downloads[0].items")
    assert [item["kind"] for item in items] == expected_kinds
    for item in items:
        expected_id = "c-latest" if item["kind"] == "coa" else "m-latest"
        assert item["file_id"] == expected_id
        assert item["status"] == "찾음"
        assert item["sku"] == "KRSKSO14"
        assert item["lot"] == ("FE221" if item["kind"] == "coa" else "")
    assert not _cell(page, 1, 6).locator("input.cf-pick-product").is_checked()


def test_newer_other_lot_product_coa_still_needs_explicit_selection(page):
    _open_rows(page, LATEST_ROWS, LATEST_COUNTS)
    page.check("#cf-all")
    product_pick = _cell(page, 1, 6).locator("input.cf-pick-product")
    assert not product_pick.is_checked()
    page.click("#cf-download")
    page.wait_for_function("() => window.__downloads.length === 1")
    assert _ids(page) == ["c-latest", "m-latest"]

    product_pick.check()
    page.click("#cf-download")
    page.wait_for_function("() => window.__downloads.length === 2")
    items = page.evaluate("() => window.__downloads[1].items")
    product = next(item for item in items if item["file_id"] == "p-other-lot")
    assert product["kind"] == "product_coa"
    assert product["status"] == "확인필요"
    assert product["lot"] == ""


def test_the_search_renders_every_row(page):
    _open(page)
    assert page.locator("#cf-body tr").count() == 3


def test_coa_button_takes_no_msds(page):
    """원래 증상 — 한 번 받으면 COA 와 MSDS 가 섞여 왔다."""
    _open(page)
    page.check("#cf-all")
    page.click("#cf-dl-coa")
    page.wait_for_function("() => window.__downloads.length === 1")
    assert _kinds(page) == ["coa"]


def test_msds_button_takes_no_coa(page):
    _open(page)
    page.check("#cf-all")
    page.click("#cf-dl-msds")
    page.wait_for_function("() => window.__downloads.length === 1")
    assert set(_kinds(page)) == {"msds"}
    assert len(_kinds(page)) == 2


def test_the_whole_selection_still_comes_in_one_archive(page):
    """반대 방향 — 나누는 기능을 넣느라 원래 동작을 부수면 안 된다."""
    _open(page)
    page.check("#cf-all")
    page.click("#cf-download")
    page.wait_for_function("() => window.__downloads.length === 1")
    assert sorted(_kinds(page)) == ["coa", "msds", "msds"]


def test_a_kind_filter_says_how_much_it_left_out(page):
    """⛔ 선택한 것보다 적게 받았으면 화면이 그 수를 말해야 한다."""
    _open(page)
    page.check("#cf-all")
    page.click("#cf-dl-coa")
    page.wait_for_function("() => window.__downloads.length === 1")
    assert "2건" in page.locator("#cf-kind").inner_text()


def test_the_none_filter_only_hides(page):
    """받기 대상은 그대로다 — 가려진 행의 파일도 여전히 담긴다."""
    _open(page)
    page.check("#cf-all")
    page.check("#cf-only-none")
    visible = page.locator("#cf-body tr:not([hidden])")
    assert visible.count() == 1
    assert "F31C28" in visible.inner_text()

    page.click("#cf-download")
    page.wait_for_function("() => window.__downloads.length === 1")
    assert len(_kinds(page)) == 3


def _saved_csv(page):
    """받은 파일을 **바이트로** 읽는다.

    ⚠️ `Blob.text()` 는 규격상 BOM 을 떼어낸다 — 그걸로 검사하면 BOM 이
       빠져도 통과한다 (엑셀에서 한글이 깨지는 그 실패를 못 잡는다).
    """
    page.click("#cf-dl-none")
    page.wait_for_function("() => window.__saved.length === 1")
    raw = page.evaluate(
        "async () => Array.from(new Uint8Array("
        "await window.__saved[0].arrayBuffer()))")
    return bytes(raw)


def _csv(page):
    return _saved_csv(page).decode("utf-8-sig")


def test_the_missing_coa_list_holds_only_the_none_rows(page):
    _open(page)
    raw = _saved_csv(page)
    assert raw[:3] == b"\xef\xbb\xbf", "BOM 이 없으면 엑셀이 한글을 깨서 연다"
    text = raw.decode("utf-8-sig")
    assert "F31C28" in text          # 없음
    assert "FE103C" not in text      # 찾음
    assert "MO388" not in text       # 조회실패 — 판정이 아니다
    # 엑셀이 한 줄로 읽지 않도록 진짜 줄바꿈이어야 한다 (문자 그대로의 역슬래시 r n 금지)
    assert len(text.splitlines()) >= 2


def test_the_missing_coa_list_states_the_rows_it_could_not_judge(page):
    """⛔ 화면에만 있는 경고는 파일이 손을 떠나는 순간 사라진다."""
    _open(page)
    assert "조회실패 1건" in _csv(page)


def test_a_comma_in_a_product_name_does_not_shift_a_column(page):
    _open(page)
    assert '"크림, 75ml"' in _csv(page)


def test_the_page_refuses_an_empty_missing_coa_list(page):
    """모든 행에 COA 가 있으면 빈 파일을 주지 않고 그렇다고 말한다."""
    page.set_content(PAGE.read_text(encoding="utf-8").replace(
        '<script src="/static/coa_finder.js"></script>',
        "<script>" + SCRIPT.read_text(encoding="utf-8") + "</script>"))
    only_found = [dict(ROWS[0], index=1, total=1)]
    frames = [f"event: row\ndata: {json.dumps(only_found[0], ensure_ascii=False)}\n\n",
              "event: done\ndata: " + json.dumps(
                  dict(DONE, total=1,
                       counts={"찾음": 1, "여러건": 0, "없음": 0,
                               "확인필요": 0, "조회실패": 0}),
                  ensure_ascii=False) + "\n\n"]
    page.evaluate(_STUBS, frames)
    page.fill("#cf-paste", "x")
    page.click("#cf-run")
    page.wait_for_function("() => !document.getElementById('cf-download').disabled")
    page.click("#cf-dl-none")
    assert "비어 있습니다" in page.locator("#cf-error").inner_text()
    assert page.evaluate("() => window.__saved.length") == 0


# ── 후보가 여러 건일 때 하나만 골라 받기 ─────────────────────────────────
#
# 여기서 잡으려는 것도 전부 **에러 없이 잘못 보이는** 종류다:
# - 전체선택이 파일 체크박스를 못 켜면 "선택했는데 안 받아진다" (콘솔은 조용하다)
# - 제품COA 후보를 행 체크가 쓸어가면 **다른 롯트의 증명서**가 고객에게 나간다
# - 고른 것의 판정이 '찾음' 으로 바뀌면 ZIP 안 _확인필요_목록.txt 에서 빠진다


def test_multiple_candidates_each_get_their_own_checkbox(page):
    _many(page)
    assert _cell(page, 0, 4).locator("input.cf-pick-file").count() == 2
    assert _cell(page, 1, 6).locator("input.cf-pick-file-product").count() == 2


def test_a_lone_candidate_gets_no_checkbox(page):
    """⛔ 고를 것이 하나뿐인 칸의 체크박스는 잘못된 질문을 만든다."""
    _many(page)
    assert _cell(page, 0, 5).locator("input.cf-pick-file").count() == 0


def test_the_row_checkbox_turns_every_candidate_on(page):
    """반대 방향 — 고르는 기능을 넣느라 행 단위 받기를 부수면 안 된다."""
    _many(page)
    _cell(page, 0, 0).locator("input.cf-pick").check()
    page.click("#cf-download")
    page.wait_for_function("() => window.__downloads.length === 1")
    assert sorted(_ids(page)) == ["c1", "c2", "m1"]


def test_select_all_reaches_the_candidates_too(page):
    """⛔ `.checked` 를 코드로 바꾸면 change 가 안 난다 — 그 조용한 실패."""
    _many(page)
    page.check("#cf-all")
    page.click("#cf-download")
    page.wait_for_function("() => window.__downloads.length === 1")
    assert sorted(_ids(page)) == ["c1", "c2", "m1"]


def test_unchecking_one_candidate_leaves_it_out(page):
    _many(page)
    _cell(page, 0, 0).locator("input.cf-pick").check()
    _cell(page, 0, 4).locator("input.cf-pick-file").nth(1).uncheck()
    page.click("#cf-download")
    page.wait_for_function("() => window.__downloads.length === 1")
    assert sorted(_ids(page)) == ["c1", "m1"]


def test_the_page_says_how_many_candidates_it_left_out(page):
    """⛔ 선택보다 작아진 ZIP 을 조용히 넘기지 않는다."""
    _many(page)
    _cell(page, 0, 0).locator("input.cf-pick").check()
    _cell(page, 0, 4).locator("input.cf-pick-file").nth(1).uncheck()
    page.click("#cf-download")
    page.wait_for_function("() => window.__downloads.length === 1")
    assert "1건" in page.locator("#cf-picked").inner_text()


def test_a_hand_picked_candidate_still_carries_its_verdict(page):
    """⛔ 사람이 골랐다고 확정이 되지는 않는다 — ZIP 의 확인필요 목록이 여기서 나온다."""
    _many(page)
    _cell(page, 0, 0).locator("input.cf-pick").check()
    _cell(page, 0, 4).locator("input.cf-pick-file").nth(1).uncheck()
    page.click("#cf-download")
    page.wait_for_function("() => window.__downloads.length === 1")
    items = page.evaluate("() => window.__downloads[0].items")
    picked = [i for i in items if i["file_id"] == "c1"][0]
    assert picked["status"] == "여러건"


def test_select_all_never_reaches_a_product_coa_candidate(page):
    """⛔ 다른 롯트의 증명서다 — 행 체크도 전체선택도 닿으면 안 된다."""
    _many(page)
    page.check("#cf-all")
    boxes = _cell(page, 1, 6).locator("input.cf-pick-file-product")
    assert boxes.nth(0).is_checked() is False
    assert boxes.nth(1).is_checked() is False
    page.click("#cf-download")
    page.wait_for_function("() => window.__downloads.length === 1")
    assert "p1" not in _ids(page) and "p2" not in _ids(page)


def test_a_product_coa_candidate_can_be_picked_one_at_a_time(page):
    _many(page)
    _cell(page, 1, 6).locator("input.cf-pick-file-product").nth(0).check()
    page.click("#cf-download")
    page.wait_for_function("() => window.__downloads.length === 1")
    assert _ids(page) == ["p1"]


def test_the_cell_checkbox_still_takes_every_product_candidate(page):
    _many(page)
    _cell(page, 1, 6).locator("input.cf-pick-product").check()
    page.click("#cf-download")
    page.wait_for_function("() => window.__downloads.length === 1")
    assert sorted(_ids(page)) == ["p1", "p2"]


def test_unpicked_product_candidates_are_not_announced_as_left_out(page):
    """⛔ 기본이 꺼짐인 칸을 매번 'N건 제외' 로 알리면 그 안내는 소음이 된다."""
    _many(page)
    _cell(page, 0, 0).locator("input.cf-pick").check()
    page.click("#cf-download")
    page.wait_for_function("() => window.__downloads.length === 1")
    assert page.locator("#cf-picked").count() == 0


def test_a_candidate_line_shows_its_size_and_date(page):
    """열지 않고도 고를 수 있어야 한다 — 그러라고 붙인 값이다."""
    _many(page)
    text = _cell(page, 0, 4).inner_text()
    assert "2.0MB" in text
    assert "2026-08-14" in text


def test_a_candidate_without_a_date_shows_only_its_size(page):
    """⛔ 모르는 값을 오늘 날짜로 채우면 화면이 조용히 거짓말을 한다."""
    _many(page)
    metas = _cell(page, 0, 4).locator(".cf-meta")
    assert metas.nth(1).inner_text().strip() == "50KB"


def test_a_lone_file_gets_no_size_line(page):
    """고를 것이 없는 칸에는 붙이지 않는다 — 화면만 시끄러워진다."""
    _many(page)
    assert _cell(page, 0, 5).locator(".cf-meta").count() == 0
