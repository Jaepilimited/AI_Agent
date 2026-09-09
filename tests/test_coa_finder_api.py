"""COA 찾기 엔드포인트."""
import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi.testclient import TestClient

from app.api.auth_middleware import get_current_user
from app.core import coa_finder as cf


_SECRET = "test-only-coa-session-secret-" + "x" * 40
_TENANT = "11111111-1111-4111-8111-111111111111"


class _User:
    email = "tester@skin1004korea.com"
    role = "user"


@pytest.fixture
def client(monkeypatch):
    # ⛔ 개발 PC `.env` 의 MIGRATED_REDIRECT_URL 이 앱 생성 시점에 리다이렉트
    #    미들웨어로 박힌다 (tests/test_router.py 와 같은 원인). 이미 만들어진
    #    `app.main.app` 싱글턴을 그대로 쓰면 모든 요청이 307 로 튕기므로,
    #    설정을 끈 뒤 새로 만든 앱으로 테스트한다.
    from app.api import auth_middleware
    from app.config import get_settings
    monkeypatch.setenv("MIGRATED_REDIRECT_URL", "")
    monkeypatch.setenv("JWT_SECRET_KEY", _SECRET)
    monkeypatch.setenv("ENTRA_TENANT_ID", _TENANT)
    monkeypatch.setenv("PASSWORD_LOGIN_ENABLED", "false")
    get_settings.cache_clear()
    monkeypatch.setattr(auth_middleware, "_user_cache", {})
    monkeypatch.setattr(auth_middleware, "fetch_one", lambda *args, **kwargs: {
        "id": 7, "email": _User.email, "role": _User.role, "display_name": "Tester",
        "account_active": 1, "directory_active": 1, "must_change_password": 0,
        "requires_group_assignment": 0,
    })
    from app.main import create_app
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _User()
    session = TestClient(app)
    session.cookies.set("token", jwt.encode({
        "user_id": 7, "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        "purpose": "session", "auth_provider": "entra", "entra_tid": _TENANT,
        "entra_oid": "22222222-2222-4222-8222-222222222222",
    }, _SECRET, algorithm="HS256"))
    yield session
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def test_search_requires_google_connection(client):
    """⛔ 미연결을 '전부 없음' 으로 보여주면 안 된다."""
    with patch("app.api.coa_finder_api._credentials", return_value=None):
        r = client.post("/api/coa-finder/search",
                        data={"pasted": "SKU\tDESCRIPTION\tLOT\nA\t앰플\tFE103C\n"})
    assert r.status_code == 409
    assert "구글" in r.json()["detail"]


def test_search_streams_one_event_per_row(client):
    payload = "SKU\tDESCRIPTION\tLOT\nA\t앰플\tFE103C\nB\t크림\t416022\n"
    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()), \
         patch("app.core.coa_finder.search_drive", return_value=[]):
        r = client.post("/api/coa-finder/search", data={"pasted": payload})
    assert r.status_code == 200
    body = r.text
    assert body.count("event: row") == 2
    assert "event: done" in body

    # 이벤트 개수만 세면 순서가 뒤바뀌거나 필드가 빈 통과도 놓친다 — 실제 payload 를 본다
    row_lines = [ln for blk in body.split("\n\n") if blk.startswith("event: row")
                 for ln in blk.splitlines() if ln.startswith("data: ")]
    rows = [json.loads(ln[len("data: "):]) for ln in row_lines]
    assert [r["sku"] for r in rows] == ["A", "B"]  # 입력 순서 그대로
    assert rows[0]["lot"] == "FE103C"
    assert rows[0]["coa"]["status"]
    assert rows[0]["msds"]["status"]


def test_search_stream_reports_error_without_a_fake_done(client):
    """⛔ 잘린 스트림이 완료로 보이면 안 된다."""
    payload = "SKU\tDESCRIPTION\tLOT\nA\t앰플\tFE103C\nB\t크림\t416022\n"

    def _dies_after_one_row(creds, rows, search=None, max_workers=8):
        row = cf.Row(sku="A", description="앰플", lot="FE103C", line_no=2)
        yield cf.Result(row=row, coa=cf.Verdict(cf.FOUND, (), ""),
                        msds=cf.Verdict(cf.NONE, (), ""),
                        product_coa=cf.Verdict(cf.NONE, (), ""))
        raise RuntimeError("drive timeout")

    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()), \
         patch("app.api.coa_finder_api.cf.find_all", side_effect=_dies_after_one_row):
        r = client.post("/api/coa-finder/search", data={"pasted": payload})

    assert r.status_code == 200
    body = r.text
    assert body.count("event: row") == 1
    assert "event: error" in body
    assert "event: done" not in body
    error_line = next(ln for ln in body.splitlines() if ln.startswith("data: ")
                      and "completed" in ln)
    error_payload = json.loads(error_line[len("data: "):])
    assert error_payload["completed"] == 1
    assert error_payload["total"] == 2


def test_search_rejects_oversized_upload(client):
    """⛔ 상한을 넘는 순간 끊는다 — 다 받은 뒤에 재는 게 아니다.

    그래서 정확한 총량이 아니라 '적어도 이만큼' 이라고만 말한다.
    """
    oversized = b"x" * (5 * 1024 * 1024 + 1024)
    r = client.post(
        "/api/coa-finder/search",
        files={"file": ("big.xlsx", oversized, "application/octet-stream")},
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "5MB" in detail          # 상한
    assert "적어도" in detail        # 잰 만큼만 말한다 — 확인 안 된 총량을 주장하지 않는다


def test_search_rejects_oversized_pasted_text(client):
    """⛔ 파일만 막고 붙여넣기는 그대로 두면 같은 구멍이 방식만 바뀌어 남는다."""
    oversized = "A" * (5 * 1024 * 1024 + 1024)
    r = client.post("/api/coa-finder/search", data={"pasted": oversized})
    assert r.status_code == 400
    assert "5MB" in r.json()["detail"]


def test_search_accepts_a_paste_with_no_header_row(client):
    """⛔ 프로덕션 400 두 건의 원인 — 헤더 없이 붙여넣으면 조회가 아예 안 됐다."""
    payload = "EUSKA022\tSKIN1004 Ampoule 100ml\tFE103C\nEUSKC017\t크림 75ml\t416022\n"
    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()), \
         patch("app.core.coa_finder.search_drive", return_value=[]):
        r = client.post("/api/coa-finder/search", data={"pasted": payload})
    assert r.status_code == 200
    assert r.text.count("event: row") == 2
    done = _done_payload(r.text)
    assert done["inferred"] is True
    assert done["layout"], "무엇을 어떻게 읽었는지 알려주지 않았다"


def test_search_accepts_a_bare_list_of_lots(client):
    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()), \
         patch("app.core.coa_finder.search_drive", return_value=[]):
        r = client.post("/api/coa-finder/search", data={"pasted": "FE103C\nE08Z011\n"})
    assert r.status_code == 200
    assert r.text.count("event: row") == 2


