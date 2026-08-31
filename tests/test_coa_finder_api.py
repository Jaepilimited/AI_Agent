"""COA 찾기 엔드포인트."""
import json
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.auth_middleware import get_current_user
from app.core import coa_finder as cf


class _User:
    email = "tester@skin1004korea.com"
    role = "user"


@pytest.fixture
def client():
    # ⛔ 개발 PC `.env` 의 MIGRATED_REDIRECT_URL 이 앱 생성 시점에 리다이렉트
    #    미들웨어로 박힌다 (tests/test_router.py 와 같은 원인). 이미 만들어진
    #    `app.main.app` 싱글턴을 그대로 쓰면 모든 요청이 307 로 튕기므로,
    #    설정을 끈 뒤 새로 만든 앱으로 테스트한다.
    from app.config import get_settings
    os.environ["MIGRATED_REDIRECT_URL"] = ""
    get_settings.cache_clear()
    from app.main import create_app
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _User()
    yield TestClient(app)
    app.dependency_overrides.clear()


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
                        msds=cf.Verdict(cf.NONE, (), ""))
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
    oversized = b"x" * (5 * 1024 * 1024 + 1024)
    r = client.post(
        "/api/coa-finder/search",
        files={"file": ("big.xlsx", oversized, "application/octet-stream")},
    )
    assert r.status_code == 400
    assert "5MB" in r.json()["detail"]


def test_search_reports_missing_header_column(client):
    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()):
        r = client.post("/api/coa-finder/search",
                        data={"pasted": "SKU\tDESCRIPTION\nA\t앰플\n"})
    assert r.status_code == 400
    assert "LOT" in r.json()["detail"]


def test_page_is_served(client):
    r = client.get("/coa-finder")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


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

    items = [{"file_id": "ok", "sku": "A", "lot": "FE103C", "name": "a.pdf"},
             {"file_id": "bad", "sku": "B", "lot": "416022", "name": "b.pdf"}]

    def fake_fetch(creds, file_id):
        if file_id == "bad":
            raise RuntimeError("403")
        return b"%PDF-1.4 fake"

    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()), \
         patch("app.api.coa_finder_api._fetch_file", side_effect=fake_fetch):
        r = client.post("/api/coa-finder/download", json={"items": items})

    assert r.status_code == 200
    zf = zipfile.ZipFile(_io.BytesIO(r.content))
    names = zf.namelist()
    assert "A_FE103C_a.pdf" in names
    assert "_받지못한_목록.txt" in names
    assert "416022" in zf.read("_받지못한_목록.txt").decode("utf-8")
