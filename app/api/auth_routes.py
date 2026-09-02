"""OAuth2 authentication endpoints for Google Workspace."""

import asyncio
import html
import re

import jwt
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api.auth_middleware import get_current_user, get_optional_user
from app.config import get_settings
from app.core.google_auth import GoogleAuthManager
from app.core.google_oauth_state import consume_state, issue_state
from app.core import password_reset_google
from app.core.personal_briefing import get_user_refresh_lock
from app.core.jandi_briefing import drop_pending_for_user
from app.core.personal_briefing_store import delete_for_user
from app.db.models import User

logger = structlog.get_logger(__name__)

auth_router = APIRouter(prefix="/auth/google", tags=["auth"])

_auth_manager = None


def _get_auth_manager() -> GoogleAuthManager:
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = GoogleAuthManager()
    return _auth_manager


def _get_redirect_uri(request: Request) -> str:
    """Build redirect URI dynamically from the request's Host header.

    Google rejects raw IP addresses as redirect URIs (except localhost).
    For LAN IPs, we append .nip.io (wildcard DNS that resolves IP.nip.io → IP).
    The resulting URI must be registered in Google Cloud Console.
    """
    host = request.headers.get("host", "localhost:3000")
    scheme = "https" if request.url.scheme == "https" else "http"
    # Convert raw IP to nip.io domain (Google requires a real domain)
    # e.g. 172.16.1.250:3000 → 172.16.1.250.nip.io:3000
    m = re.match(r'^(\d+\.\d+\.\d+\.\d+)(:\d+)?$', host)
    if m and not host.startswith("127."):
        ip, port = m.group(1), m.group(2) or ""
        host = f"{ip}.nip.io{port}"
    return f"{scheme}://{host}/auth/google/callback"


#: 구글에 **등록돼 있고** 구글이 정책상 받아 주는 콜백 호스트만 통과시킨다.
#: 원시 IP 를 바꾼 `<ip>.nip.io` 형태와 localhost 가 그것이다.
_USABLE_HOST_RE = re.compile(r'^(?:\d+\.\d+\.\d+\.\d+\.nip\.io|localhost|127\.0\.0\.1)(?::\d+)?$')


def _redirect_uri_is_usable(redirect_uri: str) -> bool:
    """이 콜백 주소로 구글에 보내도 되는지 본다.

    ⛔ **Host 헤더를 그대로 믿으면 안 된다** (2026-09-02 실사용자 차단). 사내 DNS
       이름으로 접속한 사람에게는 `http://ai.cravercorp.internal/auth/google/callback`
       이 만들어져 나갔다. 그 주소는 ① 콘솔에 등록돼 있지 않고 ② `.internal` 은
       구글이 금지하는 사설 도메인이다. 구글이 준 사유가 정확히 그것이었다:
       `invalid_request … redirect_uri=http://ai.cravercorp.internal/…`

    ⚠️ 그런데 **에러가 우리 쪽으로 돌아오지 않는다.** 구글 화면에서 끝나므로
       서버 로그에는 콜백이 0건이고, 사용자는 "액세스 차단됨" 만 본다 — 원인을
       추측하게 되는 전형적인 조용한 실패다. 그래서 **보내기 전에** 막고 말한다.

    ⚠️ `_get_redirect_uri` 는 원시 IP 만 `<ip>.nip.io` 로 바꾼다. 호스트명은 그대로
       통과하므로, 새 DNS 이름이 생길 때마다 같은 일이 반복된다.
    """
    from urllib.parse import urlparse

    host = (urlparse(redirect_uri or "").netloc or "").lower()
    if _USABLE_HOST_RE.match(host):
        return True
    registered = urlparse(get_settings().google_oauth_redirect_uri or "").netloc.lower()
    return bool(registered) and host == registered


def _canonical_browsing_url() -> str:
    """구글 연결이 되는 주소 — 사람에게 알려 줄 값이다."""
    from urllib.parse import urlparse

    parsed = urlparse(get_settings().google_oauth_redirect_uri or "")
    host = _browsing_host(parsed.netloc) or parsed.netloc
    return f"{parsed.scheme or 'http'}://{host}" if host else ""