def test_unparsable_paste_says_what_shapes_are_accepted(client):
    """⛔ 무엇이 없는지가 아니라 무엇을 하면 되는지 말한다."""
    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()):
        r = client.post("/api/coa-finder/search", data={"pasted": "   \n\n"})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "롯트" in detail          # 최소 입력이 무엇인지
    assert "엑셀" in detail or "쉼표" in detail


def test_search_stream_carries_the_product_coa_column(client):
    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()), \
         patch("app.core.coa_finder.search_drive", return_value=[]):
        r = client.post("/api/coa-finder/search",
                        data={"pasted": "SKU\tDESCRIPTION\tLOT\nA\t앰플 100ml\tFE103C\n"})
    row_line = next(ln for ln in r.text.splitlines()
                    if ln.startswith("data: ") and "product_coa" in ln)
    row = json.loads(row_line[len("data: "):])
    assert row["product_coa"]["status"]
    assert row["coa"]["status"] and row["msds"]["status"]


def test_search_stream_carries_size_and_modified_time(client):
    """후보가 여럿일 때 **열지 않고** 고르는 근거다 — 여기서 빠지면 화면도 못 적는다.

    ⛔ 판정에는 쓰지 않는다. 최신 파일이라고 그 롯트의 것은 아니다.
    """
    hit = [{"id": "f1", "name": "COA_FE103C.pdf", "size": "2048",
            "webViewLink": "http://d/f1", "modifiedTime": "2026-08-14T01:02:03.000Z"}]
    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()), \
         patch("app.core.coa_finder.search_drive", return_value=hit):
        r = client.post("/api/coa-finder/search",
                        data={"pasted": "SKU\tDESCRIPTION\tLOT\nA\t앰플\tFE103C\n"})
    row_line = next(ln for ln in r.text.splitlines()
                    if ln.startswith("data: ") and "COA_FE103C" in ln)
    row = json.loads(row_line[len("data: "):])
    got = row["coa"]["files"][0]
    assert got["size"] == 2048
    assert got["modified"].startswith("2026-08-14")


def test_a_file_without_a_modified_time_is_not_given_one(client):
    """⛔ 없는 값을 지어내면 화면이 조용히 거짓말을 한다."""
    hit = [{"id": "f1", "name": "COA_FE103C.pdf", "size": "10",
            "webViewLink": "http://d/f1"}]
    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()), \
         patch("app.core.coa_finder.search_drive", return_value=hit):
        r = client.post("/api/coa-finder/search",
                        data={"pasted": "SKU\tDESCRIPTION\tLOT\nA\t앰플\tFE103C\n"})
    row_line = next(ln for ln in r.text.splitlines()
                    if ln.startswith("data: ") and "COA_FE103C" in ln)
    row = json.loads(row_line[len("data: "):])
    assert row["coa"]["files"][0]["modified"] == ""


@pytest.fixture
def anon_client():
    """로그인 안 된 브라우저 — get_current_user 오버라이드를 걸지 않는다."""
    from app.config import get_settings
    os.environ["MIGRATED_REDIRECT_URL"] = ""
    get_settings.cache_clear()
    from app.main import create_app
    yield TestClient(create_app())


def test_page_is_served(client):
    r = client.get("/coa-finder")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_page_redirects_to_login_without_a_session(anon_client):
    """⛔ 브라우저 주소로 여는 화면이 원시 401 JSON 을 보여주면 안 된다.

    요청자가 링크를 처음 여는데 세션이 끊겨 있으면 로그인 화면이 나와야 한다
    (구글 OAuth 콜백 사고에서 정한 규칙과 같다). `/` 가 하는 그대로 한다.
    """
    r = anon_client.get("/coa-finder", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login"


def test_api_endpoints_keep_returning_json_401(anon_client):
    """⛔ 반대 방향 — API 를 로그인 페이지로 리다이렉트하면 fetch 가 HTML 을
    결과로 읽는다. 화면 경로와 API 경로는 실패하는 방식이 달라야 한다."""
    r = anon_client.post("/api/coa-finder/search",
                         data={"pasted": "SKU\tDESCRIPTION\tLOT\nA\t앰플\tFE103C\n"},
                         follow_redirects=False)
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/json")

    r2 = anon_client.post("/api/coa-finder/download",
                          json={"items": [{"file_id": "1"}]}, follow_redirects=False)
    assert r2.status_code == 401
    assert r2.headers["content-type"].startswith("application/json")


def test_download_rejects_too_many_items(client):
    items = [{"file_id": str(i), "sku": "A", "lot": "L", "name": "x.pdf"}
             for i in range(201)]
    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()):
        r = client.post("/api/coa-finder/download", json={"items": items})
    assert r.status_code == 400
    assert "200" in r.json()["detail"]


def test_download_records_files_it_could_not_fetch(client):
    """⛔ 조용히 빠지면 아무도 모른다 — 못 받은 목록을 ZIP 안에 남긴다."""
    import io as _io
    import zipfile

    # status 는 화면의 판정이다 — 확정된 행이라야 이름에 확인필요 접두가 안 붙는다
    items = [{"file_id": "ok", "sku": "A", "lot": "FE103C", "name": "a.pdf",
              "status": "찾음", "kind": "coa"},
             {"file_id": "bad", "sku": "B", "lot": "416022", "name": "b.pdf",
              "status": "찾음", "kind": "coa"}]

    def fake_fetch(creds, file_id, budget):
        if file_id == "bad":
            raise RuntimeError("403")
        return b"%PDF-1.4 fake"

    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()), \
         patch("app.api.coa_finder_api._fetch_file", side_effect=fake_fetch):
        r = client.post("/api/coa-finder/download", json={"items": items})

    assert r.status_code == 200
    zf = zipfile.ZipFile(_io.BytesIO(r.content))
    names = zf.namelist()
    assert "COA/A_FE103C_a.pdf" in names
    assert "_받지못한_목록.txt" in names
    assert "416022" in zf.read("_받지못한_목록.txt").decode("utf-8")


def test_download_rejects_empty_file_id(client):
    """⛔ 빈 file_id 를 그냥 넘기면 ZIP 안에 이름 없는 항목이 생긴다."""
    items = [{"file_id": "  ", "sku": "A", "lot": "L", "name": "x.pdf"}]
    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()):
        r = client.post("/api/coa-finder/download", json={"items": items})
    assert r.status_code == 400
    assert "file_id" in r.json()["detail"]


