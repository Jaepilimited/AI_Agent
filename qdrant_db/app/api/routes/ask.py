"""
POST /ask
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.rag.retrieve import retrieve
from app.rag.answer import generate_answer

router = APIRouter()


class AskRequest(BaseModel):
    query: str
    top_k: int = 8
    team: str | None = None


class AskResponse(BaseModel):
    answer: str
    sources: list[dict]


@router.post("/ask", response_model=AskResponse)
def ask(req: AskRequest):
    results = retrieve(query=req.query, top_k=req.top_k, team_filter=req.team)
    result = generate_answer(query=req.query, results=results)
    return AskResponse(answer=result["answer"], sources=result["sources"])
