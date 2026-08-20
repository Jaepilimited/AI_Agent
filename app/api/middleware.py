"""Middleware for CORS, authentication, and request logging."""

import time
import uuid

import jwt as pyjwt
import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings, validate_jwt_secret

logger = structlog.get_logger(__name__)


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
    """Middleware that logs request/response details and extracts user from JWT cookie."""

    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())[:8]
        start_time = time.time()

        # Extract user email from JWT cookie
        user_email = ""
        token = request.cookies.get("token")
        if token:
            try:
                settings = get_settings()
                payload = pyjwt.decode(
                    token,
                    validate_jwt_secret(settings.jwt_secret_key),
                    algorithms=["HS256"],
                )
                user_email = payload.get("email", "")
                request.state.user_id = payload.get("user_id", "")
            except Exception:
                pass

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
            response = await call_next(request)
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
