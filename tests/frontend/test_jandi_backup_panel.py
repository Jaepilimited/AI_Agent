"""잔디 백업 도우미 — 북마크 본체를 **가짜 잔디 화면**에서 끝까지 돌린다 (2026-10-01).

진짜 잔디 대신 같은 모양의 앵귤러 서비스(`$http`·`RoomTopicList`·`FileApi`…)를 심은
페이지를 `https://team.jandi.com/app/` 에 띄우고, 저장 폴더는 브라우저 안의 OPFS 로
갈아 끼운다. 잡으려는 것:
- 패널이 뜨고 범위를 고른 뒤 끝까지 가는가 (사용자 클릭 → 폴더 선택 → 저장)
- 쪽 넘김이 처음 기록에서 멈추고, 열람 제한 방은 access_limit 로 남는가
- 메시지 없는 방은 API 를 부르지 않는가
- 첨부 주소를 **받기 직전에 한 번씩** 요청하는가 (미리 몰아 받지 않는다)
- 지워진 파일(404)은 실패로 적고 계속 가는가, 크기가 맞는가
- 같은 폴더로 다시 돌리면 끝난 방은 건너뛰고 실패한 파일만 다시 받는가
- 실행 중 셀라 쪽 주소로 나가는 요청이 하나도 없는가
- `용량 미리 확인` 이 아무것도 쓰지 않고 방·기록·첨부·용량을 맞게 세는가, 실행 중 받은 용량 / 예상이 보이는가
- 기준일(잔디 가입일 = JANDI 봇 대화의 첫 기록 날짜, 2026-10-06): 기준일 이전 기록·첨부는 저장도 미리 확인도
  하지 않는가, 봇 대화를 못 찾으면 시작을 막는가, 기준일 없이 받은 예전 폴더를 고르면 막는가

가짜 잔디의 JANDI 봇 대화(90000004)는 기준일을 정하려고 더한 1:1 방이다 — 그래서 1:1 이 2개, 방이 4개다.
"""

import base64
import io
import json
import struct
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (ROOT / "app/static/jandi-backup/jandi-backup.js").read_text(encoding="utf-8")
FX = json.loads((ROOT / "tests/fixtures/jandi_backup_room.json").read_text(encoding="utf-8"))

SIZES = {"7000001": 11, "7000002": 22, "7000004": 7, "5000010": 44}
WELCOME_MS = 1782862200000   # 2026-07-01 08:30 KST — JANDI 환영 메시지
CUTOFF_MS = 1782831600000    # 2026-07-01 00:00 KST — 기준일
CUTOFF_TEXT = "기준일: 2026-07-01 (잔디 가입일) — 이 날 이후 대화만 받습니다"
CUTOFF_MISSING = "잔디 가입일을 확인하지 못해 백업을 시작할 수 없습니다. 셀라 담당자에게 알려 주세요."
OLD_FOLDER = "예전 방식으로 받은 폴더입니다 — 새 빈 폴더를 고르세요"
# 첨부 종류 기본은 업무용(문서·사진) — 이모티콘까지 모두 받는 '전부' 를 기억해 둔 상태로 시작한다
ALL_FILES = "localStorage.setItem('jandiBackupFiles:80000001', JSON.stringify(['doc', 'photo', 'video', 'other', 'sticker']))"

FAKE_JANDI = """<!doctype html><html><head><meta charset="utf-8"><title>대화 | TESTTEAM</title></head>
<body><div id="app">잔디 흉내</div><script>
const FX = __FX__;
const SIZES = __SIZES__;
const log = window.__log = {pages: [], urls: [], getImageAt: [], fetchAt: []};
const recs = FX.records;
// JANDI 봇 대화 — 첫 장에는 최신 기록만 준다 (첫 기록은 firstLinkId 로 한 장 더 불러야 나온다)
const WELCOME = [
  {id: 9500, time: __WELCOME_MS__, fromEntity: 2002, status: 'created', message: {id: 9500, writerId: 2002, contentType: 'text', attachments: [], content: {body: '잔디 팀에 오신 것을 환영합니다~'}}},
  {id: 9501, time: __WELCOME_MS__ + 60000, fromEntity: 2002, status: 'created', message: {id: 9501, writerId: 2002, contentType: 'text', attachments: [], content: {body: '이 JANDI 채팅은 다른 누구에게도 보여지지 않습니다!'}}}
];
function page(roomId, params) {
  if (String(roomId) === '90000003') return {roomId: 90000003, records: [{id: 9100, time: 1782864000000, fromEntity: 1001, status: 'event', info: {eventType: 'join'}}], firstLinkId: 9000, lastLinkId: 9100, isLimited: true};
  if (String(roomId) === '90000004') {
    const rs = params.linkId ? WELCOME.filter(r => r.id < params.linkId) : WELCOME.slice(-1);
    return {roomId: 90000004, records: rs, firstLinkId: 9500, lastLinkId: 9501, isLimited: false};
  }
  // window.__old = 기준일 무렵의 더 오래된 기록, window.__new = 최신 쪽에 붙는 기록 (시험마다 넣는다)
  const all = (window.__old || []).concat(recs, window.__new || []);
  const firstLinkId = Math.min(...all.map(r => r.id));
  const lastLinkId = Math.max(...all.map(r => r.id));
  if (!params.linkId) return {roomId: FX.roomId, records: recs.slice(5).concat(window.__new || []), firstLinkId, lastLinkId, isLimited: false};
  return {roomId: FX.roomId, records: all.filter(r => r.id < params.linkId), firstLinkId, lastLinkId, isLimited: false};
}
const services = {
  $http: {get(url, cfg) {
    log.pages.push({url, params: cfg.params});
    const m = /teams\\/(\\d+)\\/rooms\\/(\\d+)\\/messages$/.exec(url);
    if (!m || m[1] !== '80000001') return Promise.reject({status: 400});
    return new Promise(r => setTimeout(() => r({data: page(m[2], cfg.params)}), window.__slow || 0));
  }},
  RoomTopicList: {
    toJSON() { return [
      {id: 90000001, name: FX.roomName, type: 'privateGroup', viewType: 'chat', teamId: 80000001, lastLinkId: 6000011},
      {id: 90000009, name: '안 들어간 토픽', type: 'channel', teamId: 80000001, lastLinkId: 5}
    ]; },
    isJoined(id) { return id !== 90000009; }
  },
  RoomChatDmList: {_data: {entityId: {list: [
    {id: 90000002, type: 'chat', name: 'Lee O\\'Hara_이하라', teamId: 80000001, lastLinkId: 0, members: [1001, 1002]},
    {id: 90000004, type: 'chat', name: '', teamId: 80000001, lastLinkId: 9501, members: [{id: 1001}, {id: 2002}]}
  ]}}},
  RoomChatGroupList: {_data: {list: [{id: 90000003, type: 'groupChat', name: '', teamId: 80000001, lastLinkId: 9100, members: [1001, 1002, 1003]}]}},
  UserList: {toJSON() { return Object.values(FX.members); }},
  // window.__noJandiBot = 봇 목록에 JANDI 가 없다 (기준일을 못 찾는 계정)
  BotList: {toJSON() { return [{id: 2001, name: '잔디봇', status: 'enabled'}].concat(window.__noJandiBot ? [] : [{id: 2002, name: 'JANDI', status: 'enabled'}]); }},
  FileApi: {getImage(opts) {
    log.getImageAt.push({id: String(opts.fileId), t: performance.now(), mode: opts.mode, hasContent: !!opts.content});
    const id = String(opts.fileId);
    if (!SIZES[id]) return Promise.reject({status: 404});
    return Promise.resolve({data: {downloadUrl: 'https://files.jandi.com/dl/' + id + '?Expires=60'}});
  }}
};
window.angular = {element() { return {injector() { return {
  has(n) { return n in services; },
  get(n) { if (!(n in services)) throw new Error('no ' + n); return services[n]; }
}; }}; }};
</script></body></html>"""

INIT = """
window.showDirectoryPicker = async () => {
  const root = await navigator.storage.getDirectory();
  return root.getDirectoryHandle('picked', {create: true});
};
window.__dumpOpfs = async () => {
  const out = {};
  async function walk(dir, prefix) {
    for await (const [name, h] of dir.entries()) {
      const p = prefix + name;
      if (h.kind === 'directory') await walk(h, p + '/');
      else {
        const f = await h.getFile();
        out[p] = {size: f.size, text: f.size < 200000 ? await f.text() : null};
        if (p.endsWith('.docx')) { const b = new Uint8Array(await f.arrayBuffer()); let s = ''; for (const x of b) s += String.fromCharCode(x); out[p].b64 = btoa(s); }
      }
    }
  }
  await walk(await (await navigator.storage.getDirectory()).getDirectoryHandle('picked'), '');
  return out;
};
const _fetch = window.fetch;
window.fetch = function (url, opts) {
  (window.__log || (window.__log = {fetchAt: []})).fetchAt.push({url: String(url), t: performance.now()});
  return _fetch.call(this, url, opts);
};
"""