def _notice_page(title: str, body: str) -> str:
    """콜백에서 사람이 읽을 안내를 보여준다 — 원시 401 JSON 은 무엇을 할지 알 수 없다."""
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
  body {{ font-family: 'Montserrat', -apple-system, sans-serif; display: flex;
         justify-content: center; align-items: center; min-height: 100vh; margin: 0;
         background: #0a0a0a; color: #e8e8e8; }}
  .card {{ background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08);
          border-radius: 24px; padding: 48px; text-align: center; max-width: 420px; }}
  h1 {{ font-size: 18px; font-weight: 700; margin-bottom: 12px; }}
  p {{ color: rgba(255,255,255,0.55); font-size: 14px; line-height: 1.6; }}
</style></head>
<body><div class="card"><h1>{html.escape(title)}</h1>
<p>{html.escape(body)}</p></div></body></html>"""


_NIP_HOST_RE = re.compile(r'^(\d+\.\d+\.\d+\.\d+)\.nip\.io(:\d+)?$')


def _browsing_host(host: str) -> str:
    """`_get_redirect_uri` 가 바꾼 호스트를 **사용자가 보던 호스트로 되돌린다.**

    ⛔ 세션 쿠키는 호스트 전용이다. 사용자는 `10.1.100.5` 를 보고 있는데 구글은
       `10.1.100.5.nip.io` 로 돌려보내므로 **콜백에는 쿠키가 실리지 않는다** —
       콜백에 로그인 요구가 붙은 날(2026-08-25) 새로 연결하려는 사람이 전부 401 을
       받았다. 이미 연결된 사람은 토큰 갱신만 하므로 아무도 눈치채지 못한다.

    되돌릴 곳이 없으면(사용자가 이미 그 호스트였거나 진짜 도메인이면) 빈 문자열.
    """
    m = _NIP_HOST_RE.match(host or "")
    return f"{m.group(1)}{m.group(2) or ''}" if m else ""


async def _handle_password_reset_callback(request: Request, code: str, state: str):
    """구글이 보증한 신원으로 **본인이** 비밀번호를 되찾게 한다.

    ⚠️ 이 분기는 쿠키를 보지 않는다 — state 가 서버에 있어서 `session_hop` 왕복이
       필요 없다. 쿠키 없는 호스트로 돌아오던 그 사고를 구조적으로 비켜간다.
    """
    try:
        await asyncio.to_thread(password_reset_google.consume_state, state)
    except Exception:
        logger.warning("pwreset_oauth_state_rejected")
        return HTMLResponse(status_code=400, content=_notice_page(
            "확인 링크가 만료되었습니다",
            "로그인 화면에서 '비밀번호를 잊으셨나요' 를 다시 눌러 주세요."))

    try:
        email = await asyncio.to_thread(
            password_reset_google.verified_google_email, code, _get_redirect_uri(request))
        target = await asyncio.to_thread(
            password_reset_google.find_user_by_google_email, email)
    except password_reset_google.ResetUnavailable as unavailable:
        return HTMLResponse(status_code=400,
                            content=_notice_page(unavailable.title, unavailable.body))
    except Exception as exc:
        logger.error("pwreset_oauth_failed", error_type=type(exc).__name__)
        return HTMLResponse(status_code=500, content=_notice_page(
            "구글 확인에 실패했습니다",
            "잠시 후 다시 시도하거나, 로그인 화면에서 관리자에게 요청을 남겨 주세요."))

    if not target:
        # ⚠️ 여기서 "없다" 고 말해도 유출이 아니다 — 그 구글 계정으로 방금 인증한
        #    사람에게 **자기 계정** 얘기를 하는 것이다. 말해 주지 않으면 무엇이
        #    잘못됐는지 알 수 없어 같은 시도를 반복한다.
        logger.warning("pwreset_oauth_no_account")
        return HTMLResponse(status_code=404, content=_notice_page(
            "이 구글 계정으로 가입된 셀라 계정이 없습니다",
            "회사 구글 계정으로 다시 시도하시거나, 로그인 화면에서 관리자에게 "
            "요청을 남겨 주세요."))

    grant = await asyncio.to_thread(password_reset_google.issue_grant, target["id"])
    logger.warning("pwreset_oauth_verified", user_id=target["id"])

    # 사용자가 보던 호스트로 되돌린다 — 구글은 `…nip.io` 로 돌려보냈을 수 있다.
    host = _browsing_host(request.headers.get("host", "")) or request.headers.get("host", "")
    scheme = "https" if request.url.scheme == "https" else "http"
    return RedirectResponse(
        url=f"{scheme}://{host}/api/auth/password-reset/google/land?rc={grant}")


@auth_router.get("/login")
async def google_login(
    request: Request,
    user: User = Depends(get_current_user),
):
    """Redirect the authenticated user to Google OAuth consent."""
    # ⛔ 쓸 수 없는 콜백 주소로 구글에 보내면 사용자는 "액세스 차단됨" 만 보고,
    #    에러는 구글 화면에서 끝나 **우리 로그에는 아무것도 남지 않는다.**
    #    보내기 전에 막고, 무엇을 하면 되는지 말해 준다.
    redirect_uri = _get_redirect_uri(request)
    if not _redirect_uri_is_usable(redirect_uri):
        logger.warning("google_login_unusable_redirect",
                       host=request.headers.get("host", ""), redirect_uri=redirect_uri)
        canonical = _canonical_browsing_url()
        return HTMLResponse(status_code=400, content=_notice_page(
            "이 주소에서는 구글 연결을 할 수 없습니다",
            f"지금 접속하신 주소는 구글에 등록할 수 없는 사내 전용 주소입니다. "
            f"{canonical} 로 접속해 다시 로그인한 뒤 연결해 주세요."
            if canonical else
            "지금 접속하신 주소는 구글에 등록할 수 없는 사내 전용 주소입니다. "
            "관리자에게 문의해 주세요."))

    state = await asyncio.to_thread(issue_state, user.id, user.email)
    auth_url = _get_auth_manager().get_auth_url(
        user.email, state=state, redirect_uri=redirect_uri
    )
    return RedirectResponse(url=auth_url)


@auth_router.get("/callback")
async def google_callback(
    request: Request,
    code: str = Query(..., description="Authorization code from Google"),
    state: str = Query("", description="Signed OAuth state"),
    session_hop: str = Query("", description="Set once after bouncing to the cookie host"),
    user: User | None = Depends(get_optional_user),
):
    """Handle an authenticated user's OAuth callback and save their token."""
    # ── 비밀번호 재설정은 **로그인 없이** 같은 문으로 돌아온다 ──
    # 구글 콘솔에 등록된 리다이렉트 URI 가 이것 하나뿐이라 경로를 새로 팔 수 없다.
    # ⛔ 서명이 검증된 state 만 이 분기로 들어온다 — 로그인 요구를 건너뛰는 쪽이다.
    if password_reset_google.is_reset_state(state):
        return await _handle_password_reset_callback(request, code, state)

    if user is None:
        # 쿠키가 없다 — 구글이 nip.io 호스트로 돌려보냈기 때문일 수 있다.
        # **딱 한 번** 쿠키가 있는 호스트로 되돌려 보낸다 (최상위 GET 이동이라
        # SameSite=Lax 쿠키가 실린다). ⛔ 여기서 코드를 교환하지 않는다 —
        # 인증 없이 교환하면 남의 세션에 계정을 붙일 수 있다.
        target = "" if session_hop else _browsing_host(request.headers.get("host", ""))
        if target:
            scheme = "https" if request.url.scheme == "https" else "http"
            query = f"{request.url.query}&session_hop=1" if request.url.query else "session_hop=1"
            logger.info("oauth_callback_session_hop", to_host=target)
            return RedirectResponse(url=f"{scheme}://{target}/auth/google/callback?{query}")


        logger.warning("oauth_callback_no_session", hopped=bool(session_hop))
        return HTMLResponse(status_code=401, content=_notice_page(
            "로그인 세션을 찾지 못했습니다",
            "이 창을 닫고 채팅 화면에서 다시 로그인한 뒤 Google 연결을 눌러 주세요. "
            "시크릿 창이나 다른 브라우저에서 열렸다면 같은 창에서 다시 시도해 주세요."))

    try:
        payload = await asyncio.to_thread(consume_state, state, user.id)
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    user_email = str(payload["email"])
    if user_email.strip().casefold() != user.email.strip().casefold():
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    try:
        redirect_uri = _get_redirect_uri(request)
        async with get_user_refresh_lock(user.id):
            await asyncio.to_thread(
                _get_auth_manager().exchange_code,
                code,
                user_email,
                redirect_uri,
            )
            try:
                await asyncio.to_thread(delete_for_user, user.id)
            except Exception as cleanup_error:
                logger.warning(
                    "oauth_snapshot_cleanup_failed",
                    user_id=user.id,
                    error_type=type(cleanup_error).__name__,
                )
        logger.info("oauth_callback_success", user_id=user.id)
    except Exception as e:
        logger.error("oauth_callback_failed", user_id=user.id, error_type=type(e).__name__)
        raise HTTPException(status_code=500, detail="Token exchange failed")

    # Return success page that auto-closes
    page_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>인증 완료</title>
        <style>
            body {{
                font-family: 'Montserrat', -apple-system, sans-serif;
                display: flex; justify-content: center; align-items: center;
                min-height: 100vh; margin: 0; background: #0a0a0a; color: #e8e8e8;
            }}
            .card {{
                background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08);
                border-radius: 24px; padding: 48px; text-align: center; max-width: 400px;
                backdrop-filter: blur(20px);
            }}
            .check {{
                width: 56px; height: 56px; border-radius: 50%;
                background: #34A853; color: #fff; display: flex;
                align-items: center; justify-content: center;
                font-size: 28px; margin: 0 auto 20px;
            }}
            h1 {{ font-size: 18px; font-weight: 700; margin-bottom: 8px; }}
            p {{ color: rgba(255,255,255,0.5); font-size: 14px; line-height: 1.5; }}
            .email {{ color: #e89200; font-weight: 600; }}
            .countdown {{ color: rgba(255,255,255,0.3); font-size: 12px; margin-top: 16px; }}
        </style>
    </head>
    <body>
        <div class="card">
            <div class="check">&#10003;</div>
            <h1>Google 인증 완료</h1>
            <p><span class="email">{html.escape(user.email)}</span></p>
            <p>Gmail, Drive, Calendar 접근이 연결되었습니다.</p>
            <p class="countdown" id="cd">3초 후 자동으로 닫힙니다...</p>
        </div>
        <script>
            var s = 3;
            var t = setInterval(function() {{
                s--;
                if (s <= 0) {{ clearInterval(t); window.close(); }}
                else {{ document.getElementById('cd').textContent = s + '초 후 자동으로 닫힙니다...'; }}
            }}, 1000);
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=page_html)


@auth_router.get("/status")
async def google_auth_status(user: User = Depends(get_current_user)):
    """Check if user has valid Google OAuth credentials.

    Returns authenticated status and the connected Google account email.
    """
    mgr = _get_auth_manager()
    # Fast check: file exists? (no token refresh, instant)
    authenticated = mgr.has_credentials(user.email)
    google_email = mgr.get_stored_google_email(user.email) if authenticated else ""

    return {
        "authenticated": authenticated,
        "google_email": google_email,
    }


@auth_router.post("/revoke")
async def google_revoke(user: User = Depends(get_current_user)):
    """Revoke the authenticated user's stored Google OAuth credentials."""
    async with get_user_refresh_lock(user.id):
        deleted = await asyncio.to_thread(_get_auth_manager().revoke_credentials, user.email)
        await asyncio.to_thread(delete_for_user, user.id)
        # ⚠️ 아직 안 보낸 잔디 브리핑에는 방금 끊은 계정의 메일 요약이 들어 있다.
        #    스냅샷만 지우면 그 본문이 나중에 잔디로 나간다 — 함께 버린다.
        try:
            await asyncio.to_thread(drop_pending_for_user, user.id)
        except Exception as cleanup_error:
            logger.warning(
                "jandi_outbox_cleanup_failed",
                user_id=user.id,
                error_type=type(cleanup_error).__name__,
            )
    return {
        "revoked": deleted,
        "message": "토큰이 삭제되었습니다." if deleted else "저장된 토큰이 없습니다.",
    }