def test_download_reports_cap_and_skips_remaining(client):
    """⛔ 용량 상한을 넘긴 뒤 남은 항목이 조용히 사라지면 안 된다."""
    import io as _io
    import zipfile

    items = [
        {"file_id": "1", "sku": "A", "lot": "L1", "name": "a.pdf", "status": "찾음"},
        {"file_id": "2", "sku": "B", "lot": "L2", "name": "b.pdf", "status": "찾음"},
        {"file_id": "3", "sku": "C", "lot": "L3", "name": "c.pdf", "status": "찾음"},
    ]

    def fake_fetch(creds, file_id, budget):
        # 실제로 500MB 를 만들지 않는다 — 상한 자체를 낮춰서 같은 경로를 튄다.
        # 예산은 진짜 _fetch_file 처럼 청크 사이에서 본다 (다 받은 뒤가 아니다)
        from app.api.coa_finder_api import _DownloadBudgetExceeded
        if 40 > budget:
            raise _DownloadBudgetExceeded(40)
        return b"x" * 40

    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()), \
         patch("app.api.coa_finder_api._fetch_file", side_effect=fake_fetch), \
         patch("app.api.coa_finder_api._MAX_DOWNLOAD_BYTES", 50):
        r = client.post("/api/coa-finder/download", json={"items": items})

    assert r.status_code == 200
    zf = zipfile.ZipFile(_io.BytesIO(r.content))
    names = zf.namelist()
    assert "COA/A_L1_a.pdf" in names      # 40바이트 — 상한 50 안에 든다
    assert "COA/B_L2_b.pdf" not in names  # 누적 80 > 50 — 여기서 상한을 넘긴다
    assert "COA/C_L3_c.pdf" not in names  # 상한 넘긴 뒤라 건드리지 않는다
    assert "_받지못한_목록.txt" in names
    note = zf.read("_받지못한_목록.txt").decode("utf-8")
    assert "L2" in note                   # 상한을 넘긴 항목 자신
    assert "L3" in note                   # 넘긴 뒤 건너뛴 항목도 각자 한 줄씩


def _zip_of(client, items):
    import io as _io
    import zipfile

    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()), \
         patch("app.api.coa_finder_api._fetch_file",
               side_effect=lambda creds, file_id, budget: b"%PDF-1.4 fake"):
        r = client.post("/api/coa-finder/download", json={"items": items})
    assert r.status_code == 200
    return zipfile.ZipFile(_io.BytesIO(r.content))


def test_a_file_name_never_carries_a_warning(client):
    """⛔ 파일 이름에는 아무 표시도 붙이지 않는다 (2026-09-03 사용자 결정).

    예전엔 확정되지 않은 문서에 `확인필요_` 접두를 붙였다. ZIP 이 그대로
    고객에게 가는 산출물이라 한국어 접두가 이름에 남는 것이 문제였고,
    경고는 `_확인필요_목록.txt` 하나로 모았다.
    """
    zf = _zip_of(client, [{
        "file_id": "1", "sku": "EUSKA022", "lot": "FE161",
        "name": "COA_10116720_SUN SERUM_FE1615_15643EA.pdf",
        "status": "확인필요", "kind": "coa",
    }])
    names = zf.namelist()
    assert ("COA/EUSKA022_FE161_"
            "COA_10116720_SUN SERUM_FE1615_15643EA.pdf") in names
    assert not any("확인필요_" in n for n in names if not n.startswith("_"))


def test_an_unconfirmed_file_is_still_listed_with_its_reason(client):
    """⛔ 이름이 조용해진 만큼 목록이 유일한 경고다 — 여기가 비면 아무 경고도 없다.

    롯트 FE161 을 물었는데 드라이브 파일은 FE1615 인 실측 사례 — 화면은
    확인필요라고 말하는데, 그 사실이 ZIP 안 어디에도 없으면 없는 것이다.
    """
    zf = _zip_of(client, [{
        "file_id": "1", "sku": "EUSKA022", "lot": "FE161",
        "name": "COA_10116720_SUN SERUM_FE1615_15643EA.pdf",
        "status": "확인필요", "kind": "coa",
    }])
    names = zf.namelist()
    assert "_확인필요_목록.txt" in names
    note = zf.read("_확인필요_목록.txt").decode("utf-8")
    assert "EUSKA022" in note and "FE161" in note
    assert "FE1615" in note                     # 원본 파일명
    assert "확인필요" in note                    # 왜 확정이 아닌지
    # ⛔ 실패와 불확실은 다른 목록이다 — 섞으면 둘 다 안 읽힌다
    assert "_받지못한_목록.txt" not in names


def test_the_unconfirmed_list_says_the_names_are_unmarked(client):
    """⛔ 목록을 안 열어 본 사람은 이름만 보고 전부 확정된 문서라고 읽는다."""
    zf = _zip_of(client, [{"file_id": "1", "sku": "A", "lot": "FE103C",
                           "name": "a.pdf", "status": "확인필요", "kind": "coa"}])
    note = zf.read("_확인필요_목록.txt").decode("utf-8")
    assert "파일 이름에는 표시가 없습니다" in note


def test_download_treats_missing_status_as_unconfirmed(client):
    """⛔ 엔드포인트는 임의 JSON 을 받는다 — 상태가 없으면 확정으로 보면 안 된다.

    이름으로는 더 이상 알 수 없으니 **목록에 오르는지**로 확인한다.
    """
    zf = _zip_of(client, [{"file_id": "1", "sku": "A", "lot": "FE103C",
                           "name": "a.pdf"}])
    assert "COA/A_FE103C_a.pdf" in zf.namelist()
    assert "_확인필요_목록.txt" in zf.namelist()


def test_download_treats_unknown_status_as_unconfirmed(client):
    zf = _zip_of(client, [{"file_id": "1", "sku": "A", "lot": "FE103C",
                           "name": "a.pdf", "status": "OK"}])
    assert "_확인필요_목록.txt" in zf.namelist()


def test_download_leaves_confirmed_items_out_of_the_list(client):
    """반대 방향 — 확정된 문서까지 목록에 올리면 그 목록은 곧 안 읽힌다."""
    zf = _zip_of(client, [{"file_id": "1", "sku": "A", "lot": "FE103C",
                           "name": "a.pdf", "status": "찾음", "kind": "coa"}])
    names = zf.namelist()
    assert "COA/A_FE103C_a.pdf" in names
    assert "_확인필요_목록.txt" not in names


def test_download_msds_entry_carries_no_lot(client):
    """⛔ MSDS 는 제품 단위 문서라 롯트가 없다 — 이름에 롯트를 심으면
    롯트가 맞는 문서인 것처럼 보인다 (스펙이 명시적으로 금지한 것)."""
    zf = _zip_of(client, [{
        "file_id": "1", "sku": "EUSKA022", "lot": "FE103C",
        "name": "SKIN1004 Madagascar Centella Cream 75ML_MSDS(WERCS).pdf",
        "status": "찾음", "kind": "msds",
    }])
    entry = next(n for n in zf.namelist() if not n.startswith("_"))
    assert "FE103C" not in entry
    assert entry.startswith("MSDS/EUSKA022_MSDS_")