def _fulfill(route):
    url = route.request.url
    if url.startswith("https://team.jandi.com/app"):
        body = (FAKE_JANDI.replace("__FX__", json.dumps(FX).replace("</", "<\/"))
                .replace("__SIZES__", json.dumps(SIZES)).replace("__WELCOME_MS__", str(WELCOME_MS)))
        return route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)
    if url.startswith("https://files.jandi.com/dl/"):
        fid = url.split("/dl/")[1].split("?")[0]
        if fid in PHOTOS:   # 진짜 그림 (워드에 넣는 사진)
            return route.fulfill(status=200, content_type="application/octet-stream",
                                 headers={"Access-Control-Allow-Origin": "*"}, body=PHOTOS[fid])
        return route.fulfill(status=200, content_type="application/octet-stream",
                             headers={"Access-Control-Allow-Origin": "*"}, body=b"x" * SIZES[fid])
    if url == "https://jandi-box.com/files-sticker/155/2":
        return route.fulfill(status=200, content_type="image/png",
                             headers={"Access-Control-Allow-Origin": "*"}, body=b"PNG!!")
    if url.startswith("https://jandi-box.com/files-sticker/"):
        return route.fulfill(status=404, headers={"Access-Control-Allow-Origin": "*"}, body=b"")
    return route.fulfill(status=599, body=b"unexpected")


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def _start(page, requests, before=None, wait_cutoff=True):
    page.on("request", lambda r: requests.append(r.url))
    page.route("**/*", _fulfill)
    page.add_init_script(INIT)
    page.goto("https://team.jandi.com/app/#!/room/90000001")
    if before:
        page.evaluate(before)
    page.evaluate(SCRIPT)   # 사용자가 소스를 그대로 붙여 넣어도 돈다 — 북마크도 같은 본문이다
    host = page.locator("#__jandi-backup-host")
    host.locator("text=토픽 1 / 1:1 2 / 그룹 1").wait_for()
    if wait_cutoff:
        host.locator(f"text={CUTOFF_TEXT}").wait_for()
    return host


def test_full_backup_then_resume(browser):
    ctx = browser.new_context()
    page = ctx.new_page()
    requests = []
    host = _start(page, requests, before=ALL_FILES)   # 스티커 받기·실패까지 보려고 '전부' (기본은 업무용)
    host.locator("button[data-act=start]").click()
    host.locator("text=백업을 마쳤습니다").wait_for(timeout=20000)
    files = page.evaluate("window.__dumpOpfs()")
    log = page.evaluate("window.__log")
    state = page.evaluate("JSON.parse(JSON.stringify(window.__jandiBackup.state.progress))")

    roots = {k.split("/")[0] for k in files}
    assert len(roots) == 1
    top = roots.pop()
    assert top.startswith("잔디백업_TESTTEAM_")
    for name in ("index.html", "summary.json", "README.txt", "backup_state.json"):
        assert f"{top}/{name}" in files, name
    assert json.loads(files[f"{top}/backup_state.json"]["text"])["finishedAt"]

    room = f"{top}/rooms/비공개토픽_90000001_[테스트] 기획 _팀_ 방"
    for name in ("index.html", "messages.csv", "messages.txt", "raw.json", "files_manifest.json", "done.json"):
        assert f"{room}/{name}" in files, name
    raw = json.loads(files[f"{room}/raw.json"]["text"])
    assert raw["stopReason"] == "first_link_reached"
    assert len(raw["records"]) == 11 and len(raw["pages"]) == 2
    assert raw["members"]["1001"]["name"] == "Kim Minsu_김민수"
    manifest = {str(f["id"]): f for f in json.loads(files[f"{room}/files_manifest.json"]["text"])}
    assert manifest["7000003"]["status"] == "failed" and "404" in manifest["7000003"]["error"]
    assert manifest["sticker_160_11"]["status"] == "failed"
    for fid, size in SIZES.items():
        f = manifest[fid]
        assert f["status"] == "downloaded" and f["size"] == size and len(f["sha256"]) == 64
        assert files[f"{room}/{f['path']}"]["size"] == size
    assert files[f"{room}/attachments/sticker_155_2_sticker_155_2.png"]["size"] == 5
    assert "7000003_지워진 파일.pdf" not in " ".join(files), "실패한 파일의 빈 껍데기를 남기지 않는다"
    viewer = files[f"{room}/index.html"]["text"]
    assert viewer.count("<article ") == 11 and "지워진 파일.pdf — 미저장" in viewer

    # 메시지 없는 1:1 방은 API 를 부르지 않고 빈 방으로 남는다
    assert not any("/rooms/90000002/" in p["url"] for p in log["pages"])
    summary = {r["id"]: r for r in json.loads(files[f"{top}/summary.json"]["text"])}
    assert summary["90000002"]["stop"] == "no_messages" and summary["90000002"]["records"] == 0
    # 열람 제한 방
    assert summary["90000003"]["stop"] == "access_limit"
    assert "90000009" not in summary, "참여하지 않은 토픽은 받지 않는다"
    assert summary["90000001"]["saved"] == 5 and summary["90000001"]["files"] == 7

    # 첨부 주소는 받기 직전에 — 각 다운로드 바로 앞에 그 파일의 주소 요청이 있다
    for g in log["getImageAt"]:
        fetches = [f for f in log["fetchAt"] if f["url"].startswith("https://files.jandi.com/dl/" + g["id"] + "?")]
        if fetches:
            assert 0 <= fetches[0]["t"] - g["t"] < 5000
        assert g["mode"] == "ATTACHMENT" and g["hasContent"]
    assert state["filesFailed"] == 2   # 지워진 파일(404) · 404 스티커
    # 미리 확인 없이 돌려도 방을 다 모으면 예상 용량이 다 찬다 (스티커는 크기를 몰라 예상에 없다)
    assert state["bytesExpected"] == sum(SIZES.values()) + 33 and state["roomsUnsized"] == 0
    assert "/ 예상 117B" in host.locator(".bd").inner_text()
    assert all(("jandi.com" in u) or ("jandi-box.com" in u) for u in requests), \
        [u for u in requests if "jandi" not in u]
    # 기준일은 README · 전체 목록 · 상태 파일 · 방 원본에 남는다 (예전 폴더를 가려내는 근거)
    assert json.loads(files[f"{top}/backup_state.json"]["text"])["cutoff"] == "2026-07-01"
    assert "기준일: 2026-07-01 (잔디 가입일)" in files[f"{top}/README.txt"]["text"]
    assert "기준일: 2026-07-01 (잔디 가입일)" in files[f"{top}/index.html"]["text"]
    assert raw["cutoff"] == "2026-07-01"
    # 기준일은 JANDI 대화의 첫 기록 — firstLinkId(9500) 다음 id 로 한 장 더 불렀다
    assert {"count": 100, "type": "old", "linkId": 9501} in [p["params"] for p in log["pages"] if "/rooms/90000004/" in p["url"]]

    # 같은 폴더로 다시 — 끝난 방은 건너뛰고, 실패한 파일만 다시 요청한다
    page.evaluate("window.__log.getImageAt = []; window.__log.pages = []")
    page.reload()
    page.evaluate(SCRIPT)
    host = page.locator("#__jandi-backup-host")
    host.locator(f"text={CUTOFF_TEXT}").wait_for()
    host.locator("button[data-act=start]").click()
    host.locator("text=백업을 마쳤습니다").wait_for(timeout=20000)
    log2 = page.evaluate("window.__log")
    # 패널을 열 때마다 기준일을 확인하느라 JANDI 대화(90000004)의 첫 기록은 다시 읽는다
    assert [p for p in log2["pages"] if "/rooms/90000004/" not in p["url"]] == [], "끝난 방의 대화를 다시 읽지 않는다"
    assert sorted(g["id"] for g in log2["getImageAt"]) == ["7000003"]
    files2 = page.evaluate("window.__dumpOpfs()")
    assert len({k.split("/")[0] for k in files2}) == 1, "같은 날 다시 돌리면 같은 폴더에 이어 쓴다"
    ctx.close()


def test_off_session_message_and_reopen(browser):
    """앵귤러가 없으면 친절한 문장, 두 번 누르면 패널을 하나만."""
    ctx = browser.new_context()
    page = ctx.new_page()
    page.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html",
                                           body="<html><head><title>JANDI</title></head><body>login</body></html>"))
    page.goto("https://team.jandi.com/app/")
    page.evaluate(SCRIPT)
    host = page.locator("#__jandi-backup-host")
    host.locator("text=잔디 앱이 아직 다 열리지 않았거나").wait_for()
    host.locator("button.x").click()
    page.evaluate(SCRIPT)
    assert page.locator("#__jandi-backup-host").count() == 1
    assert page.locator("#__jandi-backup-host .p").is_visible(), "닫은 패널이 다시 열린다"
    ctx.close()


