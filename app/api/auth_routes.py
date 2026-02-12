"""OAuth2 authentication endpoints for Google Workspace."""

import structlog
from fastapi import APIRouter, HTTPException, Query
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


@auth_router.get("/login")
async def google_login(user_email: str = Query(..., description="사용자 이메일")):
    """Redirect user to Google OAuth consent screen.

    Args:
        user_email: User's email address.
    """
    if not user_email:
        raise HTTPException(status_code=400, detail="user_email 파라미터가 필요합니다.")

    auth_url = _get_auth_manager().get_auth_url(user_email)
    return RedirectResponse(url=auth_url)


@auth_router.get("/callback")
async def google_callback(
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
        _get_auth_manager().exchange_code(code, user_email)
        logger.info("oauth_callback_success", user_email=user_email)
    except Exception as e:
        logger.error("oauth_callback_failed", user_email=user_email, error=str(e))
        raise HTTPException(status_code=500, detail=f"토큰 교환 실패: {str(e)}")

    # Return a simple HTML success page
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>인증 완료</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                display: flex; justify-content: center; align-items: center;
                min-height: 100vh; margin: 0; background: #f0f2f5;
            }}
            .card {{
                background: white; border-radius: 12px; padding: 40px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.1); text-align: center;
                max-width: 400px;
            }}
            .check {{ font-size: 48px; margin-bottom: 16px; }}
            h1 {{ font-size: 20px; color: #1a1a1a; margin-bottom: 8px; }}
            p {{ color: #666; font-size: 14px; line-height: 1.5; }}
        </style>
    </head>
    <body>
        <div class="card">
            <div class="check">&#10003;</div>
            <h1>Google 인증 완료</h1>
            <p><strong>{user_email}</strong> 계정이 연결되었습니다.</p>
            <p>이 창을 닫고 Open WebUI에서 다시 질문해주세요.</p>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@auth_router.get("/status")
async def google_auth_status(user_email: str = Query(..., description="사용자 이메일")):
    """Check if user has valid Google OAuth credentials.

    Args:
        user_email: User's email address.
    """
    if not user_email:
        raise HTTPException(status_code=400, detail="user_email 파라미터가 필요합니다.")

    creds = _get_auth_manager().get_credentials(user_email)
    authenticated = creds is not None

    return {
        "user_email": user_email,
        "authenticated": authenticated,
        "scopes": list(creds.scopes) if authenticated and creds.scopes else [],
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
