"""Qdrant Agent — 벡터 검색 기반 Notion 사내 문서 검색.

Gemini embedding-001 (1536차원) → Qdrant Cloud 벡터 검색 → Gemini Flash 답변 생성.
팀별 필터링 지원: @@prefix에서 team 매핑 → payload.team 필터.
"""

import asyncio
from typing import Optional

import httpx
import structlog

from app.config import get_settings
from app.core.llm import get_flash_client
from app.core.prompt_fragments import LANGUAGE_DETECTION_RULE

logger = structlog.get_logger(__name__)

# ── Qdrant config ──
QDRANT_URL = "https://bf41bcbe-af68-416f-9d26-1b3d64f7bed0.us-east-1-1.aws.cloud.qdrant.io:6333"
QDRANT_API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2Nlc3MiOiJtIiwic3ViamVjdCI6ImFwaS1rZXk6OTFkOGVkZWYtNTFkNi00ODNhLTg0MDItZTdjNjI0ZjA2NThmIn0.K0zdMdpnbIMl_yfXV8EJfcClpPnkoPa_SS_XbDI1kv4"
COLLECTION = "notion_hub_gemini"
EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIM = 1536
TOP_K = 8

# ── Team 매핑 (@@prefix → Qdrant payload.team) ──
TEAM_MAP = {
    "west": "[GM]WEST",
    "gm_west": "[GM]WEST",
    "서부": "[GM]WEST",
    "east": "[GM]EAST",
    "gm_east": "[GM]EAST",
    "동부": "[GM]EAST",
    "bcm": "BCM",
    "jbt": "JBT",
    "kbt": "KBT",
    "db": "DB",
    "데이터분석": "DB",
    "it": "IT",
    "피플": "PEOPLE",
    "people": "PEOPLE",
    "b2b": "B2B2",
    "b2b2": "B2B2",
    "해외영업": "B2B2",
    "b2b1": "B2B1",
    "국내영업": "B2B1",
    "notion_cs": "CS",
    "cs": "CS",
    "craver": "Craver",
    "크레이버": "Craver",
    "log": "LOG",
    "물류": "LOG",
    "fi": "FI",
    "재무": "FI",
    "op": "OP",
    "운영": "OP",
}


def _get_qdrant_headers() -> dict:
    return {"api-key": QDRANT_API_KEY, "Content-Type": "application/json"}


async def _embed_query(query: str) -> list[float]:
    """Gemini embedding-001로 쿼리 임베딩 (1536차원)."""
    from google import genai

    settings = get_settings()
    client = genai.Client(api_key=settings.gemini_api_key)

    result = await asyncio.to_thread(
        client.models.embed_content,
        model=EMBEDDING_MODEL,
        contents=[query],
        config={"output_dimensionality": EMBEDDING_DIM},
    )
    return result.embeddings[0].values


async def _search_qdrant(
    vector: list[float],
    team_filter: Optional[str] = None,
    top_k: int = TOP_K,
) -> list[dict]:
    """Qdrant 벡터 유사도 검색."""
    body = {
        "vector": vector,
        "limit": top_k,
        "with_payload": True,
        "score_threshold": 0.3,
    }
    if team_filter:
        body["filter"] = {
            "must": [{"key": "team", "match": {"value": team_filter}}]
        }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{QDRANT_URL}/collections/{COLLECTION}/points/search",
            headers=_get_qdrant_headers(),
            json=body,
        )
        resp.raise_for_status()
        return resp.json().get("result", [])


def _format_search_results(results: list[dict]) -> str:
    """검색 결과를 LLM 프롬프트용 컨텍스트로 포맷."""
    if not results:
        return "검색 결과 없음"

    chunks = []
    for i, r in enumerate(results, 1):
        p = r.get("payload", {})
        score = r.get("score", 0)
        team = p.get("team", "?")
        title = p.get("page_title", "?")
        breadcrumb = p.get("breadcrumb", "")
        section = p.get("section_path", "")
        text = p.get("text", "")[:2000]
        url = p.get("page_url", "")

        header = f"[{i}] ({score:.2f}) {team} > {title}"
        if section:
            header += f" > {section}"

        chunks.append(f"{header}\n{text}\n출처: {url}")

    return "\n\n---\n\n".join(chunks)


async def run(
    query: str,
    team_key: Optional[str] = None,
    model_type: str = "gemini",
) -> str:
    """Qdrant 벡터 검색 + Gemini Flash 답변 생성.

    Args:
        query: 사용자 질문
        team_key: @@prefix에서 추출한 팀 키 (lowercase). None이면 전체 검색.
        model_type: LLM 모델 타입 (현재 gemini만 지원)

    Returns:
        자연어 답변
    """
    # 1. Team 필터 매핑
    team_filter = TEAM_MAP.get(team_key.lower(), None) if team_key else None
    logger.info("qdrant_search_start", query=query[:80], team_key=team_key, team_filter=team_filter)

    # 2. 쿼리 임베딩
    try:
        vector = await _embed_query(query)
    except Exception as e:
        logger.error("qdrant_embedding_failed", error=str(e))
        return f"임베딩 생성 실패: {e}"

    # 3. Qdrant 검색
    try:
        results = await _search_qdrant(vector, team_filter=team_filter, top_k=TOP_K)
    except Exception as e:
        logger.error("qdrant_search_failed", error=str(e))
        return f"벡터 검색 실패: {e}"

    logger.info("qdrant_search_done", result_count=len(results), top_score=results[0]["score"] if results else 0)

    if not results:
        team_label = team_filter or "전체"
        return f"**{team_label}** 팀 자료에서 '{query}'와 관련된 문서를 찾을 수 없습니다.\n\n다른 키워드로 검색해보세요."

    # 4. 컨텍스트 생성
    context = _format_search_results(results)

    # 5. LLM 답변 생성
    llm = get_flash_client()
    team_label = team_filter or "전체"

    prompt = f"""{LANGUAGE_DETECTION_RULE}

당신은 SKIN1004의 사내 문서 검색 도우미입니다.
아래는 사용자의 질문과 Qdrant 벡터 검색으로 찾은 관련 문서입니다.

## 사용자 질문
{query}

## 검색된 문서 ({len(results)}건, 팀: {team_label})
{context}

## 답변 규칙
- **검색된 문서 내용을 직접 요약하여 답변하세요** (링크만 달지 마세요!)
- 문서에 내용이 있으면 핵심을 구조적으로 정리 (제목, 요약, 상세 내용)
- 출처 링크를 함께 제공: [문서명](URL)
- 팀/카테고리별로 그룹화
- 매칭 결과가 부족하면 "관련 자료가 제한적입니다" 안내
- 답변 마지막에 출처 표시:
  ---
  *Qdrant 벡터 검색 · {team_label} 팀 자료*

## 후속 질문
> 💡 **이런 것도 물어보세요**
> - [관련 팀의 다른 자료]
> - [같은 주제의 다른 문서]
"""

    try:
        answer = await asyncio.to_thread(llm.generate, prompt, None, 0.3, 2048)
        return answer
    except Exception as e:
        logger.error("qdrant_answer_failed", error=str(e))
        return f"답변 생성 중 오류: {e}"
