# -*- coding: utf-8 -*-
"""흐름 API 는 admin 전용이고, 그래프를 그대로 돌려준다."""
import inspect


def test_endpoint_is_registered():
    from app.api.admin_api import admin_router
    paths = {r.path for r in admin_router.routes}
    assert "/api/admin/flow" in paths


def test_endpoint_requires_admin():
    """⛔ 권한 판정은 서버에서 한다 — 프론트가 탭을 숨기는 것에 기대지 않는다."""
    from app.api import admin_api
    sig = inspect.signature(admin_api.get_flow)
    deps = [p.default for p in sig.parameters.values()]
    assert any(getattr(d, "dependency", None) is admin_api._require_admin
               for d in deps), "_require_admin 의존성이 없다"