def test_download_product_coa_entry_carries_no_lot(client):
    """⛔ 제품명으로 찾은 COA 는 **다른 롯트**의 것이다 — 이름에 이 행의 롯트를
    붙이면 그 롯트 증명서라고 주장하게 된다 (MSDS 와 같은 이유, 위험은 더 크다)."""
    zf = _zip_of(client, [{
        "file_id": "1", "sku": "EUSKA022", "lot": "F31C28 D",
        "name": "COA_AMPOULE 100ML_F11C05 C.pdf",
        "status": "확인필요", "kind": "product_coa",
    }])
    entry = next(n for n in zf.namelist() if not n.startswith("_"))
    assert "F31C28" not in entry
    assert entry.startswith("제품COA/EUSKA022_")
    note = zf.read("_확인필요_목록.txt").decode("utf-8")
    assert "롯트" in note


def test_download_never_treats_product_coa_as_confirmed(client):
    """⛔ 서버가 막는다 — 클라이언트가 '찾음' 이라고 주장해도 확정이 아니다.
    이 열은 구조적으로 확정될 수 없다 (다른 생산분의 문서다)."""
    zf = _zip_of(client, [{
        "file_id": "1", "sku": "A", "lot": "L1", "name": "coa.pdf",
        "status": "찾음", "kind": "product_coa",
    }])
    # 이름이 아니라 **목록에 오르는지**가 판정을 지고 있다
    assert "_확인필요_목록.txt" in zf.namelist()
    note = zf.read("_확인필요_목록.txt").decode("utf-8")
    assert "다른 생산분" in note


# ── COA 와 MSDS 를 섞지 않는다 — 2026-09-02 사용자 문의 ──────────────────────
#
# *"자료 다운로드 시 COA와 MSDS가 섞여서 다운 되는데, 따로 다운로드 가능할까요?"*
# 이름으로는 갈렸지만 한 폴더에 쏟아져서, 받은 사람이 파일명을 읽어 골라내야 했다.


def test_download_sorts_each_kind_into_its_own_folder(client):
    """섞어서 받아도 폴더로 갈린다 — 종류별 버튼을 안 쓴 사람도 이득을 본다."""
    zf = _zip_of(client, [
        {"file_id": "1", "sku": "A", "lot": "FE103C", "name": "coa.pdf",
         "status": "찾음", "kind": "coa"},
        {"file_id": "2", "sku": "A", "lot": "FE103C", "name": "msds.pdf",
         "status": "찾음", "kind": "msds"},
        {"file_id": "3", "sku": "A", "lot": "FE103C", "name": "other.pdf",
         "status": "확인필요", "kind": "product_coa"},
    ])
    names = [n for n in zf.namelist() if not n.startswith("_")]
    assert sorted(n.split("/")[0] for n in names) == ["COA", "MSDS", "제품COA"]
    # 폴더 깊이는 언제나 하나다 — 파일명의 `/` 는 `_UNSAFE` 가 이미 지웠다
    assert all(n.count("/") == 1 for n in names)


def test_the_entry_name_is_folder_plus_plain_name(client):
    """폴더는 종류만 말한다 — 판정은 이름 어디에도 적히지 않는다."""
    zf = _zip_of(client, [{"file_id": "1", "sku": "A", "lot": "FE161",
                           "name": "a.pdf", "status": "확인필요", "kind": "coa"}])
    entry = next(n for n in zf.namelist() if not n.startswith("_"))
    assert entry == "COA/A_FE161_a.pdf"


def _disposition(client, items):
    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()),          patch("app.api.coa_finder_api._fetch_file",
               side_effect=lambda creds, file_id, budget: b"%PDF-1.4 fake"):
        r = client.post("/api/coa-finder/download", json={"items": items})
    assert r.status_code == 200
    return r.headers["content-disposition"]


def test_single_kind_archive_is_not_named_coa_msds(client):
    """⛔ MSDS 만 받았는데 파일 이름이 `coa_msds.zip` 이면 이름이 거짓말을 한다."""
    only_msds = [{"file_id": "1", "sku": "A", "lot": "", "name": "m.pdf",
                  "status": "찾음", "kind": "msds"}]
    assert "msds.zip" in _disposition(client, only_msds)
    assert "coa_msds.zip" not in _disposition(client, only_msds)

    only_coa = [{"file_id": "1", "sku": "A", "lot": "FE103C", "name": "c.pdf",
                 "status": "찾음", "kind": "coa"}]
    assert 'filename="coa.zip"' in _disposition(client, only_coa)


def test_mixed_archive_keeps_the_combined_name(client):
    """반대 방향 — 섞여 있으면 섞였다고 이름이 말해야 한다."""
    mixed = [
        {"file_id": "1", "sku": "A", "lot": "FE103C", "name": "c.pdf",
         "status": "찾음", "kind": "coa"},
        {"file_id": "2", "sku": "A", "lot": "", "name": "m.pdf",
         "status": "찾음", "kind": "msds"},
    ]
    assert 'filename="coa_msds.zip"' in _disposition(client, mixed)


def test_download_unknown_kind_is_treated_as_coa(client):
    """kind 가 없으면 롯트를 지니는 쪽(COA)으로 본다 — C1 규칙이 그대로 지킨다."""
    zf = _zip_of(client, [{"file_id": "1", "sku": "A", "lot": "FE103C",
                           "name": "a.pdf", "status": "찾음"}])
    assert "COA/A_FE103C_a.pdf" in zf.namelist()


def _done_payload(body):
    block = next(b for b in body.split("\n\n") if b.startswith("event: done"))
    line = next(ln for ln in block.splitlines() if ln.startswith("data: "))
    return json.loads(line[len("data: "):])


def test_summary_reports_rows_skipped_for_empty_sku(client):
    """⛔ 버릴 거면 버렸다고 말해야 한다 — 화면에 아무 표시가 없었다."""
    payload = ("SKU\tDESCRIPTION\tLOT\nA\t앰플\tFE103C\n\t크림\t416022\n")
    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()), \
         patch("app.core.coa_finder.search_drive", return_value=[]):
        r = client.post("/api/coa-finder/search", data={"pasted": payload})
    assert r.status_code == 200
    assert r.text.count("event: row") == 1
    assert _done_payload(r.text)["skipped_no_sku"] == 1


def test_summary_counts_query_failures_separately(client):
    """⛔ 100행 중 8행이 조회 실패했는데 '확인필요 8' 로만 보이면 사용자는
    그것이 애매한 매칭인지 조회가 안 된 것인지 알 수 없다."""
    payload = "SKU\tDESCRIPTION\tLOT\nA\t앰플\tFE103C\nB\t크림\t416022\n"

    def dying_search(creds, query, **k):
        raise RuntimeError("drive down")

    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()), \
         patch("app.core.coa_finder.search_drive", side_effect=dying_search):
        r = client.post("/api/coa-finder/search", data={"pasted": payload})

    done = _done_payload(r.text)
    assert done["counts"]["조회실패"] == 2      # COA 쪽
    assert done["counts"].get("확인필요", 0) == 0
    assert done["msds_failed"] == 2             # ⛔ MSDS 실패도 세지 않고 있었다