def test_pause_and_stop_leave_a_resumable_folder(browser):
    """일시정지 버튼이 0.7초마다 다시 그려져도 눌리고, 중지하면 끝나지 않은 방에 done.json 이 없다."""
    ctx = browser.new_context()
    page = ctx.new_page()
    page.on("dialog", lambda d: d.accept())
    host = _start(page, [])
    page.evaluate("window.__slow = 1500")
    host.locator("button[data-act=start]").click()
    # 미리 확인 없이 시작하면 예상 용량은 모은 방만큼 — 그렇다고 적는다
    host.locator("text=지금까지 확인된 용량").wait_for()
    host.locator("button[data-act=pause]").click()
    host.locator("text=일시정지됨").wait_for()
    assert page.evaluate("window.__jandiBackup.state.paused") is True
    host.locator("button[data-act=pause]").click()
    assert page.evaluate("window.__jandiBackup.state.paused") is False
    host.locator("button[data-act=stop]").click()
    host.locator("text=중지했습니다").wait_for(timeout=20000)
    files = page.evaluate("window.__dumpOpfs()")
    top = next(iter(files)).split("/")[0]
    state = json.loads(files[f"{top}/backup_state.json"]["text"])
    assert state["finishedAt"] is None, "중지한 백업은 끝난 것으로 적지 않는다 — 다음 날에도 이어 쓴다"
    assert not any(k.endswith("90000001_[테스트] 기획 _팀_ 방/done.json") for k in files)
    assert f"{top}/index.html" in files and f"{top}/README.txt" in files
    ctx.close()


def test_cella_page_builds_a_bookmarklet_that_runs_in_jandi(browser):
    """셀라 화면이 만든 북마크 주소를 그대로 잔디 탭에서 실행하면 패널이 뜬다 (주소 인코딩 왕복)."""
    static = ROOT / "app/static"

    def serve(route):
        url = route.request.url
        if url == "https://cella.test/jandi-backup":
            return route.fulfill(status=200, content_type="text/html; charset=utf-8",
                                 body=(static / "jandi_backup.html").read_bytes())
        if url.startswith("https://cella.test/static/"):
            rel = url.split("/static/", 1)[1].split("?")[0]
            target = static / rel
            if target.is_file():
                ctype = "application/javascript" if rel.endswith(".js") else "text/css" if rel.endswith(".css") else "application/octet-stream"
                return route.fulfill(status=200, content_type=ctype, body=target.read_bytes())
            return route.fulfill(status=404, body=b"")
        return _fulfill(route)

    ctx = browser.new_context()
    page = ctx.new_page()
    page.route("**/*", serve)
    page.add_init_script(INIT)
    page.goto("https://cella.test/jandi-backup")
    page.locator("#jb-ready", has_text="준비됐습니다").wait_for()
    href = page.get_attribute("#jb-bookmarklet", "href")
    assert href.startswith("javascript:") and len(href) > 50000
    page.click("#jb-bookmarklet")   # 누르면 실행하지 않고 끌어다 놓으라고 안내한다
    assert "끌어다 놓으세요" in page.inner_text("#jb-msg")
    assert page.evaluate("window.__jandiBackup") is None

    page.goto("https://team.jandi.com/app/")
    page.evaluate("href => { (0, eval)(decodeURIComponent(href.slice('javascript:'.length))); }", href)
    page.locator("#__jandi-backup-host").locator("text=토픽 1 / 1:1 2 / 그룹 1").wait_for()
    ctx.close()


def _cells(host):
    rows = host.locator("[data-estimate] table tr").all_inner_texts()
    return {r.split("	")[0]: r.split("	")[1:] for r in rows}


def test_precheck_counts_without_writing_then_run_shows_expected(browser):
    """용량 미리 확인: 읽기만 하고(폴더·첨부 주소 요청 없음) 종류별 숫자를 맞게 센다.
    그 다음 시작하면 실행 화면이 처음부터 예상 용량을 안다."""
    ctx = browser.new_context()
    page = ctx.new_page()
    requests = []
    host = _start(page, requests, before=ALL_FILES)
    host.locator("button[data-act=scan]", has_text="용량 미리 확인").click()
    host.locator("text=미리 확인 결과").wait_for(timeout=20000)
    cells = _cells(host)
    # fixture: 비공개 토픽 1(기록 11 · 첨부 5개 117B, 옛 방식 44B 포함 · 스티커 2) · 빈 1:1 + JANDI 대화(기록 2) · 열람 제한 그룹(기록 1)
    assert cells["토픽"] == ["1", "11", "5", "117B"], cells
    assert cells["1:1"] == ["2", "2", "0", "0B"], cells
    assert cells["그룹"] == ["1", "1", "0", "0B"], cells
    assert cells["합계"] == ["4", "14", "5", "117B"], cells
    text = host.locator(".bd").inner_text()
    assert "스티커 2개" in text and "예전 파일 1개 · 44B 포함" in text
    assert "대화만" in text and "첨부 포함" in text and "약 1분 미만" in text
    assert "20~60MB/s" in text and "회사 네트워크 기준 대략값, 실제 속도에 따라 달라집니다" in text
    log = page.evaluate("window.__log")
    assert log["getImageAt"] == [] and log["fetchAt"] == [], "미리 확인은 첨부를 건드리지 않는다"
    assert not any("/rooms/90000002/" in p["url"] for p in log["pages"]), "빈 방은 부르지 않는다"
    opfs = page.evaluate("async () => { const r = await navigator.storage.getDirectory(); const n = []; for await (const k of r.keys()) n.push(k); return n; }")
    assert opfs == [], "미리 확인은 아무것도 쓰지 않는다"
    scan = page.evaluate("JSON.parse(JSON.stringify(window.__jandiBackup.state.scan))")
    assert scan["perRoom"]["90000001"]["bytes"] == 117 and scan["failed"] == 0

    # 범위를 바꾸면 예전 숫자라고 표시하고, 되돌리면 지운다
    host.locator("input[data-k=chat]").uncheck()
    host.locator("text=고른 범위가 바뀌었습니다").wait_for()
    assert "stale" in host.locator("[data-estimate]").get_attribute("class")
    host.locator("input[data-k=chat]").check()
    assert host.locator("text=고른 범위가 바뀌었습니다").count() == 0

    # 시작 — 처음부터 예상 용량을 안다 (지금까지 확인된 용량 이 아니라 예상)
    page.evaluate("window.__slow = 400")
    host.locator("button[data-act=start]").click()
    host.locator("[data-bytes]", has_text="/ 예상 117B").wait_for()
    assert page.evaluate("window.__jandiBackup.state.phase") == "running"
    assert "지금까지 확인된 용량" not in host.locator(".bd").inner_text()
    assert host.locator("[data-speed]").count() == 1 and host.locator("[data-bytes-bar]").count() == 1
    host.locator("text=백업을 마쳤습니다").wait_for(timeout=20000)
    # 예상은 잔디가 적어 둔 크기(서버에서 지운 7000003 의 33B 포함), 받은 용량은 실제로 받은 것 + 스티커 5B
    prog = page.evaluate("JSON.parse(JSON.stringify(window.__jandiBackup.state.progress))")
    assert prog["bytesExpected"] == 117 and prog["roomsUnsized"] == 0
    assert prog["bytes"] == sum(SIZES.values()) + 5
    assert all(("jandi.com" in u) or ("jandi-box.com" in u) for u in requests)
    ctx.close()


def test_precheck_can_be_cancelled(browser):
    ctx = browser.new_context()
    page = ctx.new_page()
    host = _start(page, [])
    page.evaluate("window.__slow = 1500")
    host.locator("button[data-act=scan]").click()
    host.locator("text=용량을 확인하는 중입니다").wait_for()
    host.locator("button[data-act=cancel-scan]").click()
    host.locator("text=용량 확인을 멈췄습니다").wait_for(timeout=20000)
    assert page.evaluate("window.__jandiBackup.state.scan") is None
    assert page.evaluate("window.__jandiBackup.state.phase") == "intro"
    assert host.locator("button[data-act=start]").is_enabled()
    ctx.close()


# ── 기준일 (잔디 가입일) ─────────────────────────────────────────────────────

