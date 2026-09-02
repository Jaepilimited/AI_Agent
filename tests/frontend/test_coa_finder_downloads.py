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


def _open(page):
    html = PAGE.read_text(encoding="utf-8").replace(
        '<script src="/static/coa_finder.js"></script>',
        "<script>" + SCRIPT.read_text(encoding="utf-8") + "</script>")
    page.set_content(html)
    page.evaluate(_STUBS, _frames())
    page.fill("#cf-paste", "SKU\tDESCRIPTION\tLOT")
    page.click("#cf-run")
    page.wait_for_function("() => !document.getElementById('cf-download').disabled")
    return page


def _kinds(page, index=0):
    items = page.evaluate("(i) => window.__downloads[i].items", index)
    return [it["kind"] for it in items]


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
