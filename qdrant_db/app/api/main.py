"""
FastAPI 앱 진입점
"""

from fastapi import FastAPI
from app.api.routes import search, ask, admin, webhook_notion

app = FastAPI(
    title="Notion RAG API",
    description="Notion Hub 기반 RAG 시스템",
    version="1.0.0",
)

app.include_router(search.router)
app.include_router(ask.router)
app.include_router(admin.router)
app.include_router(webhook_notion.router)


@app.get("/health")
def health():
    return {"status": "ok"}