OLD_AND_NEW = """
window.__old = [
  {id: 5999998, time: %(before)d, fromEntity: 1001, status: 'created', feedbackId: -1, messageId: 4000002,
   message: {id: 4000002, writerId: 1001, contentType: 'text', createdAt: null, content: {body: '입사 전 대화'},
             attachments: [{id: 7000099, contentType: 'file', content: {name: '입사 전 첨부.pdf', type: 'application/pdf', size: 9}}]}},
  {id: 5999999, time: %(cutoff)d, fromEntity: 1002, status: 'created', feedbackId: -1, messageId: 4000003,
   message: {id: 4000003, writerId: 1002, contentType: 'text', content: {body: '가입 당일 0시'}, attachments: []}}
];
window.__new = [
  {id: 6000012, time: 1782864660000, fromEntity: 1001, status: 'created', feedbackType: 'text', feedbackId: 4000001, messageId: 5000020,
   message: {id: 5000020, writerId: 1001, contentType: 'comment', feedbackId: 4000001, content: {body: '옛 글에 댓글'}, attachments: []},
   feedback: {id: 4000001, contentType: 'text', createdAt: '2026-06-01T00:00:00.000Z', content: {body: '입사 전 원본'},
              attachments: [{id: 7000098, contentType: 'file', content: {name: '입사 전 원본 첨부.txt', type: 'text/plain', size: 8}}]}}
];
""" % {"before": CUTOFF_MS - 60000, "cutoff": CUTOFF_MS}


def test_cutoff_drops_records_and_files_before_join_date(browser):
    """기준일 이전 기록·첨부·댓글 원본은 미리 확인에도, 저장(raw·csv·html·txt)에도, 첨부 요청에도 없다.
    기준일 당일 00:00 기록은 들어간다. 쪽 넘김은 기준일 이전 기록이 보이는 장에서 cutoff 로 멈춘다."""
    ctx = browser.new_context()
    page = ctx.new_page()
    host = _start(page, [], before=OLD_AND_NEW)
    assert host.locator("[data-cutoff]").inner_text() == CUTOFF_TEXT
    assert host.locator("button[data-act=start]").is_enabled()

    host.locator("button[data-act=scan]").click()
    host.locator("text=미리 확인 결과").wait_for(timeout=20000)
    # 토픽 = 기존 11 + 당일 0시 1 + 새 댓글 1. 입사 전 첨부(9B)·원본 첨부(8B)는 세지 않는다
    assert _cells(host)["토픽"] == ["1", "13", "5", "117B"]

    page.evaluate("window.__slow = 300")
    host.locator("button[data-act=start]").click()
    host.locator("[data-live] [data-cutoff]", has_text="기준일 2026-07-01 (잔디 가입일) 이후 대화만").wait_for()
    host.locator("text=백업을 마쳤습니다").wait_for(timeout=20000)
    files = page.evaluate("window.__dumpOpfs()")
    log = page.evaluate("window.__log")
    top = next(iter(files)).split("/")[0]
    room = f"{top}/rooms/비공개토픽_90000001_[테스트] 기획 _팀_ 방"
    raw = json.loads(files[f"{room}/raw.json"]["text"])
    assert raw["stopReason"] == "cutoff" and len(raw["pages"]) == 2
    assert sorted(raw["records"]) == sorted(str(i) for i in [5999999] + list(range(6000001, 6000013)))
    assert "feedback" not in raw["records"]["6000012"], "기준일 이전 원본은 떼어 낸다"
    assert raw["records"]["6000012"]["message"]["content"]["body"] == "옛 글에 댓글", "댓글 자체는 남긴다"
    assert raw["records"]["6000004"]["feedback"]["id"] == 5000002, "기준일 이후 원본은 그대로"
    everything = "".join(f["text"] or "" for f in files.values())
    for gone in ("입사 전 대화", "입사 전 원본", "입사 전 첨부", "입사 전 원본 첨부"):
        assert gone not in everything, gone
    assert "가입 당일 0시" in files[f"{room}/messages.csv"]["text"]
    assert files[f"{room}/index.html"]["text"].count("<article ") == 13
    ids = {str(f["id"]) for f in json.loads(files[f"{room}/files_manifest.json"]["text"])}
    assert not ids & {"7000098", "7000099"}
    assert not {g["id"] for g in log["getImageAt"]} & {"7000098", "7000099"}, "기준일 이전 첨부는 주소도 요청하지 않는다"
    summary = {r["id"]: r for r in json.loads(files[f"{top}/summary.json"]["text"])}
    assert summary["90000001"]["stop"] == "cutoff" and summary["90000001"]["records"] == 13
    assert "처음 기록까지 가지 못한 방 1개" in files[f"{top}/index.html"]["text"], "기준일에서 멈춘 방은 끝난 방이다 (열람 제한 그룹만)"
    ctx.close()


def test_no_jandi_bot_conversation_blocks_start_and_precheck(browser):
    """JANDI 봇 대화를 못 찾으면 기준일 없이 받지 않는다 — 시작·미리 확인 버튼이 꺼지고 안내가 보인다."""
    ctx = browser.new_context()
    page = ctx.new_page()
    requests = []
    host = _start(page, requests, before="window.__noJandiBot = true", wait_cutoff=False)
    host.locator(f"text={CUTOFF_MISSING}").wait_for()
    assert host.locator("button[data-act=start]").is_disabled()
    assert host.locator("button[data-act=scan]").is_disabled()
    assert page.evaluate("window.__jandiBackup.state.cutoff.state") == "missing"
    # 버튼을 우회해 불러도 막힌다
    page.evaluate("() => { window.showDirectoryPicker = async () => { throw new Error('부르면 안 된다'); }; }")
    host.locator("button[data-act=start]").dispatch_event("click")
    assert page.evaluate("window.__jandiBackup.state.phase") == "intro"
    opfs = page.evaluate("async () => { const r = await navigator.storage.getDirectory(); const n = []; for await (const k of r.keys()) n.push(k); return n; }")
    assert opfs == []
    assert not [u for u in requests if "/messages" in u], "봇 대화가 없으면 대화를 읽지 않는다"
    ctx.close()


def test_folder_from_old_version_is_refused(browser):
    """기준일 없이(예전 버전) 받았거나 다른 기준일로 받은 폴더를 고르면 이어 쓰지 않고 막는다."""
    ctx = browser.new_context()
    page = ctx.new_page()
    host = _start(page, [])
    for state in ({"teamId": 80000001, "finishedAt": None, "version": "2026-10-01.2"},
                  {"teamId": 80000001, "finishedAt": None, "version": "2026-10-06.1", "cutoff": "2020-01-01"}):
        page.evaluate("""async (state) => {
          const root = await (await navigator.storage.getDirectory()).getDirectoryHandle('picked', {create: true});
          const name = '잔디백업_TESTTEAM_' + window.__jandiBackup.lib.ymdKst(Date.now());
          const dir = await root.getDirectoryHandle(name, {create: true});
          const w = await (await dir.getFileHandle('backup_state.json', {create: true})).createWritable();
          await w.write(JSON.stringify(state)); await w.close();
        }""", state)
        host.locator("button[data-act=start]").click()
        host.locator(f"text={OLD_FOLDER}").wait_for(timeout=20000)
        files = page.evaluate("window.__dumpOpfs()")
        assert all(k.endswith("/backup_state.json") for k in files), files.keys()
        assert json.loads(next(iter(files.values()))["text"]) == state, "예전 폴더는 건드리지 않는다"
        host.locator("button[data-act=again]").click()
        host.locator(f"text={CUTOFF_TEXT}").wait_for()
    ctx.close()


# ── 방 고르기 (필요 없는 방 빼기, 2026-10-07) ────────────────────────────────

TOPIC = "90000001"   # 비공개 토픽 — 기록 11 · 첨부 117B (가장 큰 방)
GROUP = "90000003"   # 열람 제한 그룹 — 기록 1


def _pick(host):
    """방 고르기를 펼친다 (기본은 접혀 있다)."""
    det = host.locator("[data-pick]")
    if det.get_attribute("open") is None:
        host.locator("[data-pick-sum]").click()
    return det


def _visible_rooms(host):
    return host.locator("[data-pick-list] label:visible input").evaluate_all("els => els.map(e => e.dataset.room)")


