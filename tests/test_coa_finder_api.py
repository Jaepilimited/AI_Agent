"""COA 찾기 엔드포인트."""
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.auth_middleware import get_current_user


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
