"""
POST /search
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.rag.retrieve import retrieve

router = APIRouter()


class SearchRequest(BaseModel):
    query: str
    top_k: int = 8
    team: str | None = None


class SearchResponse(BaseModel):
    results: list[dict]


@router.post("/search", response_model=SearchResponse)
def search(req: SearchRequest):
    results = retrieve(query=req.query, top_k=req.top_k, team_filter=req.team)
    return SearchResponse(
        results=[
            {
                "score": round(r.score, 4),
                "page_title": r.page_title,
                "page_url": r.page_url,
                "team": r.team,
                "section_path": r.section_path,
                "breadcrumb": r.breadcrumb,
                "text_preview": r.text[:300],
            }
            for r in results
        ]
    )
