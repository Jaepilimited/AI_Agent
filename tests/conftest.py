"""Shared test-process configuration."""

import os


# Application startup deliberately refuses missing or weak JWT keys. Tests use
# an explicit non-production key so importing app.main exercises the same guard.
os.environ.setdefault("JWT_SECRET_KEY", "test-only-jwt-secret-" + ("x" * 42))
