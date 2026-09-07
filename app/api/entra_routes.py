# -*- coding: utf-8 -*-
"""Entra ID(OIDC) 로그인 엔드포인트.

기존 ID/PW 로그인과 **나란히** 둔다 (전환기). EntraID 가 자리를 잡으면 로컬
비밀번호 경로와 재설정 2경로를 걷어낸다 — 그때까지 둘 다 살아 있어야
누구도 갇히지 않는다.

⚠️ 자격증명이 없으면 세 엔드포인트 모두 **503 + 읽을 수 있는 안내**를 준다.
   기능이 없는 것과 고장난 것을 사용자가 구분할 수 있어야 한다.
"""

from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import get_settings
from app.core import entra_auth

logger = structlog.get_logger(__name__)

entra_router = APIRouter(prefix="/auth/entra", tags=["auth"])

# ⛔ **회신 URL 은 우리가 고르는 것이 아니라 Entra 앱 등록에 적힌 값이다.**
#    IT 가 등록한 값이 `/users/auth/openid_connect/callback` 이다 — GitLab
#    (OmniAuth) 의 관례적 경로이고 우리 앱의 경로 체계가 아니다. 그런데
#    Entra 는 요청의 redirect_uri 가 등록값과 **정확히 일치**해야만 코드를
#    돌려준다 (2026-09-07 실측: 우리 경로로 보내니 AADSTS50011).
#    그래서 등록값 그대로 한 자리를 더 연다. 두 경로 모두 **같은 핸들러**다.
# ⚠️ 셀라 전용 앱 등록을 새로 받게 되면 그때 `/auth/entra/callback` 하나로
#    되돌리고 이 별칭을 지운다 — 남의 관례가 우리 URL 체계에 남을 이유가 없다.
entra_alias_router = APIRouter(tags=["auth"])


def _redirect_uri(request: Request) -> str:
    """회신 URL 은 **설정값이 이긴다.**

    ⛔ Host 헤더로 만들면 접속 주소가 여러 개일 때 등록되지 않은 값이 나간다 —
       구글에서 정확히 그 사고를 겪었다 (`http://ai.cravercorp.internal/...`,
       2026-09-02). Entra 는 회신 URL 이 **정확히 일치**해야 하고 대소문자도
       구분하므로, 등록한 값 하나만 쓴다.
    """
    configured = (get_settings().entra_redirect_uri or "").strip()
    if configured:
        return configured
    scheme = "https" if request.url.scheme == "https" else "http"
    return f"{scheme}://{request.headers.get('host', 'localhost:3000')}/auth/entra/callback"


def _notice(title: str, body: str, status: int = 400) -> HTMLResponse:
    from app.api.auth_routes import _notice_page
    return HTMLResponse(status_code=status, content=_notice_page(title, body))


def is_available(request: Request) -> bool:
    """회사 계정 로그인이 지금 **실제로 되는가.**

    ⛔ 판정은 여기 한 곳이다. `/auth/entra/status` 와 `/api/auth/methods` 가
       각자 같은 식을 적으면 언젠가 갈리고, 갈리면 화면은 버튼을 보여주는데
       눌러도 안 되거나 그 반대가 된다 (사본이 갈리는 이 프로젝트의 단골 사고).
    """
    return bool(entra_auth.is_configured()
                and entra_auth.redirect_uri_is_usable(_redirect_uri(request)))


@entra_router.get("/status")
async def entra_status(request: Request):
    """로그인 화면이 이 버튼을 보여줄지 **서버에 묻는다.**

    ⚠️ 프론트에 조건을 박으면 IT 가 값을 준 날 아무도 기억하지 못한다.
    """
    return {"enabled": is_available(request)}


@entra_router.get("/login")
async def entra_login(request: Request, next: str = Query("/")):
    """회사 계정으로 로그인 시작."""
    # ⚠️ 열린 리다이렉트 방지 — 돌아갈 곳은 우리 경로만 허용한다.
    next_path = next if next.startswith("/") and not next.startswith("//") else "/"
    try:
        url = await asyncio.to_thread(
            entra_auth.begin_login, _redirect_uri(request), next_path)
    except entra_auth.EntraUnavailable as unavailable:
        return _notice(unavailable.title, unavailable.body, status=503)
    return RedirectResponse(url=url, status_code=302)


@entra_router.get("/callback")
@entra_alias_router.get("/users/auth/openid_connect/callback")
async def entra_callback(
    request: Request,
    response: Response,
    code: str = Query("", description="Authorization code"),
    state: str = Query("", description="Opaque single-use state"),
    error: str = Query(""),
    error_description: str = Query(""),
):
    """EntraID 가 돌려보낸 결과를 확인하고 셀라 세션을 발급한다."""
    from app.api.auth_api import _create_token, _lookup_brand_filter, _set_cookie

    if error:
        # ⚠️ 사유를 로그에 남긴다 — 안 남기면 "안 된다" 만 듣고 추측하게 된다.
        logger.warning("entra_callback_error", error=error,
                       description=error_description[:200])
        return _notice("회사 계정 로그인에 실패했습니다",
                       error_description or "다시 시도해 주세요.")

    try:
        pending = await asyncio.to_thread(entra_auth.consume_state, state)
        tokens = await asyncio.to_thread(
            entra_auth.exchange_code, code, pending["redirect_uri"],
            pending["code_verifier"])
        claims = await asyncio.to_thread(
            entra_auth.verify_id_token, tokens.get("id_token", ""),
            pending["nonce_hash"])
    except entra_auth.EntraUnavailable as unavailable:
        return _notice(unavailable.title, unavailable.body)
    except Exception as exc:
        logger.error("entra_callback_failed", error_type=type(exc).__name__)
        return _notice("회사 계정 확인에 실패했습니다",
                       "잠시 후 다시 시도하거나, 관리자에게 문의해 주세요.", status=500)

    oid = str(claims["oid"])
    user = await asyncio.to_thread(entra_auth.find_user_by_oid, oid)
    if not user:
        # 첫 로그인 — 기존 계정에 한 번 맞춰 잇는다.
        user = await asyncio.to_thread(entra_auth.match_existing_account, claims)
        if user:
            await asyncio.to_thread(entra_auth.link_oid, int(user["id"]), oid)
            logger.warning("entra_account_linked", user_id=int(user["id"]))

    if not user:
        # ⛔ 여기서 계정을 새로 만들지 않는다. 셀라 계정은 AD 기반으로 발급되며,
        #    아무나 들어오면 권한(FI 열람 등)의 근거가 사라진다.
        logger.warning("entra_no_matching_account")
        return _notice(
            "셀라 계정을 찾지 못했습니다",
            "회사 계정은 확인됐지만 연결된 셀라 계정이 없습니다. "
            "기존 방식으로 한 번 로그인하시거나 관리자에게 문의해 주세요.",
            status=404)

    brand_filter = await asyncio.to_thread(_lookup_brand_filter, int(user["id"]))
    token = _create_token(int(user["id"]), claims.get("preferred_username", "") or "",
                          brand_filter=brand_filter,
                          role=user.get("role", "user"))
    redirect = RedirectResponse(url=pending.get("next_path") or "/", status_code=303)
    _set_cookie(redirect, token)
    logger.warning("entra_login_success", user_id=int(user["id"]))
    return redirect
