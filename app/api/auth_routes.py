"""OAuth2 authentication endpoints for Google Workspace."""

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.google_auth import GoogleAuthManager

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
    user_email: str = Query(..., description="사용자 이메일"),
):
    """Redirect user to Google OAuth consent screen.

    Args:
        user_email: User's email address.
    """
    if not user_email:
        raise HTTPException(status_code=400, detail="user_email 파라미터가 필요합니다.")

    redirect_uri = _get_redirect_uri(request)
    auth_url = _get_auth_manager().get_auth_url(user_email, redirect_uri=redirect_uri)
    return RedirectResponse(url=auth_url)


@auth_router.get("/callback")
async def google_callback(
    request: Request,
    code: str = Query(..., description="Authorization code from Google"),
    state: str = Query("", description="User email passed as state"),
):
    """Handle Google OAuth callback, exchange code for tokens.

    Args:
        code: Authorization code from Google.
        state: User email passed via state parameter.
    """
    user_email = state
    if not user_email:
        raise HTTPException(status_code=400, detail="state 파라미터(user_email)가 없습니다.")

    try:
        redirect_uri = _get_redirect_uri(request)
        _get_auth_manager().exchange_code(code, user_email, redirect_uri=redirect_uri)
        logger.info("oauth_callback_success", user_email=user_email)
    except Exception as e:
        logger.error("oauth_callback_failed", user_email=user_email, error=str(e))
        raise HTTPException(status_code=500, detail=f"토큰 교환 실패: {str(e)}")

    # Return success page that auto-closes
    html = f"""
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
            <p><span class="email">{user_email}</span></p>
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
    return HTMLResponse(content=html)


@auth_router.get("/status")
async def google_auth_status(user_email: str = Query(..., description="사용자 이메일")):
    """Check if user has valid Google OAuth credentials.

    Returns authenticated status and the connected Google account email.
    """
    if not user_email:
        raise HTTPException(status_code=400, detail="user_email 파라미터가 필요합니다.")

    mgr = _get_auth_manager()
    # Fast check: file exists? (no token refresh, instant)
    authenticated = mgr.has_credentials(user_email)
    google_email = mgr.get_stored_google_email(user_email) if authenticated else ""

    return {
        "user_email": user_email,
        "authenticated": authenticated,
        "google_email": google_email,
    }


@auth_router.post("/revoke")
async def google_revoke(user_email: str = Query(..., description="사용자 이메일")):
    """Revoke (delete) stored Google OAuth credentials for a user.

    Args:
        user_email: User's email address.
    """
    if not user_email:
        raise HTTPException(status_code=400, detail="user_email 파라미터가 필요합니다.")

    deleted = _get_auth_manager().revoke_credentials(user_email)
    return {
        "user_email": user_email,
        "revoked": deleted,
        "message": "토큰이 삭제되었습니다." if deleted else "저장된 토큰이 없습니다.",
    }