def test_fetch_file_aborts_between_chunks_once_the_budget_is_gone():
    """⛔ 다 받은 뒤에 재면 이미 통째로 메모리에 들고 있는 것이다. file_id 는
    클라이언트가 주는 임의 값이라 '실측 COA 는 350KB' 라는 전제가 성립하지 않는다."""
    from app.api import coa_finder_api as api

    class _Downloader:
        """청크마다 40바이트씩 준다. 예산을 안 보면 200바이트를 다 받는다."""
        def __init__(self, buf, req, chunksize=None):
            self._buf, self._n = buf, 0

        def next_chunk(self):
            self._n += 1
            self._buf.write(b"x" * 40)
            return None, self._n >= 5

    with patch("googleapiclient.discovery.build", return_value=MagicMock()), \
         patch("googleapiclient.http.MediaIoBaseDownload", _Downloader):
        with pytest.raises(api._DownloadBudgetExceeded) as e:
            api._fetch_file(MagicMock(), "huge", 50)

    # 잰 만큼만 말한다 — 확인하지 않은 총 크기를 주장하지 않는다
    assert e.value.received == 80


def test_fetch_file_returns_a_file_that_fits_the_budget():
    """반대 방향 — 예산 안에 드는 파일까지 끊으면 기능이 죽는다."""
    from app.api import coa_finder_api as api

    class _Downloader:
        def __init__(self, buf, req, chunksize=None):
            self._buf, self._n = buf, 0

        def next_chunk(self):
            self._n += 1
            self._buf.write(b"x" * 40)
            return None, self._n >= 2

    with patch("googleapiclient.discovery.build", return_value=MagicMock()), \
         patch("googleapiclient.http.MediaIoBaseDownload", _Downloader):
        assert api._fetch_file(MagicMock(), "ok", 500) == b"x" * 80


def test_download_records_an_item_that_blew_the_budget(client):
    """중단한 항목이 조용히 빠지면 안 된다 — 못 받은 목록에 사유가 남아야 한다."""
    import io as _io
    import zipfile

    from app.api import coa_finder_api as api

    items = [{"file_id": "huge", "sku": "A", "lot": "L1", "name": "big.pdf",
              "status": "찾음"}]

    def fake_fetch(creds, file_id, budget):
        raise api._DownloadBudgetExceeded(80)

    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()), \
         patch("app.api.coa_finder_api._fetch_file", side_effect=fake_fetch):
        r = client.post("/api/coa-finder/download", json={"items": items})

    assert r.status_code == 200
    zf = zipfile.ZipFile(_io.BytesIO(r.content))
    assert zf.namelist() == ["_받지못한_목록.txt"]
    note = zf.read("_받지못한_목록.txt").decode("utf-8")
    assert "L1" in note
    assert "적어도" in note        # 재지 않은 총 크기를 주장하지 않는다


def test_done_payload_publishes_the_download_caps(client):
    """⛔ 화면이 서버 상한의 사본을 들고 있으면 조용히 어긋난다 — 서버가 알려준다."""
    from app.api.coa_finder_api import _MAX_DOWNLOAD_BYTES, _MAX_DOWNLOAD_ITEMS

    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()), \
         patch("app.core.coa_finder.search_drive", return_value=[]):
        r = client.post("/api/coa-finder/search",
                        data={"pasted": "SKU\tDESCRIPTION\tLOT\nA\t앰플\tFE103C\n"})

    done = _done_payload(r.text)
    assert done["max_download_items"] == _MAX_DOWNLOAD_ITEMS
    assert done["max_download_bytes"] == _MAX_DOWNLOAD_BYTES


def test_download_headers_state_how_much_of_it_arrived(client):
    """⛔ 반쪽짜리 ZIP 이 평범한 ZIP 과 똑같이 내려온다 — 열어보기 전에는 모른다.

    ZIP **안에만** 진실이 있으면 화면은 성공한 것처럼 보인다. 헤더로 밖에도 적는다.
    """
    items = [{"file_id": "ok", "sku": "A", "lot": "L1", "name": "a.pdf",
              "status": "찾음"},
             {"file_id": "bad", "sku": "B", "lot": "L2", "name": "b.pdf",
              "status": "찾음"}]

    def fake_fetch(creds, file_id, budget):
        if file_id == "bad":
            raise RuntimeError("403")
        return b"%PDF"

    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()), \
         patch("app.api.coa_finder_api._fetch_file", side_effect=fake_fetch):
        r = client.post("/api/coa-finder/download", json={"items": items})

    assert r.headers["X-Coa-Items-Requested"] == "2"
    assert r.headers["X-Coa-Items-Written"] == "1"
    assert r.headers["X-Coa-Items-Skipped"] == "1"
    # ⛔ 헤더 값은 ASCII 만 — 비ASCII 는 전송 계층에서 깨진다
    for k in ("X-Coa-Items-Requested", "X-Coa-Items-Written", "X-Coa-Items-Skipped"):
        r.headers[k].encode("ascii")


def test_download_headers_on_a_whole_archive(client):
    """반대 방향 — 멀쩡한 ZIP 이 반쪽으로 보이면 그 경고도 소음이 된다."""
    zf = _zip_of(client, [{"file_id": "1", "sku": "A", "lot": "L1", "name": "a.pdf",
                           "status": "찾음"}])
    assert "_받지못한_목록.txt" not in zf.namelist()


def test_failure_list_separates_the_cause_from_the_casualties(client):
    """⛔ '남은 예산을 넘겨 중단' 과 '상한 초과로 받지 못함' 은 읽어서 구분되지 않는다.
    원인이 된 파일과 그 때문에 밀린 파일은 다른 사실이다."""
    import io as _io
    import zipfile

    from app.api import coa_finder_api as api

    items = [
        {"file_id": "1", "sku": "A", "lot": "L1", "name": "a.pdf", "status": "찾음"},
        {"file_id": "2", "sku": "B", "lot": "L2", "name": "b.pdf", "status": "찾음"},
        {"file_id": "3", "sku": "C", "lot": "L3", "name": "c.pdf", "status": "찾음"},
    ]

    def fake_fetch(creds, file_id, budget):
        if 40 > budget:
            raise api._DownloadBudgetExceeded(40)
        return b"x" * 40

    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()), \
         patch("app.api.coa_finder_api._fetch_file", side_effect=fake_fetch), \
         patch("app.api.coa_finder_api._MAX_DOWNLOAD_BYTES", 50):
        r = client.post("/api/coa-finder/download", json={"items": items})

    note = zipfile.ZipFile(_io.BytesIO(r.content)).read(
        "_받지못한_목록.txt").decode("utf-8")
    cause = note.index("여기서 남은 용량 예산이 바닥났습니다")
    casualty = note.index("앞 파일에서 용량이 차서 시도하지 않았습니다")
    assert cause < casualty, "원인이 된 파일이 밀린 파일보다 먼저 나와야 한다"
    assert note.index("L2") < note.index("L3")


