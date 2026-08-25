"""OAuth2 authentication endpoints for Google Workspace."""

import asyncio
import html

import jwt
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api.auth_middleware import get_current_user
from app.core.google_auth import GoogleAuthManager
from app.core.google_oauth_state import consume_state, issue_state
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
    import re
    host = request.headers.get("host", "localhost:3000")
    scheme = "https" if request.url.scheme == "https" else "http"
    # Convert raw IP to nip.io domain (Google requires a real domain)
    # e.g. 172.16.1.250:3000 → 172.16.1.250.nip.io:3000
    m = re.match(r'^(\d+\.\d+\.\d+\.\d+)(:\d+)?$', host)
    if m and not host.startswith("127."):
        ip, port = m.group(1), m.group(2) or ""
        host = f"{ip}.nip.io{port}"
    return f"{scheme}://{host}/auth/google/callback"


@auth_router.get("/login")
async def google_login(
    request: Request,
    user: User = Depends(get_current_user),
):
    """Redirect the authenticated user to Google OAuth consent."""
    state = await asyncio.to_thread(issue_state, user.id, user.email)
    auth_url = _get_auth_manager().get_auth_url(
        user.email, state=state, redirect_uri=_get_redirect_uri(request)
    )
    return RedirectResponse(url=auth_url)


@auth_router.get("/callback")
async def google_callback(
    request: Request,
    code: str = Query(..., description="Authorization code from Google"),
    state: str = Query("", description="Signed OAuth state"),
    user: User = Depends(get_current_user),
):
    """Handle an authenticated user's OAuth callback and save their token."""
    try:
        payload = await asyncio.to_thread(consume_state, state, user.id)
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    user_email = str(payload["email"])

    try:
        redirect_uri = _get_redirect_uri(request)
        _get_auth_manager().exchange_code(code, user_email, redirect_uri=redirect_uri)
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
    deleted = _get_auth_manager().revoke_credentials(user.email)
    return {
        "revoked": deleted,
        "message": "토큰이 삭제되었습니다." if deleted else "저장된 토큰이 없습니다.",
    }