def test_room_pick_search_exclude_run_and_remember(browser):
    """검색해서 뺀 방은 폴더가 안 생기고 README·목록·상태 파일에 '제외한 방' 으로 남는다.
    다시 열면 제외가 기억되고(없어진 id 는 버림), 같은 폴더로 이어 받을 때 이번에 뺀 방은 건너뛰되 지우지 않는다."""
    ctx = browser.new_context()
    page = ctx.new_page()
    host = _start(page, [])
    summary = host.locator("[data-pick-sum]")
    assert summary.inner_text() == "방 고르기 (4개 중 4개 받음)"
    assert host.locator("[data-pick]").get_attribute("open") is None, "기본은 접혀 있다"
    _pick(host)
    assert sorted(_visible_rooms(host)) == sorted([TOPIC, "90000002", GROUP, "90000004"])
    host.locator("[data-pick-q]").fill("기획")
    assert _visible_rooms(host) == [TOPIC], "검색은 방 이름으로"
    host.locator("[data-pick-q]").fill("PARK JIWOO")   # 그룹 방 이름 = 멤버 이름 — 대소문자 무시
    assert _visible_rooms(host) == [GROUP]
    host.locator("[data-pick-q]").fill("기획")
    host.locator("button[data-act=pick-none]").click()
    assert summary.inner_text() == "방 고르기 (4개 중 3개 받음)"
    host.locator("[data-pick-q]").fill("")
    assert len(_visible_rooms(host)) == 4, "검색을 지워도 선택 상태는 그대로"
    assert not host.locator(f"input[data-room='{TOPIC}']").is_checked()
    assert host.locator(f"input[data-room='{GROUP}']").is_checked()
    assert page.evaluate("localStorage.getItem('jandiBackupExcluded:80000001')") == json.dumps([TOPIC])

    host.locator("button[data-act=start]").click()
    host.locator("text=백업을 마쳤습니다").wait_for(timeout=20000)
    files = page.evaluate("window.__dumpOpfs()")
    log = page.evaluate("window.__log")
    top = next(iter(files)).split("/")[0]
    assert not any(f"_{TOPIC}_" in k for k in files), "뺀 방은 폴더가 생기지 않는다"
    assert not any(f"/rooms/{TOPIC}/" in p["url"] for p in log["pages"]), "뺀 방은 읽지도 않는다"
    assert log["getImageAt"] == []
    assert page.evaluate("window.__jandiBackup.state.progress.roomsTotal") == 3
    summary_rows = {r["id"] for r in json.loads(files[f"{top}/summary.json"]["text"])}
    assert summary_rows == {"90000002", GROUP, "90000004"}
    room_name = FX["roomName"]
    readme = files[f"{top}/README.txt"]["text"]
    assert "제외한 방 1개" in readme and room_name in readme
    index = files[f"{top}/index.html"]["text"]
    assert "제외한 방 1개" in index and room_name.replace("<", "&lt;").replace(">", "&gt;") in index
    assert json.loads(files[f"{top}/backup_state.json"]["text"])["excluded"] == [TOPIC]

    # 새로 열면 기억한다 — 이제 없는 방 id 는 버린다
    page.evaluate(f"localStorage.setItem('jandiBackupExcluded:80000001', JSON.stringify(['{TOPIC}', '123456']))")
    page.evaluate("window.__log.pages = []; window.__log.getImageAt = []")
    page.reload()
    page.evaluate(SCRIPT)
    host = page.locator("#__jandi-backup-host")
    host.locator(f"text={CUTOFF_TEXT}").wait_for()
    assert host.locator("[data-pick-sum]").inner_text() == "방 고르기 (4개 중 3개 받음)"
    assert page.evaluate("JSON.parse(JSON.stringify(window.__jandiBackup.state.opts.excluded))") == {TOPIC: True}

    # 같은 폴더로 이어 받기: 토픽을 다시 넣고 그룹을 뺀다 → 토픽만 새로 받고, 받아 둔 그룹 폴더는 그대로
    _pick(host)
    host.locator(f"input[data-room='{TOPIC}']").check()
    host.locator(f"input[data-room='{GROUP}']").uncheck()
    assert page.evaluate("localStorage.getItem('jandiBackupExcluded:80000001')") == json.dumps([GROUP])
    host.locator("button[data-act=start]").click()
    host.locator("text=백업을 마쳤습니다").wait_for(timeout=20000)
    files2 = page.evaluate("window.__dumpOpfs()")
    log2 = page.evaluate("window.__log")
    assert len({k.split("/")[0] for k in files2}) == 1
    assert any(k.endswith(f"_{TOPIC}_[테스트] 기획 _팀_ 방/done.json") for k in files2)
    group_files = {k: v for k, v in files.items() if f"_{GROUP}_" in k}
    assert group_files and all(files2.get(k) == v for k, v in group_files.items()), "전에 받은 방 폴더는 지우지 않는다"
    assert not any(f"/rooms/{GROUP}/" in p["url"] for p in log2["pages"])
    state = json.loads(files2[f"{top}/backup_state.json"]["text"])
    assert state["excluded"] == [GROUP] and state["finishedAt"]
    ctx.close()


def test_room_pick_with_precheck_numbers_and_all_excluded(browser):
    """미리 확인 후 방 옆에 기록·첨부 숫자가 보이고 큰 방이 위로 온다. 빼면 합계가 그만큼 줄고 다시 확인하라고 하지 않는다.
    확인할 때 빠져 있던 방을 다시 넣으면 다시 확인하라고 한다. 모두 빼면 시작·미리 확인이 막힌다."""
    ctx = browser.new_context()
    page = ctx.new_page()
    host = _start(page, [])
    _pick(host)
    assert host.locator("[data-pick-list] .sz").all_inner_texts() == ["", "", "", ""], "미리 확인 전에는 숫자가 없다"

    host.locator("button[data-act=scan]").click()
    host.locator("text=미리 확인 결과").wait_for(timeout=20000)
    assert _cells(host)["합계"] == ["4", "14", "5", "117B"]
    _pick(host)
    rows = host.locator("[data-pick-list] label")
    assert rows.first.locator("input").get_attribute("data-room") == TOPIC, "용량 큰 방이 맨 위"
    assert rows.first.locator(".sz").inner_text() == "기록 11건 · 첨부 117B"
    assert rows.first.locator(".kd").inner_text() == "비공개"
    assert host.locator(f"[data-pick-list] label:has(input[data-room='{GROUP}']) .sz").inner_text() == "기록 1건 · 첨부 0B"

    host.locator(f"input[data-room='{TOPIC}']").uncheck()
    cells = _cells(host)
    assert cells["토픽"] == ["0", "0", "0", "0B"] and cells["합계"] == ["3", "3", "0", "0B"], cells
    assert "stale" not in host.locator("[data-estimate]").get_attribute("class"), "빼기만 하면 다시 확인할 필요 없다"
    host.locator(f"input[data-room='{TOPIC}']").check()
    assert _cells(host)["합계"] == ["4", "14", "5", "117B"]

    # 그룹을 빼고 다시 확인 → 그룹은 확인하지 않았다 → 다시 넣으면 다시 확인하라고 한다
    host.locator(f"input[data-room='{GROUP}']").uncheck()
    host.locator("button[data-act=scan]", has_text="용량 다시 확인").click()
    host.locator("[data-estimate]:not(.stale)").wait_for(timeout=20000)
    assert _cells(host)["합계"] == ["3", "13", "5", "117B"]
    assert GROUP not in page.evaluate("Object.keys(window.__jandiBackup.state.scan.perRoom)")
    assert host.locator("[data-pick]").get_attribute("open") is not None, "펼친 상태를 기억한다"
    host.locator(f"input[data-room='{GROUP}']").check()
    host.locator("text=미리 확인하지 않은 방을 다시 넣었습니다").wait_for()
    assert "stale" in host.locator("[data-estimate]").get_attribute("class")

    # 종류를 끄면 그 방은 목록에서 사라진다
    host.locator("input[data-k=groupChat]").uncheck()
    assert host.locator(f"input[data-room='{GROUP}']").count() == 0
    assert host.locator("[data-pick-sum]").inner_text() == "방 고르기 (3개 중 3개 받음)"
    host.locator("input[data-k=groupChat]").check()

    # 모두 빼면 시작·미리 확인이 막힌다
    host.locator("button[data-act=pick-none]").click()
    assert host.locator("[data-pick-sum]").inner_text() == "방 고르기 (4개 중 0개 받음)"
    page.evaluate("() => { window.showDirectoryPicker = async () => { throw new Error('부르면 안 된다'); }; }")
    host.locator("button[data-act=start]").click()
    host.locator("[data-msg]", has_text="저장할 방이 없습니다. 방 고르기에서 받을 방을 하나 이상 체크하세요.").wait_for()
    host.locator("button[data-act=scan]").click()
    host.locator("[data-msg]", has_text="확인할 방이 없습니다").wait_for()
    assert page.evaluate("window.__jandiBackup.state.phase") == "intro"
    opfs = page.evaluate("async () => { const r = await navigator.storage.getDirectory(); const n = []; for await (const k of r.keys()) n.push(k); return n; }")
    assert opfs == []
    ctx.close()


# ── 첨부 종류 (대화만 / 업무용 / 전부 + 종류 5개, 2026-10-07) ───────────────