def test_unconfirmed_list_describes_a_query_failure_accurately(client):
    """⛔ 조회실패를 '판정 상태가 전달되지 않았습니다' 라고 적으면 거짓말이다 —
    전달은 됐고, 그 값이 '조회실패' 였다."""
    import io as _io
    import zipfile

    zf = _zip_of(client, [{"file_id": "1", "sku": "A", "lot": "L1", "name": "a.pdf",
                           "status": "조회실패"}])
    note = zf.read("_확인필요_목록.txt").decode("utf-8")
    assert "전달되지 않았습니다" not in note
    assert "조회" in note


_JS = "app/static/coa_finder.js"


def _js_source():
    with open(_JS, encoding="utf-8") as fh:
        return fh.read()


def test_frontend_sends_verdict_status_and_kind_with_each_item():
    """⛔ 프론트가 판정을 안 실어 보내면 서버는 전부 확인필요로 내보낸다 —
    맞는 방향으로 무너지지만 확정 문서까지 경고가 붙어 경고가 소음이 된다."""
    src = _js_source()
    assert "dataset.status" in src
    assert "dataset.kind" in src
    assert "status:" in src and "kind:" in src


def test_frontend_summary_states_what_was_searched():
    """⛔ '없음 41' 만 보이면 사용자는 도구가 고장났다고 읽는다."""
    src = _js_source()
    assert "COA" in src                      # 개수가 COA 기준임을 밝힌다
    assert "파일명" in src and "본문" in src   # 탐색 범위


def test_frontend_warns_when_every_row_is_none():
    """전 행이 없음이면 조용한 전멸을 시끄럽게 만든다."""
    src = _js_source()
    assert "cf-allnone" in src


def test_frontend_knows_the_query_failure_status():
    """⛔ 상태 표에 없으면 조회실패 행이 아무 표시 없이 그려진다."""
    assert "조회실패" in _js_source()


def test_frontend_states_rows_skipped_for_empty_sku():
    src = _js_source()
    assert "skipped_no_sku" in src
    assert "cf-skipped" in src


def test_frontend_renders_the_product_coa_column():
    """⛔ 표 머리글은 마크업에 있어야 한다 — 스크립트가 넣어 주는 머리글은
    스크립트가 낡거나 죽으면 열이 통째로 사라지고, 그때 에러는 안 난다."""
    with open("app/static/coa_finder.html", encoding="utf-8") as fh:
        html = fh.read()
    assert "COA(제품)" in html
    assert "COA(제품)" not in _js_source(), "머리글을 JS 가 다시 심고 있다"
    assert "product_coa" in _js_source()


def test_frontend_does_not_let_the_row_checkbox_take_product_coa():
    """⛔ 이 열은 화면에서 가장 조심스러운 것이어야 한다 — 가장 도움이 되어
    보이는 것이 아니라. 행 체크·전체선택으로 딸려 나가면 안 된다."""
    src = _js_source()
    assert "cf-pick-product" in src
    assert "dataset.optin" in src or "dataset.optIn" in src


def test_frontend_carries_file_size_for_batching():
    """크기를 안 실으면 나눠 받기가 추측이 된다 — 조회 결과에 이미 들어 있다."""
    assert "dataset.size" in _js_source()


def test_frontend_reads_the_partial_archive_headers():
    """⛔ 반쪽 ZIP 을 성공처럼 보여주면 안 된다 — 헤더를 읽어 화면에 적는다."""
    src = _js_source()
    assert "X-COA-Items-Skipped" in src or "x-coa-items-skipped" in src
    assert "_받지못한_목록.txt" in src


def test_frontend_offers_a_download_per_document_kind():
    """⛔ 버튼은 마크업에 있어야 한다 — JS 가 심으면 스크립트가 낡을 때
    버튼이 통째로 사라지고 그때 에러는 안 난다 (COA(제품) 열과 같은 규칙)."""
    with open("app/static/coa_finder.html", encoding="utf-8") as fh:
        html = fh.read()
    assert "cf-dl-coa" in html and "COA만 받기" in html
    assert "cf-dl-msds" in html and "MSDS만 받기" in html
    assert "cf-dl-none" in html
    assert "cf-only-none" in html


def test_frontend_passes_an_explicit_kind_to_download():
    """⛔ `addEventListener("click", download)` 로 넘기면 이벤트 객체가
    kindFilter 자리에 들어간다 — truthy 라 **모든 파일이 걸러져 0건**이 되고,
    화면엔 "받을 파일을 선택해주세요" 만 뜬다 (에러가 아니다)."""
    src = _js_source()
    assert 'download(null)' in src
    assert 'download("coa")' in src
    assert 'download("msds")' in src
    assert 'addEventListener("click", download)' not in src


def test_frontend_states_what_a_kind_filter_left_out():
    """⛔ 선택한 것보다 적게 받으면 그 사실을 말해야 한다 — 조용히 작아진
    ZIP 은 이 페이지가 막으려는 바로 그 실패다."""
    src = _js_source()
    assert "cf-kind" in src
    assert "제외했습니다" in src


def test_frontend_names_a_single_kind_archive_after_its_contents():
    """서버의 Content-Disposition 과 같은 규칙 — 두 곳이 갈리면 안 된다."""
    src = _js_source()
    assert "ZIP_BASE" in src
    assert '"coa_msds"' in src


def test_none_filter_only_hides_and_says_so():
    """⛔ 필터가 받기 대상까지 조용히 바꾸면 안 된다 — 화면이 그렇게 말한다."""
    with open("app/static/coa_finder.html", encoding="utf-8") as fh:
        html = fh.read()
    assert "화면만 가립니다" in html
    src = _js_source()
    # 받기는 여전히 표의 모든 행을 훑는다 (hidden 을 보지 않는다)
    assert "hidden" not in src.split("async function download(")[1].split("planBatches")[0]


