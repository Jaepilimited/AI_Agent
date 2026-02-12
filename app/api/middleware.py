"""Middleware for CORS, authentication, and request logging."""

import time
import uuid

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

logger = structlog.get_logger(__name__)


def setup_middleware(app: FastAPI) -> None:
    """Configure all middleware for the FastAPI app.

    Args:
        app: The FastAPI application instance.
    """
    # CORS - allow Open WebUI and local development
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Open WebUI needs this
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Request logging
    app.add_middleware(RequestLoggingMiddleware)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware that logs request/response details."""

    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())[:8]
        start_time = time.time()

        # Extract user email from Open WebUI header
        user_email = request.headers.get("X-OpenWebUI-User-Email", "")
        request.state.user_email = user_email

        # Debug: capture user info from Open WebUI and extract email
        if request.url.path == "/v1/chat/completions" and request.method == "POST":
            import json as _json
            body_bytes = await request.body()
            try:
                body = _json.loads(body_bytes)
                user_field = body.get("user")
                # Open WebUI sends user as JSON string: {"id":"...","email":"...","name":"...","role":"..."}
                if user_field and not user_email:
                    try:
                        if isinstance(user_field, str):
                            user_data = _json.loads(user_field)
                        else:
                            user_data = user_field
                        if isinstance(user_data, dict) and user_data.get("email"):
                            user_email = user_data["email"]
                            request.state.user_email = user_email
                    except (ValueError, TypeError):
                        # user_field might be a plain string (email or name)
                        if "@" in str(user_field):
                            user_email = str(user_field)
                            request.state.user_email = user_email
                logger.warning("debug_user_info",
                    user_field=user_field,
                    resolved_email=user_email,
                )
                # Write to file for easy debugging
                with open("data/debug_user.log", "a", encoding="utf-8") as _f:
                    _f.write(f"user_field={user_field!r} | resolved={user_email}\n")
            except Exception:
                pass

        # Log request
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

            # Log response
            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.info(
                "request_completed",
                request_id=request_id,
                status_code=response.status_code,
                latency_ms=elapsed_ms,
            )

            # Add custom headers
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