# 토픽에 동영상·압축 첨부가 붙은 기록 하나를 더한다 (가짜 잔디는 SIZES 에 없는 파일을 404 로 준다)
VIDEO_AND_ZIP = """
window.__new = [
  {id: 6000013, time: 1782864700000, fromEntity: 1001, status: 'created', feedbackId: -1, messageId: 5000030,
   message: {id: 5000030, writerId: 1001, contentType: 'text', content: {body: '행사 영상'},
             attachments: [{id: 7000201, contentType: 'file', content: {name: '행사.mp4', type: 'video/mp4', size: 1000}},
                           {id: 7000202, contentType: 'file', content: {name: '자료.zip', type: 'application/zip', size: 500}}]}}
];
"""


def _file_boxes(host):
    return {c: host.locator(f"input[data-fc={c}]").is_checked() for c in ("doc", "photo", "video", "other", "sticker")}


def test_file_kinds_presets_precheck_and_resume(browser):
    """기본은 업무용(문서·사진). 프리셋·체크박스를 바꾸면 미리 확인 합계가 다시 확인 없이 바뀐다.
    업무용으로 받으면 동영상·압축·이모티콘은 요청하지 않고 뷰어에 (받지 않음: 동영상) 으로 남는다.
    '전부' 로 받다 실패한 동영상·이모티콘은, 같은 폴더를 업무용으로 이어 받을 때 다시 요청하지 않는다."""
    ctx = browser.new_context()
    page = ctx.new_page()
    requests = []
    host = _start(page, requests, before=VIDEO_AND_ZIP)
    name = host.locator("[data-preset-name]")
    assert name.inner_text() == "업무용"
    assert _file_boxes(host) == {"doc": True, "photo": True, "video": False, "other": False, "sticker": False}
    assert host.locator("[data-preset=work]").get_attribute("aria-pressed") == "true"

    host.locator("button[data-act=scan]").click()
    host.locator("text=미리 확인 결과").wait_for(timeout=20000)
    assert _cells(host)["합계"] == ["4", "15", "5", "117B"]
    scans = page.evaluate("window.__log.pages.length")

    host.locator("[data-preset=all]").click()
    assert name.inner_text() == "전부" and all(_file_boxes(host).values())
    assert _cells(host)["합계"] == ["4", "15", "7", "2KB"], "동영상 1000B + 압축 500B 가 바로 더해진다"
    assert "스티커 2개" in host.locator("[data-estimate]").inner_text()
    host.locator("input[data-fc=video]").uncheck()
    assert name.inner_text() == "직접 고름"
    assert _cells(host)["합계"] == ["4", "15", "6", "617B"]
    host.locator("[data-preset=messages]").click()
    assert name.inner_text() == "대화만" and not any(_file_boxes(host).values())
    assert _cells(host)["합계"] == ["4", "15", "0", "0B"]
    assert "stale" not in host.locator("[data-estimate]").get_attribute("class")
    assert page.evaluate("window.__log.pages.length") == scans, "종류를 바꿔도 다시 읽지 않는다"
    assert page.evaluate("localStorage.getItem('jandiBackupFiles:80000001')") == "[]"

    # 전부로 받는다 — 동영상·압축은 가짜 잔디에 없어서(404) 실패, 404 스티커도 실패
    host.locator("[data-preset=all]").click()
    host.locator("button[data-act=start]").click()
    host.locator("text=백업을 마쳤습니다").wait_for(timeout=20000)
    files = page.evaluate("window.__dumpOpfs()")
    top = next(iter(files)).split("/")[0]
    room = f"{top}/rooms/비공개토픽_90000001_[테스트] 기획 _팀_ 방"
    failed = {str(f["id"]) for f in json.loads(files[f"{room}/files_manifest.json"]["text"]) if f["status"] == "failed"}
    assert failed == {"7000003", "7000201", "7000202", "sticker_160_11"}
    assert "받은 첨부 종류: 문서, 사진, 동영상, 기타, 이모티콘" in files[f"{top}/README.txt"]["text"]

    # 같은 폴더를 업무용으로 이어 받기 — 꺼진 종류(동영상·기타·이모티콘)는 다시 요청하지 않고, 받아 둔 이모티콘은 둔다
    page.evaluate("window.__log.getImageAt = []; window.__log.fetchAt = []")
    page.reload()
    page.evaluate(VIDEO_AND_ZIP)
    page.evaluate(SCRIPT)
    host = page.locator("#__jandi-backup-host")
    host.locator(f"text={CUTOFF_TEXT}").wait_for()
    assert host.locator("[data-preset-name]").inner_text() == "전부", "팀별로 기억한다"
    host.locator("[data-preset=work]").click()
    host.locator("button[data-act=start]").click()
    host.locator("text=백업을 마쳤습니다").wait_for(timeout=20000)
    log = page.evaluate("window.__log")
    assert sorted(g["id"] for g in log["getImageAt"]) == ["7000003"], "켜진 종류의 실패 파일만 다시"
    assert not [f for f in log["fetchAt"] if "files-sticker" in f["url"]]
    files2 = page.evaluate("window.__dumpOpfs()")
    assert files2[f"{room}/attachments/sticker_155_2_sticker_155_2.png"]["size"] == 5, "이미 받은 파일은 지우지 않는다"
    viewer = files2[f"{room}/index.html"]["text"]
    assert "행사.mp4 (받지 않음: 동영상)" in viewer and "자료.zip (받지 않음: 기타)" in viewer
    assert "(이모티콘)" in viewer
    state = json.loads(files2[f"{top}/backup_state.json"]["text"])
    assert state["fileCats"] == ["doc", "photo"]
    assert "받은 첨부 종류: 문서, 사진" in files2[f"{top}/README.txt"]["text"]
    ctx.close()


def test_file_kinds_messages_only_makes_no_attachment_folder(browser):
    """대화만 = 첨부 종류가 하나도 없다 — 첨부 주소·스티커를 부르지 않고 attachments 폴더도 만들지 않는다.
    예전 이모티콘 설정(jandiBackupStickers='1')은 업무용 + 이모티콘으로 읽는다."""
    ctx = browser.new_context()
    page = ctx.new_page()
    requests = []
    host = _start(page, requests, before="localStorage.setItem('jandiBackupStickers:80000001', '1')")
    assert host.locator("[data-preset-name]").inner_text() == "직접 고름"
    assert _file_boxes(host) == {"doc": True, "photo": True, "video": False, "other": False, "sticker": True}
    host.locator("[data-preset=messages]").click()
    host.locator("button[data-act=start]").click()
    host.locator("text=백업을 마쳤습니다").wait_for(timeout=20000)
    log = page.evaluate("window.__log")
    assert log["getImageAt"] == [] and log["fetchAt"] == []
    files = page.evaluate("window.__dumpOpfs()")
    assert not any("/attachments/" in k for k in files)
    top = next(iter(files)).split("/")[0]
    room = f"{top}/rooms/비공개토픽_90000001_[테스트] 기획 _팀_ 방"
    assert json.loads(files[f"{room}/files_manifest.json"]["text"]) == []
    viewer = files[f"{room}/index.html"]["text"]
    assert viewer.count("<article ") == 11 and "회의 사진.png (받지 않음: 사진)" in viewer
    assert "받은 첨부 종류: 없음 (대화만)" in files[f"{top}/README.txt"]["text"]
    assert host.locator("[data-bytes]").count() == 0, "대화만이면 받은 용량 줄을 보이지 않는다"
    ctx.close()


def test_room_pick_works_when_local_storage_is_blocked(browser):
    """localStorage 가 막혀도(접근하면 예외) 방 고르기는 동작한다 — 기억만 못 한다."""
    ctx = browser.new_context()
    page = ctx.new_page()
    block = "Object.defineProperty(window, 'localStorage', {get() { throw new Error('막힘'); }})"
    host = _start(page, [], before=block)
    _pick(host)
    host.locator(f"input[data-room='{GROUP}']").uncheck()
    assert host.locator("[data-pick-sum]").inner_text() == "방 고르기 (4개 중 3개 받음)"
    host.locator("button[data-act=start]").click()
    host.locator("text=백업을 마쳤습니다").wait_for(timeout=20000)
    files = page.evaluate("window.__dumpOpfs()")
    assert not any(f"_{GROUP}_" in k for k in files)
    ctx.close()


# ── 방별 워드(.docx) — 팀즈·OneDrive 에서 보기용 (2026-10-07) ─────────────────

def _png(w, h):
    """반투명 빨강 RGBA PNG — 워드에 넣을 때 흰 배경 JPEG 로 줄어야 한다."""
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes([255, 0, 0, 128]) * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