def _run_none_list(rows):
    """noneListCsv 를 node 로 **실제 실행**한다.

    ⛔ 문자열 검사로는 못 지킨다. 조건 하나가 뒤집히면 조회실패 행이
       'COA 없음' 목록에 실려 나가는데, 그건 에러가 아니라 **사람이 그 목록을
       믿고 움직이는** 조용한 오답이다.
    """
    import json as _json
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node 없음 — 개발 환경 전용 검사")

    fn = _extract_js_function(_js_source(), "noneListCsv")
    driver = (fn + "\nconst out = noneListCsv(" + _json.dumps(rows)
              + ");\nconsole.log(JSON.stringify(out));\n")
    r = subprocess.run([node, "-e", driver], capture_output=True, text=True,
                       timeout=20, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return _json.loads(r.stdout)


def _row(sku, lot, status, note="", description="앰플"):
    return {"sku": sku, "description": description, "lot": lot,
            "coa": {"status": status, "note": note}}


def test_none_list_holds_only_the_rows_with_no_coa():
    got = _run_none_list([
        _row("A", "FE103C", "없음", "파일명에 이 롯트가 든 파일이 없습니다"),
        _row("B", "F31C28", "찾음"),
        _row("C", "MO388", "확인필요"),
        _row("D", "6752FE", "여러건"),
    ])
    assert got["none"] == 1
    assert "FE103C" in got["csv"]
    for other in ("F31C28", "MO388", "6752FE"):
        assert other not in got["csv"]


def test_none_list_never_counts_a_failed_query_as_missing():
    """⛔ 조회실패는 판정이 아니다 — '모른다' 를 '없다' 로 바꾸면, 사람이
    그 목록을 근거로 재발급을 요청하거나 있는 서류를 없다고 보고한다."""
    got = _run_none_list([
        _row("A", "FE103C", "없음"),
        _row("B", "F31C28", "조회실패", "드라이브 조회에 실패했습니다"),
        _row("C", "MO388", "조회실패"),
    ])
    assert got["none"] == 1 and got["failed"] == 2
    assert "F31C28" not in got["csv"] and "MO388" not in got["csv"]
    # ⛔ 화면에만 있는 경고는 파일이 손을 떠나는 순간 사라진다 — 파일 안에 적는다
    assert "조회실패 2건" in got["csv"]


def test_none_list_has_no_footer_when_nothing_failed():
    """매번 붙는 경고는 곧 아무도 안 읽는다."""
    got = _run_none_list([_row("A", "FE103C", "없음")])
    assert "조회실패" not in got["csv"]


def test_none_list_quotes_cells_so_a_comma_cannot_shift_a_column():
    """제품명·사유에는 쉼표가 흔하다 — 안 감싸면 열이 밀려 롯트 칸에 설명이 들어간다."""
    got = _run_none_list([
        _row("A", "FE103C", "없음", '없습니다, 본문은 "검색"하지 않습니다',
             description="앰플, 100ml"),
    ])
    line = got["csv"].splitlines()[1]
    assert line.startswith('"A","앰플, 100ml","FE103C","없음",')
    assert '""검색""' in line          # 따옴표는 겹쳐서 이스케이프한다


def test_none_list_is_empty_when_every_row_has_a_coa():
    got = _run_none_list([_row("A", "FE103C", "찾음")])
    assert got["none"] == 0


def test_frontend_refuses_to_hand_over_an_empty_none_list():
    """⛔ 빈 파일이 답처럼 보이면 안 된다 — '없음이 없다' 와 '조회를 안 했다' 는
    다른 사실이라 문구도 갈린다."""
    src = _js_source()
    assert "먼저 조회를 실행해주세요" in src
    assert "내려받을 목록이 비어 있습니다" in src


def test_none_list_csv_carries_a_bom_for_excel():
    """⛔ BOM 이 없으면 엑셀이 한글을 깨서 연다 — 내보내기가 고장난 것처럼 보인다."""
    assert "ufeff" in _js_source()


def _extract_js_function(src, name):
    """`function <name>(` 부터 짝이 맞는 닫는 중괄호까지 떼어낸다.

    ⚠️ 이 함수 안에 중괄호가 든 문자열 리터럴을 두지 마라 — 세는 것이 어긋난다.
    """
    start = src.index("function " + name + "(")
    depth, i = 0, src.index("{", start)
    while True:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1


def _run_batch_plan(items, caps):
    """planBatches 를 node 로 **실제 실행**한다.

    ⛔ 이 로직은 문자열 검사로 지킬 수 없다. 상한 비교가 하나 어긋나면
       ① 상한을 넘긴 요청이 나가 서버가 반쪽 ZIP 을 만들거나
       ② 어떤 배치에도 못 드는 항목에서 **무한 루프**가 돈다 (화면이 멈춘다).
    """
    import json as _json
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node 없음 — 개발 환경 전용 검사")

    fn = _extract_js_function(_js_source(), "planBatches")
    driver = (fn + "\nconst out = planBatches("
              + _json.dumps(items) + ", " + _json.dumps(caps)
              + ");\nconsole.log(JSON.stringify(out));\n")
    r = subprocess.run([node, "-e", driver], capture_output=True, text=True,
                       timeout=20)
    assert r.returncode == 0, r.stderr
    return _json.loads(r.stdout)


_CAPS = {"items": 3, "bytes": 100}


def test_batch_plan_keeps_one_request_when_it_fits():
    got = _run_batch_plan([{"size": 10}, {"size": 20}], _CAPS)
    assert len(got) == 1 and len(got[0]) == 2


def test_batch_plan_splits_on_the_byte_cap():
    got = _run_batch_plan([{"size": 60}, {"size": 60}, {"size": 10}], _CAPS)
    assert [[i["size"] for i in b] for b in got] == [[60], [60, 10]]


def test_batch_plan_splits_on_the_item_cap():
    got = _run_batch_plan([{"size": 1}] * 7, _CAPS)
    assert [len(b) for b in got] == [3, 3, 1]


def test_batch_plan_gives_an_oversized_file_its_own_batch():
    """⛔ 어떤 배치에도 못 드는 파일을 조용히 버리지 마라 — 혼자 보내서
    서버가 거절하게 하고, 그 사유는 _받지못한_목록.txt 에 남는다.
    (여기서 루프가 안 끝나면 화면이 멈춘다 — 그래서 실제로 돌려 본다)"""
    got = _run_batch_plan([{"size": 10}, {"size": 500}, {"size": 10}], _CAPS)
    assert [[i["size"] for i in b] for b in got] == [[10], [500], [10]]


def test_batch_plan_treats_a_missing_size_as_zero():
    """Drive 는 구글 문서 형식에 size 를 주지 않는다 — 없다고 멈추면 안 된다.
    (넘칠 수는 있지만 그건 서버가 자르고 헤더가 알린다)"""
    got = _run_batch_plan([{"size": None}, {}, {"size": 10}], _CAPS)
    assert [len(b) for b in got] == [3]


def test_batch_plan_falls_back_to_one_request_without_caps():
    """⛔ 서버가 상한을 안 알려주면 나누지 않는다 — 상한은 서버가 알고,
    서버는 그 경우에도 사실대로 반쪽 ZIP + 목록을 만든다."""
    got = _run_batch_plan([{"size": 10}] * 9, None)
    assert len(got) == 1 and len(got[0]) == 9


def test_zip_name_truncates_by_utf8_bytes_not_characters():
    """⛔ 180자를 그대로 자르면 ext4·macOS 의 255바이트 상한을 넘을 수 있다."""
    from app.api.coa_finder_api import DownloadItem, _MAX_ZIP_NAME_BYTES, _zip_name

    item = DownloadItem(file_id="x", sku="A", lot="L", name="가" * 200)
    name = _zip_name(item)
    # ⛔ 폴더까지 넣어 재지 마라 — 255바이트 상한은 경로가 아니라 **이름 성분**의
    #    것이다. 그리고 자르기가 폴더를 먹으면 그 파일만 조용히 다른 곳에 떨어진다
    folder, _, base = name.partition("/")
    assert folder == "COA"
    encoded = base.encode("utf-8")
    assert len(encoded) <= _MAX_ZIP_NAME_BYTES
    # 글자 중간이 잘렸으면 여기서 디코딩 오류가 나거나 원문과 달라진다
    assert encoded.decode("utf-8") == base


# ── "토큰은 살아 있는데 그 드라이브의 멤버가 아니다" 신호 — 2026-08-31 ────────
#
# self_check 의 계정별 토큰 점검(`google_account_health`)은 토큰이 죽었는지만
# 본다. 그런데 토큰이 멀쩡해도 그 계정이 진짜 인증서가 있는 공유드라이브의
# 멤버가 아니면 검색 결과는 조용히 전부 '없음' 이다 — 이건 로그 신호로만 남긴다
# (판정을 내리는 검사가 아니다).


def _lots_payload(n: int) -> str:
    lots = ["FE103C", "416022", "F31C28", "MO388", "6752FE", "FE161", "2UE0003"]
    assert n <= len(lots)
    lines = ["SKU\tDESCRIPTION\tLOT"]
    for i in range(n):
        lines.append(f"SKU{i}\t앰플 {i}\t{lots[i]}")
    return "\n".join(lines) + "\n"


def test_all_none_at_five_lots_logs_a_named_warning(client, caplog):
    """5행 이상이 전부 '없음' 이면 요청자 이메일을 남긴 경보가 뜬다."""
    import logging
    caplog.set_level(logging.WARNING, logger="app.api.coa_finder_api")
    payload = _lots_payload(5)
    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()), \
         patch("app.core.coa_finder.search_drive", return_value=[]):
        r = client.post("/api/coa-finder/search", data={"pasted": payload})
    assert r.status_code == 200
    warnings = [rec for rec in caplog.records if rec.message == "coa_finder_all_none"]
    assert len(warnings) == 1, caplog.text
    assert warnings[0].user == "tester@skin1004korea.com"
    assert warnings[0].total == 5


