"""Middleware for CORS, authentication, and request logging."""

import time
import uuid

import jwt as pyjwt
import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings
from app.api.auth_middleware import get_current_user
from app.core.session_auth import decode_session, LOGIN_REQUIRED_HEADERS

logger = structlog.get_logger(__name__)

_PUBLIC_GET = {
    "/login", "/api/auth/methods", "/auth/entra/status", "/auth/entra/login",
    "/auth/entra/callback", "/users/auth/openid_connect/callback",
    "/auth/google/callback", "/api/auth/google/callback", "/settings",
    "/health", "/health/ready", "/favicon.ico",
    "/api/auth/password-reset/google/start", "/api/auth/password-reset/google/land",
}
_PUBLIC_POST = {
    "/api/auth/logout", "/api/auth/signup", "/api/auth/signin",
    "/api/auth/password-reset-request", "/api/auth/password-reset/google/complete",
}
_RELAY_ROUTES = {
    ("GET", "/api/internal/briefing-outbox"),
    ("POST", "/api/internal/briefing-outbox/ack"),
    ("POST", "/api/internal/fx"),
}
_PUBLIC_ASSET_SUFFIXES = {
    "js", "css", "png", "jpg", "jpeg", "gif", "svg", "ico", "webp", "woff", "woff2", "ttf",
}


def _public_request(request: Request) -> bool:
    path = request.url.path.rstrip("/") or "/"
    method = request.method
    if method == "OPTIONS" or (method, path) in _RELAY_ROUTES:
        return True  # Relay handlers retain their own token and loopback checks.
    if method == "POST":
        return path in _PUBLIC_POST
    if method not in {"GET", "HEAD"}:
        return False
    if path in _PUBLIC_GET:
        return True
    if path.startswith(("/static/", "/frontend/")):
        return path.rsplit(".", 1)[-1].lower() in _PUBLIC_ASSET_SUFFIXES
    return bool(getattr(get_settings(), "password_login_enabled", False)) and path in {
        "/api/auth/departments", "/api/auth/users-by-dept", "/api/auth/search-name",
    }


def _page_request(request: Request) -> bool:
    path = request.url.path.rstrip("/") or "/"
    return request.method in {"GET", "HEAD"} and (
        path in {"/", "/coa-finder", "/face-search", "/harness", "/dashboard", "/docs", "/redoc",
                 "/auth/google/login"}
        or path.lower().endswith(".html")
    )


def setup_middleware(app: FastAPI) -> None:
    """Configure all middleware for the FastAPI app."""
    settings = get_settings()
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestLoggingMiddleware)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Require a validated login on application routes and log request details."""

    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())[:8]
        start_time = time.time()

        user_email = ""
        auth_response = None
        token = request.cookies.get("token")
        if _public_request(request):
            try:
                payload = decode_session(token, get_settings(), request=request) if token else {}
                user_email = payload.get("email", "")
                request.state.user_id = payload.get("user_id", "")
            except pyjwt.InvalidTokenError:
                pass
        else:
            try:
                await get_current_user(request)
                user_email = request.state.user_email
            except HTTPException as exc:
                if exc.status_code == 401 and _page_request(request):
                    auth_response = RedirectResponse("/login", status_code=302, headers=LOGIN_REQUIRED_HEADERS)
                else:
                    auth_response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                                                 headers=exc.headers)
                if exc.status_code == 401:
                    auth_response.delete_cookie("token", path="/")

        request.state.user_email = user_email

        # Log request (skip noisy paths)
        if request.url.path not in ("/health", "/admin/maintenance/status", "/safety/status"):
            logger.info(
                "request_started",
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                client=request.client.host if request.client else "unknown",
                user_email=user_email or None,
            )

        try:
            response = auth_response if auth_response is not None else await call_next(request)
            elapsed_ms = int((time.time() - start_time) * 1000)

            if request.url.path not in ("/health", "/admin/maintenance/status", "/safety/status"):
                logger.info(
                    "request_completed",
                    request_id=request_id,
                    status_code=response.status_code,
                    latency_ms=elapsed_ms,
                )

            response.headers["X-Request-ID"] = request_id
            response.headers["X-Latency-Ms"] = str(elapsed_ms)
            return response

        except Exception as e:
            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.error(
                "request_failed",
                request_id=request_id,
                error=str(e),
                latency_ms=elapsed_ms,
            )
            raise