PHOTOS = {"7000301": _png(2000, 1000)}
# 토픽에 진짜 사진 한 장(현장.png, 2000×1000)을 더한다. 픽스처의 회의 사진.png 는 가짜 바이트라 그림으로 못 읽는다(→ 이름 줄만)
REAL_PHOTO = """
SIZES['7000301'] = %d;
window.__new = [
  {id: 6000014, time: 1782864800000, fromEntity: 1002, status: 'created', feedbackId: -1, messageId: 5000040,
   message: {id: 5000040, writerId: 1002, contentType: 'text', content: {body: '현장 사진\\n둘째 줄'},
             attachments: [{id: 7000301, contentType: 'file', content: {name: '현장.png', type: 'image/png', size: %d}}]}}
];
""" % (len(PHOTOS["7000301"]), len(PHOTOS["7000301"]))
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _open_docx(entry):
    z = zipfile.ZipFile(io.BytesIO(base64.b64decode(entry["b64"])))
    assert z.testzip() is None
    parts = {}
    for info in z.infolist():
        assert info.compress_type == zipfile.ZIP_STORED and info.flag_bits & 0x800
        data = z.read(info.filename)
        if info.filename.endswith((".xml", ".rels")):
            parts[info.filename] = ET.fromstring(data)   # 잘 닫힌 XML 이어야 한다
        else:
            parts[info.filename] = data
    return parts


def _paragraphs(document):
    return ["".join(t.text or "" for t in p.iter(W + "t")) for p in document.iter(W + "p")]


def test_word_docs_photos_links_and_index(browser):
    """기본으로 방마다 대화.docx 를 만든다 — zipfile·ElementTree 로 열리고, 받은 진짜 사진은 줄인 JPEG 로 media 에,
    받은 첨부는 attachments/… 상대 경로 링크, 그림으로 못 읽는 사진은 이름 줄만. 상위 목록·README 에 워드 안내와 링크."""
    ctx = browser.new_context()
    page = ctx.new_page()
    host = _start(page, [], before=REAL_PHOTO)
    assert host.locator("input[data-opt=docx]").is_checked(), "기본 켜짐"
    host.locator("button[data-act=start]").click()
    host.locator("text=백업을 마쳤습니다").wait_for(timeout=30000)
    files = page.evaluate("window.__dumpOpfs()")
    top = next(iter(files)).split("/")[0]
    room = f"{top}/rooms/비공개토픽_90000001_[테스트] 기획 _팀_ 방"
    assert files[f"{room}/attachments/7000301_현장.png"]["size"] == len(PHOTOS["7000301"]), "원본은 attachments 에 그대로"
    parts = _open_docx(files[f"{room}/대화.docx"])
    assert sorted(parts) == sorted(["[Content_Types].xml", "_rels/.rels", "word/_rels/document.xml.rels", "word/document.xml",
                                    "word/styles.xml", "docProps/core.xml", "word/media/image1.jpg"])
    jpg = parts["word/media/image1.jpg"]
    assert jpg[:3] == b"\xff\xd8\xff" and len(jpg) < len(PHOTOS["7000301"]), "줄인 JPEG"
    types = {d.get("Extension"): d.get("ContentType") for d in parts["[Content_Types].xml"]}
    assert types["jpg"] == "image/jpeg" and types["png"] == "image/png"
    rels = {r.get("Id"): r for r in parts["word/_rels/document.xml.rels"].iter(REL + "Relationship")}
    images = [r for r in rels.values() if r.get("Type").endswith("/image")]
    assert [r.get("Target") for r in images] == ["media/image1.jpg"]
    links = {r.get("Target") for r in rels.values() if r.get("Type").endswith("/hyperlink")}
    assert all(r.get("TargetMode") == "External" for r in rels.values() if r.get("Type").endswith("/hyperlink"))
    assert "attachments/7000301_%ED%98%84%EC%9E%A5.png" in links
    assert any(t.startswith("attachments/7000002_") for t in links) and not any("7000003" in t for t in links), "못 받은 첨부는 링크 없음"
    doc = parts["word/document.xml"]
    blips = [b.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
             for b in doc.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}blip")]
    assert blips == [images[0].get("Id")], "사진은 한 장 — 가짜 바이트인 회의 사진.png 는 그림 없이"
    ext = next(doc.iter("{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}extent"))
    assert (ext.get("cx"), ext.get("cy")) == ("5400000", "2700000"), "1280×640 으로 줄이고 본문 폭 15cm 에 맞춤"
    for h in doc.iter(W + "hyperlink"):
        assert h.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id") in rels
    paras = _paragraphs(doc)
    assert paras[0] == FX["roomName"]
    assert paras[1] == "TESTTEAM · 2026-07-01 ~ 2026-07-01 · 기록 12건 · 기준일 2026-07-01 이후"
    assert "2026-07-01 (수)" in paras and "현장 사진둘째 줄" in paras
    assert "📎 회의 사진.png (attachments/7000001_회의 사진.png)" in paras
    assert "📎 지워진 파일.pdf (미저장)" in paras and "(이모티콘)" in paras
    assert any(p.startswith("↳ 댓글: ") for p in paras)
    # 다른 방도 각자 대화.docx (빈 방 포함)
    for folder in {k.split("/")[2] for k in files if k.startswith(f"{top}/rooms/")}:
        assert f"{top}/rooms/{folder}/대화.docx" in files, folder
        _open_docx(files[f"{top}/rooms/{folder}/대화.docx"])
    summary = {r["id"]: r for r in json.loads(files[f"{top}/summary.json"]["text"])}
    assert summary["90000001"]["docx"] == ["대화.docx"]
    index = files[f"{top}/index.html"]["text"]
    assert index.count('class="docx"') == 4 and "팀즈·OneDrive 에 올리면 웹에서 바로 읽힙니다" in index
    assert "방별 워드: rooms/<방>/대화.docx — 팀즈·OneDrive 에 올리면 웹에서 바로 읽힙니다" in files[f"{top}/README.txt"]["text"]
    assert json.loads(files[f"{top}/backup_state.json"]["text"])["docx"] is True
    ctx.close()


def test_word_option_off_then_on_rebuilds_from_raw_without_reading(browser):
    """워드를 끄면 만들지 않고(팀별로 기억), 다시 켜고 같은 폴더로 이어 받으면 끝난 방은 raw.json 으로 워드만 만든다 — 대화는 다시 읽지 않는다."""
    ctx = browser.new_context()
    page = ctx.new_page()
    host = _start(page, [])
    host.locator("input[data-opt=docx]").uncheck()
    assert page.evaluate("localStorage.getItem('jandiBackupDocx:80000001')") == "0"
    host.locator("button[data-act=start]").click()
    host.locator("text=백업을 마쳤습니다").wait_for(timeout=20000)
    files = page.evaluate("window.__dumpOpfs()")
    top = next(iter(files)).split("/")[0]
    assert not any("/대화" in k and k.endswith(".docx") for k in files), "워드를 끄면 만들지 않는다 (첨부 .docx 는 별개)"
    assert "방별 워드" not in files[f"{top}/README.txt"]["text"] and 'class="docx"' not in files[f"{top}/index.html"]["text"]

    page.evaluate("window.__log.pages = []; window.__log.getImageAt = []")
    page.reload()
    page.evaluate(SCRIPT)
    host = page.locator("#__jandi-backup-host")
    host.locator(f"text={CUTOFF_TEXT}").wait_for()
    assert not host.locator("input[data-opt=docx]").is_checked(), "끈 것을 기억한다"
    host.locator("input[data-opt=docx]").check()
    host.locator("button[data-act=start]").click()
    host.locator("text=백업을 마쳤습니다").wait_for(timeout=20000)
    log = page.evaluate("window.__log")
    assert [p for p in log["pages"] if "/rooms/90000004/" not in p["url"]] == [], "대화를 다시 읽지 않는다"
    assert sorted(g["id"] for g in log["getImageAt"]) == ["7000003"]
    files2 = page.evaluate("window.__dumpOpfs()")
    room = f"{top}/rooms/비공개토픽_90000001_[테스트] 기획 _팀_ 방"
    paras = _paragraphs(_open_docx(files2[f"{room}/대화.docx"])["word/document.xml"])
    assert paras[1].endswith("기록 11건 · 기준일 2026-07-01 이후")
    assert sum(1 for k in files2 if k.endswith("/대화.docx")) == 4
    done = json.loads(files2[f"{room}/done.json"]["text"])
    assert done["row"]["docx"] == ["대화.docx"]
    assert files2[f"{top}/index.html"]["text"].count('class="docx"') == 4
    ctx.close()


# ── 올리기 나누기 — 회사 DLP 는 한 번에 1,000개까지 (2026-10-07) ─────────────
# 한도(lib.UPLOAD_LIMIT, 실제 900)를 3 으로 줄여 나눔을 본다

