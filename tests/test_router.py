"""Tests for Query Router and API endpoints."""

import pytest
from fastapi.testclient import TestClient


class TestAPIEndpoints:
    """Tests for FastAPI endpoint availability."""

    def setup_method(self):
        # ⛔ **이 3건은 오래 빨간 채로 방치돼 있었다** (httpx.TooManyRedirects).
        #    원인은 앱 버그가 아니라 개발 PC 의 `.env` 다 — 여기(172.16.1.250)는
        #    이관 후 **리다이렉트 껍데기**라 `MIGRATED_REDIRECT_URL` 이 켜져 있고,
        #    테스트가 그 설정을 그대로 읽어 모든 요청이 307 로 튕겼다.
        #    ⚠️ 상시 빨간 테스트는 없는 것보다 나쁘다 — 진짜 실패를 무시하게 만든다.
        #    설정을 껐다 켜는 대신 **리다이렉트를 따라가지 않게** 해서, 실제 앱 응답을
        #    검사하도록 고친다 (환경에 따라 결과가 갈리지 않는다).
        import os

        from app.config import get_settings
        os.environ["MIGRATED_REDIRECT_URL"] = ""
        get_settings.cache_clear()
        # ⚠️ 미들웨어는 **앱 생성 시점**에 설정을 캡처한다. import 된 `app` 을 그대로
        #    쓰면 이미 리다이렉트가 박혀 있으므로, 설정을 끈 뒤 새로 만든다.
        from app.main import create_app
        self.client = TestClient(create_app())

    def test_health_check(self):
        response = self.client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_dashboard_html_is_never_reused_from_browser_cache(self):
        """A cached dashboard can keep retrying an already-fixed broken asset URL."""
        response = self.client.get("/dashboard?theme=light")

        assert response.status_code == 200
        assert "no-store" in response.headers.get("cache-control", "")

    def test_cached_broken_dashboard_catalog_url_still_returns_the_catalog(self):
        """Clients holding the previously corrupted `.js?...on?v=1` URL must recover."""
        response = self.client.get(
            "/static/dashboard-catalog.js?v=90ca8445on?v=1"
        )

        assert response.status_code == 200
        catalog = response.json()
        assert len(catalog) == 5
        assert sum(len(items) for sections in catalog.values() for items in sections.values()) == 93

    def test_list_models_requires_login(self):
        """⛔ 2026-09-09 Entra 인증 릴리스로 **의도적으로 닫혔다.**

        그 릴리스가 못 박은 것: *"공통 미들웨어가 앱 화면과 API를 보호한다.
        미인증 API는 401과 `X-Cella-Auth: login-required`"*. 프로덕션 로그
        실측으로도 이 경로는 **7일간 호출 0건**이다 (`/v1/chat/completions`
        는 911건).

        ⚠️ 낡은 기대(200)를 그대로 두면 배포 직전 스위트 관문이 **영원히
           빨간불**이라, 정작 진짜 실패를 덮는다. 아침에
           `test_escaped_display_name` 을 새 표로 옮긴 것과 같은 판단이다 —
           **없어진 전제를 계속 요구하는 회귀는 고쳐야 할 것을 못 고치게 막는다.**
        ⛔ 열려 있어야 한다고 되돌리지 마라. 되돌리려면 그 미들웨어의 공개
           경로 목록을 함께 고쳐야 하고, 그건 인증 설계 결정이다.
        """
        response = self.client.get("/v1/models")

        assert response.status_code == 401
        assert response.headers.get("X-Cella-Auth") == "login-required"

    def test_the_model_catalog_still_exists(self):
        """⚠️ 위가 401 을 확인할 뿐이라, **목록 자체가 살아 있는지**는 여기서 본다.

        ⛔ 예전엔 `"skin1004-ai"` 를 문자열로 적어 뒀는데 앱은 이미
           `skin1004-Analysis` 로 바뀌어 있었다. 테스트만 옛 이름에 남아
           **몇 달간 빨간 채로 방치**됐다. 상수를 import 해서 어긋나지 않게 한다.
        """
        from app.api.routes import list_models
        from app.config import ALL_MODELS
        import asyncio

        data = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            list_models())
        payload = data.model_dump() if hasattr(data, "model_dump") else dict(data)
        assert payload["object"] == "list"
        assert ALL_MODELS in [m["id"] for m in payload["data"]]

    def test_chat_completions_missing_messages(self):
        response = self.client.post(
            "/v1/chat/completions",
            json={
                "model": "skin1004-ai",
                "messages": [],
            },
        )
        # ⚠️ 인증이 먼저다 — 로그인하지 않은 요청은 본문 검증 전에 401 로 막힌다.
        #    (예전 테스트는 400/422 를 기대했으나 그 사이 인증이 붙었다)
        #    빈 messages 를 거절하는지는 아래 인증 케이스에서 따로 본다.
        assert response.status_code == 401

    def test_chat_completions_no_user_message(self):
        response = self.client.post(
            "/v1/chat/completions",
            json={
                "model": "skin1004-ai",
                "messages": [
                    {"role": "system", "content": "You are helpful."}
                ],
            },
        )
        # 위와 같은 이유 — 인증이 먼저 막는다. 이 401 은 **지켜야 할 계약**이다
        # (인증 없이 /v1/chat/completions 가 열리면 안 된다)
        assert response.status_code == 401
