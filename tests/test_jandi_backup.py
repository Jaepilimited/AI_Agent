"""잔디 백업 도우미 (`/jandi-backup`) — 화면 · 배선 · 북마크 본체의 불변식 (2026-10-01).

북마크 본체(`app/static/jandi-backup/jandi-backup.js`)는 각자의 잔디 탭에서 돌아
대화를 각자의 PC 폴더로만 저장한다. 여기서 지키는 것:
- 화면은 로그인한 사람에게만, 로그아웃 브라우저는 원시 401 대신 로그인으로
- 북마크는 실행 중 셀라를 부르지 않는다 (사설 주소 → 브라우저 권한 창, 그리고 대화가 새는 길)
- 순수 함수(시스템 문장 · 파일 이름 · CSV · HTML · 쪽 넘김 · 방 뷰어)는 node 로 실제 실행
브라우저 흐름 전체는 `tests/frontend/test_jandi_backup_panel.py` 가 가짜 잔디 화면에서 돌린다.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app/static/jandi-backup/jandi-backup.js"
BOOKMARKLET = ROOT / "app/static/jandi-backup/bookmarklet.js"
PAGE = ROOT / "app/static/jandi_backup.html"


# ── node 로 순수 함수 시험 ─────────────────────────────────────────────────

def test_js_unit_tests_pass_under_node():
    node = shutil.which("node")
    if not node:
        pytest.skip("node 없음 — 개발 환경 전용 검사")
    r = subprocess.run([node, "--test", str(ROOT / "tests/js/jandi_backup.test.js")],
                       capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
    assert re.search(r"# fail 0|ℹ fail 0", r.stdout), r.stdout[-2000:]


def test_scripts_parse():
    node = shutil.which("node")
    if not node:
        pytest.skip("node 없음 — 개발 환경 전용 검사")
    for path in (JS, BOOKMARKLET):
        r = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
        assert r.returncode == 0, f"{path.name}: {r.stderr}"


# ── 북마크는 셀라를 부르지 않는다 ──────────────────────────────────────────

def _code_without_comments(src: str) -> str:
    src = re.sub(r"/\*[\s\S]*?\*/", "", src)
    return re.sub(r"(?m)^\s*//.*$", "", src)


def test_bookmarklet_never_calls_cella_at_runtime():
    """⛔ 북마크 실행 중 셀라(사설 주소)를 부르면 Chrome 이 '로컬 네트워크 접근' 권한을 묻고,
    무엇보다 대화가 서버로 새는 길이 생긴다. 바깥으로 나가는 길은 잔디 주소뿐이어야 한다."""
    src = JS.read_text(encoding="utf-8")
    code = _code_without_comments(src)
    for banned in ("cravercorp", ".internal", "localhost", "127.0.0.1", "10.1.", "172.16.",
                   "/api/", "XMLHttpRequest", "sendBeacon", "WebSocket", "EventSource",
                   "import(", "importScripts", "<script src", "createElement('script')",
                   'createElement("script")', "location.origin", "postMessage("):
        assert banned not in code, f"북마크 본체에 {banned!r} 가 있다"
    hosts = set(re.findall(r"https?://([A-Za-z0-9.\-]+)", code))
    # 워드(.docx) XML 이름공간은 주소 모양의 이름일 뿐 요청하지 않는다 (아래 fetch·http.get 검사가 요청 길을 막는다)
    xml_namespaces = {"schemas.openxmlformats.org", "purl.org", "www.w3.org"}
    assert hosts and all(h.endswith(("jandi.com", "jandi-box.com")) or h.startswith("팀이름") or h in xml_namespaces
                         for h in hosts), hosts
    for ns in re.findall(r"https?://(?:schemas\.openxmlformats\.org|purl\.org|www\.w3\.org)[^'\"\s]*", code):
        assert ns.startswith(("http://schemas.openxmlformats.org/", "http://purl.org/dc/", "http://www.w3.org/2001/XMLSchema-instance")), ns
    # fetch 는 잔디가 준 다운로드 주소(url 변수) 하나에만 쓴다
    assert set(re.findall(r"\bfetch\(([^,)]+)", code)) == {"url"}
    # $http 는 잔디 메시지 API 로만
    assert re.findall(r"http\.get\(([^,)]+)", code) == ["url"]
    assert "var MESSAGES_API = 'https://i1.jandi.com/message-api/v2/teams/';" in src


def test_download_url_is_requested_right_before_each_download():
    """⛔ 잔디 다운로드 주소는 약 1분 뒤 만료된다 — 파일마다, 시도마다 새로 받는다."""
    src = JS.read_text(encoding="utf-8")
    body = src[src.index("async function downloadOne"):src.index("async function downloadAll")]
    assert body.index("for (var attempt = 0; attempt < 3; attempt++)") < body.index("fileApi.getImage(") < body.index("fetch(url")
    assert src.count("getImage(") == 1, "주소를 미리 몰아 받는 다른 호출이 없다"


def test_page_builds_bookmarklet_from_same_origin_static_file():
    page = PAGE.read_text(encoding="utf-8")
    assert 'fetch("/static/jandi-backup/jandi-backup.js"' in page
    assert '<script src="/static/jandi-backup/bookmarklet.js"></script>' in page
    assert "toBookmarkletHref(code)" in page
    assert 'id="jb-bookmarklet"' in page and ">잔디 백업</a>" in page
    for phrase in ("셀라 서버는 이 내용을 받지도, 보지도 않습니다", "Chrome 또는 Edge", "잔디가 업데이트되면 멈출 수 있습니다",
                   "수십 GB", "참여하지 않은 토픽", "편집 이력", "복원",
                   # 얼마나 받는지(용량·시간) — 실측 참고값과 미리 확인 버튼 안내
                   "얼마나 걸리나요", "용량 미리 확인", "484개", "약 123GB", "약 66GB", "약 7~10분", "약 40~60분",
                   # 기준일(잔디 가입일) — 무엇을 안 받는지 (예전 버전을 받은 사람이 없어 '다시 끌어다 놓기' 안내는 없다)
                   "입사(잔디 가입) 이후 대화만 받습니다", "잔디에 들어오기 전(가입일 이전) 대화와 첨부",
                   # 방 고르기 — 필요 없는 방 빼기
                   "<b>방 고르기</b>에서 체크를 끄면 받지 않습니다",
                   # 백업 전에 — 필요 없는 방 빼기 · 사적인 방 · 이모티콘 기본 꺼짐
                   "<summary>백업 전에</summary>", "업무 기록이 아닌 방은 용량만 차지합니다",
                   "사적인 대화방은 받지 않는 것을 권합니다", "이모티콘은 기본으로 받지 않습니다",
                   # 첨부 종류 프리셋 — 기본은 업무용
                   "<b>대화만 / 업무용 / 전부</b> 중에 고르세요", "기본은 업무용(대화·문서·사진)이고 동영상·이모티콘은 빠집니다",
                   # 방별 워드 — 팀즈·OneDrive 에서 보기
                   # index.html 과 대화.docx 구분 표 (2026-10-07)
                   "<th>index.html</th><th>대화.docx (방 폴더마다)</th>", "<b>모든 방을 한 번에</b> 검색",
                   "팀즈 웹에서는 방 폴더의 attachments", "index.html 은 팀즈·OneDrive 웹에서는 열리지 않고 내려받기만 됩니다"):
        assert phrase in page, phrase


# ── 배선 ───────────────────────────────────────────────────────────────────

def test_route_and_sidebar_are_wired():
    main = (ROOT / "app/main.py").read_text(encoding="utf-8")
    assert "include_router(jandi_backup_router)" in main
    html = (ROOT / "app/frontend/chat.html").read_text(encoding="utf-8")
    js = (ROOT / "app/frontend/chat.js").read_text(encoding="utf-8")
    assert 'id="btn-jandi-backup"' in html
    assert re.search(r'getElementById\("btn-jandi-backup"\)[\s\S]{0,200}"/jandi-backup"', js)


def test_page_is_in_browser_page_list():
    """새 화면을 `_page_request` 에 안 넣으면 로그아웃 브라우저가 원시 401 JSON 을 본다."""
    from starlette.requests import Request

    from app.api.middleware import _page_request
    req = Request({"type": "http", "method": "GET", "path": "/jandi-backup", "headers": [], "query_string": b""})
    assert _page_request(req)


_SECRET = "test-only-jandi-backup-session-secret-" + "x" * 40
_TENANT = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def client(monkeypatch):
    from app.api import auth_middleware
    from app.config import get_settings
    monkeypatch.setenv("MIGRATED_REDIRECT_URL", "")
    monkeypatch.setenv("JWT_SECRET_KEY", _SECRET)
    monkeypatch.setenv("ENTRA_TENANT_ID", _TENANT)
    monkeypatch.setenv("PASSWORD_LOGIN_ENABLED", "false")
    get_settings.cache_clear()
    monkeypatch.setattr(auth_middleware, "_user_cache", {})
    monkeypatch.setattr(auth_middleware, "fetch_one", lambda *args, **kwargs: {
        "id": 7, "email": "tester@skin1004korea.com", "role": "user", "display_name": "Tester",
        "account_active": 1, "directory_active": 1, "must_change_password": 0,
        "requires_group_assignment": 0,
    })
    from app.main import create_app
    session = TestClient(create_app())
    session.cookies.set("token", jwt.encode({
        "user_id": 7, "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        "purpose": "session", "auth_provider": "entra", "entra_tid": _TENANT,
        "entra_oid": "22222222-2222-4222-8222-222222222222",
    }, _SECRET, algorithm="HS256"))
    yield session
    get_settings.cache_clear()


def test_page_redirects_to_login_without_session(client):
    client.cookies.clear()
    r = client.get("/jandi-backup", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/login"


def test_page_is_served_to_signed_in_user(client):
    r = client.get("/jandi-backup")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "잔디 백업 도우미" in r.text and 'id="jb-bookmarklet"' in r.text
    # 정적 파일 지문은 미들웨어가 붙인다 — 화면이 읽는 본체도 받을 수 있어야 한다
    assert re.search(r'"/static/jandi-backup/jandi-backup\.js(\?v=[^"]+)?"', r.text)
    js = client.get("/static/jandi-backup/jandi-backup.js")
    assert js.status_code == 200 and "boot(window, lib)" in js.text