TOPIC_DIR = "비공개토픽_90000001_[테스트] 기획 _팀_ 방"
LIMIT_3 = "window.__jandiBackup.lib.UPLOAD_LIMIT = 3"
FORGET_TOPIC = """async () => {
  const root = await (await navigator.storage.getDirectory()).getDirectoryHandle('picked');
  for await (const [name, top] of root.entries()) {
    const room = await (await top.getDirectoryHandle('rooms')).getDirectoryHandle('%s');
    await room.removeEntry('done.json'); await room.removeEntry('files_manifest.json');
  }
}""" % TOPIC_DIR
ROOM_DIRS = """async () => {
  const root = await (await navigator.storage.getDirectory()).getDirectoryHandle('picked');
  for await (const [name, top] of root.entries()) {
    const room = await (await top.getDirectoryHandle('rooms')).getDirectoryHandle('%s');
    const out = []; for await (const [n, h] of room.entries()) if (h.kind === 'directory') out.push(n);
    return out.sort();
  }
}""" % TOPIC_DIR
TOP_DIRS = """async () => {
  const root = await (await navigator.storage.getDirectory()).getDirectoryHandle('picked');
  for await (const [name, top] of root.entries()) {
    const out = []; for await (const [n, h] of top.entries()) if (h.kind === 'directory') out.push(n);
    return out.sort();
  }
}"""


def _reopen(page):
    page.evaluate("window.__log.pages = []; window.__log.getImageAt = []")
    page.reload()
    page.evaluate(SCRIPT)
    host = page.locator("#__jandi-backup-host")
    host.locator(f"text={CUTOFF_TEXT}").wait_for()
    return host


def test_upload_limit_splits_attachments_teams_folders_and_plan(browser):
    """한도를 넘는 방의 첨부는 attachments_1·_2 에 나눠 저장하고 뷰어·워드 링크도 그 경로. 「팀즈에 올릴 파일」 은
    방 이름으로 평평하게 한도마다 폴더, 올리기 순서.txt·상위 index 에 한도 이하 묶음. 같은 폴더를 원래 한도로 이어 받으면
    attachments_N 의 파일을 그대로 인정하고, 팀즈 폴더는 새로 만든다."""
    ctx = browser.new_context()
    page = ctx.new_page()
    host = _start(page, [])
    page.evaluate(LIMIT_3)
    host.locator("button[data-act=start]").click()
    host.locator("text=백업을 마쳤습니다").wait_for(timeout=20000)
    files = page.evaluate("window.__dumpOpfs()")
    top = next(iter(files)).split("/")[0]
    room = f"{top}/rooms/{TOPIC_DIR}"
    manifest = {str(f["id"]): f for f in json.loads(files[f"{room}/files_manifest.json"]["text"])}
    # 받는 순서 7000001, 7000002, 7000003(404), 7000004, 5000010 → 3개씩
    assert {k: manifest[k]["path"].split("/")[0] for k in manifest} == {
        "7000001": "attachments_1", "7000002": "attachments_1", "7000003": "attachments_1",
        "7000004": "attachments_2", "5000010": "attachments_2"}
    for fid in ("7000001", "7000002", "7000004", "5000010"):
        assert files[f"{room}/{manifest[fid]['path']}"]["size"] == SIZES[fid]
    assert page.evaluate(ROOM_DIRS) == ["attachments_1", "attachments_2"], "attachments/ 는 만들지 않는다"
    viewer = files[f"{room}/index.html"]["text"]
    assert 'href="attachments_1/7000001_' in viewer and 'href="attachments_2/5000010_' in viewer
    parts = _open_docx(files[f"{room}/대화.docx"])
    links = {r.get("Target") for r in parts["word/_rels/document.xml.rels"].iter(REL + "Relationship") if r.get("Type").endswith("/hyperlink")}
    assert any(t.startswith("attachments_2/5000010_") for t in links)

    # 팀즈에 올릴 파일: 워드 4개 → 3 + 1, 방 이름으로
    teams = sorted(k[len(top) + 1:] for k in files if k.startswith(f"{top}/팀즈에 올릴 파일"))
    assert [t.split("/")[0] for t in teams].count("팀즈에 올릴 파일_1") == 3
    assert [t.split("/")[0] for t in teams].count("팀즈에 올릴 파일_2") == 1
    assert "팀즈에 올릴 파일_1/[테스트] 기획 _팀_ 방.docx" in teams
    assert files[f"{top}/팀즈에 올릴 파일_1/[테스트] 기획 _팀_ 방.docx"]["b64"] == files[f"{room}/대화.docx"]["b64"], "그대로 복사"

    # 올리기 순서: 묶음마다 한도 이하 (넘는 건 혼자 + 표시)
    plan = files[f"{top}/올리기 순서.txt"]["text"]
    # 브라우저 text() 는 BOM 을 걷고 읽는다 — 있든 없든 본문으로 본다
    assert plan.lstrip("\ufeff").startswith("팀즈·OneDrive 에 올릴 때 한 번에 1,000개를 넘기면 회사 보안 검사가 멈춥니다.")
    lines = [l for l in plan.split("\r\n") if "번 묶음 (" in l]
    assert lines and all(int(l.split("(")[1].split("개")[0].replace(",", "")) <= 3 or "넘습니다" in l for l in lines), lines
    assert any(l.endswith(f": rooms/{TOPIC_DIR}/attachments_1") for l in lines)
    assert any(l.endswith(f": rooms/{TOPIC_DIR}/attachments_2") for l in lines)
    assert any(f"rooms/{TOPIC_DIR} (attachments_1, attachments_2 폴더 빼고)" in l for l in lines)
    assert not any("팀즈에 올릴 파일" in l for l in lines), "워드만 폴더는 묶음에 넣지 않는다"
    # 묶음 합 = 백업 폴더 파일 수 (팀즈 폴더 제외)
    total = sum(int(l.split("(")[1].split("개")[0].replace(",", "")) for l in lines)
    assert total == sum(1 for k in files if "/팀즈에 올릴 파일" not in k)
    index = files[f"{top}/index.html"]["text"]
    assert '<h2 id="upload">올리기 순서</h2>' in index and "attachments_2</li>" in index
    assert "올리기 순서.txt 의 묶음대로" in files[f"{top}/README.txt"]["text"]

    # 원래 한도(900)로 이어 받기 — 표식·목록을 지운 방도 attachments_N 의 파일을 인정하고, 못 받은 것만 다시
    page.evaluate(FORGET_TOPIC)
    host = _reopen(page)
    assert page.evaluate("window.__jandiBackup.lib.UPLOAD_LIMIT") == 900
    host.locator("button[data-act=start]").click()
    host.locator("text=백업을 마쳤습니다").wait_for(timeout=20000)
    assert sorted(g["id"] for g in page.evaluate("window.__log")["getImageAt"]) == ["7000003"]
    files2 = page.evaluate("window.__dumpOpfs()")
    manifest2 = {str(f["id"]): f["path"] for f in json.loads(files2[f"{room}/files_manifest.json"]["text"]) if f["status"] == "downloaded"}
    assert manifest2 == {k: manifest[k]["path"] for k in ("7000001", "7000002", "7000004", "5000010")}, "원래 자리 그대로"
    assert page.evaluate(ROOM_DIRS) == ["attachments_1", "attachments_2"], "새 배정(attachments/)으로 옮기거나 빈 폴더를 만들지 않는다"
    assert [d for d in page.evaluate(TOP_DIRS) if d.startswith("팀즈")] == ["팀즈에 올릴 파일"], "지난 팀즈 폴더는 지우고 새로"
    assert sum(1 for k in files2 if k.startswith(f"{top}/팀즈에 올릴 파일/")) == 4
    ctx.close()


def test_resume_old_attachments_folder_with_split_limit(browser):
    """예전처럼 attachments/ 하나에 받아 둔 방을, 한도를 넘는 방이 된 뒤(한도 3) 이어 받아도 이미 있는 파일은 attachments/ 그대로 인정한다."""
    ctx = browser.new_context()
    page = ctx.new_page()
    host = _start(page, [])
    host.locator("button[data-act=start]").click()
    host.locator("text=백업을 마쳤습니다").wait_for(timeout=20000)
    assert page.evaluate(ROOM_DIRS) == ["attachments"]
    page.evaluate(FORGET_TOPIC)
    host = _reopen(page)
    page.evaluate(LIMIT_3)
    host.locator("button[data-act=start]").click()
    host.locator("text=백업을 마쳤습니다").wait_for(timeout=20000)
    assert sorted(g["id"] for g in page.evaluate("window.__log")["getImageAt"]) == ["7000003"], "받아 둔 파일은 다시 받지 않는다"
    files = page.evaluate("window.__dumpOpfs()")
    top = next(iter(files)).split("/")[0]
    manifest = json.loads(files[f"{top}/rooms/{TOPIC_DIR}/files_manifest.json"]["text"])
    assert {f["path"].split("/")[0] for f in manifest if f["status"] == "downloaded"} == {"attachments"}
    assert page.evaluate(ROOM_DIRS) == ["attachments"], "못 받은 파일 때문에 빈 attachments_N 이 생기지 않는다"
    ctx.close()