def test_all_none_under_five_lots_does_not_warn(client, caplog):
    """1행이 우연히 '없음' 인 것은 흔하다 — 신호로 보지 않는다."""
    import logging
    caplog.set_level(logging.WARNING, logger="app.api.coa_finder_api")
    payload = _lots_payload(1)
    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()), \
         patch("app.core.coa_finder.search_drive", return_value=[]):
        r = client.post("/api/coa-finder/search", data={"pasted": payload})
    assert r.status_code == 200
    assert not any(rec.message == "coa_finder_all_none" for rec in caplog.records)


def test_four_lots_all_none_does_not_warn(client, caplog):
    """상한 바로 아래(4행)에서는 아직 신호를 켜지 않는다."""
    import logging
    caplog.set_level(logging.WARNING, logger="app.api.coa_finder_api")
    payload = _lots_payload(4)
    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()), \
         patch("app.core.coa_finder.search_drive", return_value=[]):
        r = client.post("/api/coa-finder/search", data={"pasted": payload})
    assert r.status_code == 200
    assert not any(rec.message == "coa_finder_all_none" for rec in caplog.records)


def test_five_lots_with_one_found_does_not_warn(client, caplog):
    """하나라도 찾았으면 '멤버가 아니다' 가설이 성립하지 않는다 — 신호를 켜지 않는다."""
    import logging
    caplog.set_level(logging.WARNING, logger="app.api.coa_finder_api")
    payload = _lots_payload(5)

    def _fake_search(creds, query, max_results=25, exact_name=None, widen=True):
        # FE103C 만 파일명에 정확히 매칭되는 COA 를 준다 — 나머지는 없음
        if exact_name == "FE103C":
            return [{"id": "1", "name": "FE103C COA.pdf", "size": 10,
                     "webViewLink": "http://x"}]
        return []

    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()), \
         patch("app.core.coa_finder.search_drive", side_effect=_fake_search):
        r = client.post("/api/coa-finder/search", data={"pasted": payload})
    assert r.status_code == 200
    assert not any(rec.message == "coa_finder_all_none" for rec in caplog.records)


@pytest.mark.parametrize("kinds", [("coa",), ("msds",), ("coa", "msds")])
def test_latest_search_results_flow_into_zip_without_older_versions(client, kinds):
    """Use real search classification/SSE/ZIP paths; only Drive I/O is replaced."""
    import io
    import zipfile

    creds = object()
    old_date, new_date = "2026-09-01T00:00:00Z", "2026-09-08T03:30:00Z"
    downloads = []

    def search(credential, query, **kwargs):
        assert credential is creds
        kind = "msds" if "MSDS" in query else "coa"
        name = "MSDS Toning Toner 210ml" if kind == "msds" else "COA_FE103C"
        return [{"id": kind + "-" + version, "name": name + "_" + version + ".pdf",
                 "size": 100, "modifiedTime": modified,
                 "webViewLink": "https://drive.example/" + kind + "-" + version}
                for version, modified in (("old", old_date), ("new", new_date))]

    def fetch(credential, file_id, budget):
        assert credential is creds and file_id in {"coa-new", "msds-new"}
        downloads.append(file_id)
        return file_id.encode()

    with patch("app.api.coa_finder_api._credentials", return_value=creds) as credentials, \
         patch("app.core.coa_finder.search_drive", side_effect=search), \
         patch("app.api.coa_finder_api._fetch_file", side_effect=fetch):
        response = client.post("/api/coa-finder/search", data={
            "pasted": "SKU\tDESCRIPTION\tLOT\nEUSKA022\tMadagascar Centella Toning Toner 210ml\tFE103C\n",
        })
        assert response.status_code == 200
        blocks = [block for block in response.text.split("\n\n") if block.startswith("event: row")]
        assert len(blocks) == 1
        row = json.loads(next(line[6:] for line in blocks[0].splitlines() if line.startswith("data: ")))
        for kind in ("coa", "msds"):
            verdict = row[kind]
            assert verdict["status"] == cf.FOUND
            assert [file["id"] for file in verdict["files"]] == [kind + "-new"]
            assert verdict["files"][0]["modified"] == new_date
            assert "최신" in verdict["note"] and "2026-09-08" in verdict["note"]
        assert _done_payload(response.text)["counts"][cf.FOUND] == 1
        items = [{"file_id": file["id"], "name": file["name"], "sku": row["sku"],
                  "lot": row["lot"] if kind == "coa" else "", "kind": kind,
                  "status": row[kind]["status"]}
                 for kind in kinds for file in row[kind]["files"]]
        archive = client.post("/api/coa-finder/download", json={"items": items})
        assert archive.status_code == 200
        assert credentials.call_args_list[0].args == (_User.email,)
        assert credentials.call_args_list[-1].args == (_User.email,)
    assert set(downloads) == {kind + "-new" for kind in kinds}
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zf:
        names = zf.namelist()
        assert len(names) == len(kinds)
        assert {name.split("/", 1)[0] for name in names} == {kind.upper() for kind in kinds}
        assert not any("old" in name or name.startswith("_") for name in names)
        for name in names:
            kind = name.split("/", 1)[0].lower()
            assert zf.read(name) == (kind + "-new").encode()
            assert ("FE103C" in name) is (kind == "coa")
