"""Notion 사내 문서 검색 — Qdrant Cloud 벡터 검색.

로컬 JSON(notion_vectors_gemini.json)을 소스 오브 트루스로 유지하고,
Qdrant Cloud를 실제 벡터 검색 백엔드로 사용한다.

- 검색: Gemini embedding → Qdrant Cloud query → Gemini Flash 답변
- 업데이트: 파이프라인 실행 후 reload_vectors() → Cloud 전체 재업로드
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Optional

import structlog

from app.config import get_settings
from app.core.llm import get_flash_client
from app.core.prompt_fragments import LANGUAGE_DETECTION_RULE

logger = structlog.get_logger(__name__)

EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIM   = 1536
TOP_K           = 8
SCORE_THRESHOLD = 0.3
COLLECTION      = "Craver"

_LOCAL_JSON = Path(__file__).resolve().parent.parent.parent / "data" / "notion_vectors_gemini.json"

TEAM_MAP = {
    "west": "[GM]WEST", "gm_west": "[GM]WEST", "서부": "[GM]WEST",
    "east": "[GM]EAST", "gm_east": "[GM]EAST", "동부": "[GM]EAST",
    "bcm": "BCM", "jbt": "JBT", "kbt": "KBT",
    "db": "DB", "데이터분석": "DB", "it": "IT",
    "피플": "PEOPLE", "people": "PEOPLE",
    "b2b": "B2B2", "b2b2": "B2B2", "해외영업": "B2B2",
    "b2b1": "B2B1", "국내영업": "B2B1",
    "notion_cs": "CS", "cs": "CS",
    "craver": "Craver", "크레이버": "Craver",
    "log": "LOG", "물류": "LOG",
    "fi": "FI", "재무": "FI",
    "op": "OP", "운영": "OP",
}

# ── Qdrant Cloud 설정 (env 우선, 없으면 .env 파일 참조) ──────────────────────
def _qdrant_url() -> str:
    return os.getenv("QDRANT_URL", "https://bf41bcbe-af68-416f-9d26-1b3d64f7bed0.us-east-1-1.aws.cloud.qdrant.io:6333")

def _qdrant_api_key() -> str:
    return os.getenv("QDRANT_API_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2Nlc3MiOiJtIiwic3ViamVjdCI6ImFwaS1rZXk6OTFkOGVkZWYtNTFkNi00ODNhLTg0MDItZTdjNjI0ZjA2NThmIn0.K0zdMdpnbIMl_yfXV8EJfcClpPnkoPa_SS_XbDI1kv4")


_client = None

def _get_client():
    global _client
    if _client is None:
        from qdrant_client import QdrantClient
        _client = QdrantClient(url=_qdrant_url(), api_key=_qdrant_api_key(), timeout=15)
        logger.info("qdrant_client_connected", url=_qdrant_url())
    return _client


# ── 로컬 JSON → Qdrant Cloud 전체 업로드 ────────────────────────────────────

def _upload_local_to_cloud() -> int:
    """로컬 JSON → Qdrant Cloud upsert (기존 데이터 보존)."""
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams, PointStruct

    if not _LOCAL_JSON.exists():
        logger.warning("local_json_not_found")
        return 0

    with open(_LOCAL_JSON, "r", encoding="utf-8") as f:
        raw = json.load(f)

    points = []
    for pt in raw:
        v = pt.get("vector")
        if not v:
            continue
        points.append(PointStruct(
            id=pt.get("id") or str(len(points)),
            vector=v,
            payload=pt.get("payload", {}),
        ))

    if not points:
        return 0

    client = _get_client()

    # 컬렉션 없으면 생성, 있으면 기존 데이터 유지 후 upsert
    existing = [c.name for c in client.get_collections().collections]
    if COLLECTION not in existing:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
        )

    # 배치 upsert (100개씩) — 기존 포인트는 덮어쓰기, 신규는 추가
    batch_size = 100
    for i in range(0, len(points), batch_size):
        client.upsert(collection_name=COLLECTION, points=points[i:i + batch_size])

    logger.info("qdrant_cloud_upserted", count=len(points), collection=COLLECTION)
    return len(points)


def reload_vectors():
    """로컬 JSON 갱신 후 Qdrant Cloud 재업로드 (파이프라인 실행 후 호출)."""
    global _client
    _client = None  # 클라이언트 재초기화
    count = _upload_local_to_cloud()
    logger.info("notion_vectors_reloaded", count=count)


# ── 검색 ──────────────────────────────────────────────────────────────────────

async def _embed_query(query: str) -> list[float]:
    from google import genai
    settings = get_settings()
    client = genai.Client(api_key=settings.gemini_api_key)
    result = await asyncio.to_thread(
        client.models.embed_content,
        model=EMBEDDING_MODEL, contents=[query],
        config={"output_dimensionality": EMBEDDING_DIM},
    )
    return result.embeddings[0].values


def _search(vector: list[float], team_filter: Optional[str] = None, top_k: int = TOP_K) -> list[dict]:
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    client = _get_client()
    query_filter = None
    if team_filter:
        query_filter = Filter(
            must=[FieldCondition(key="team", match=MatchValue(value=team_filter))]
        )

    results = client.query_points(
        collection_name=COLLECTION,
        query=vector,
        query_filter=query_filter,
        limit=top_k,
        score_threshold=SCORE_THRESHOLD,
    )
    return [{"score": r.score, "payload": r.payload} for r in results.points]


def _format_results(results: list[dict]) -> str:
    if not results:
        return "검색 결과 없음"
    chunks = []
    for i, r in enumerate(results, 1):
        p = r["payload"]
        score = r["score"]
        team  = p.get("team", "?")
        title = p.get("page_title", "?")
        section = p.get("section_path", "")
        text  = p.get("text", "")[:2000]
        url   = p.get("page_url", "")
        header = f"[{i}] ({score:.2f}) {team} > {title}"
        if section:
            header += f" > {section}"
        chunks.append(f"{header}\n{text}\n출처: {url}")
    return "\n\n---\n\n".join(chunks)


async def run(query: str, team_key: Optional[str] = None, model_type: str = "gemini") -> str:
    team_filter = TEAM_MAP.get(team_key.lower(), None) if team_key else None
    logger.info("qdrant_search_start", query=query[:80], team_key=team_key, team_filter=team_filter)

    try:
        vector = await _embed_query(query)
    except Exception as e:
        logger.error("qdrant_embedding_failed", error=str(e))
        return f"임베딩 생성 실패: {e}"

    try:
        results = _search(vector, team_filter=team_filter, top_k=TOP_K)
    except Exception as e:
        logger.error("qdrant_search_failed", error=str(e))
        return f"벡터 검색 실패: {e}"

    logger.info("qdrant_search_done", result_count=len(results),
                top_score=results[0]["score"] if results else 0)

    if not results:
        label = team_filter or "전체"
        return f"**{label}** 팀 자료에서 '{query}'와 관련된 문서를 찾을 수 없습니다.\n\n다른 키워드로 검색해보세요."

    context = _format_results(results)
    llm = get_flash_client()
    label = team_filter or "전체"

    prompt = f"""{LANGUAGE_DETECTION_RULE}

당신은 Craver의 사내 문서 검색 도우미입니다.
아래는 사용자의 질문과 벡터 검색으로 찾은 관련 문서입니다.

## 사용자 질문
{query}

## 검색된 문서 ({len(results)}건, 팀: {label})
{context}

## ⚠️ 최우선 규칙
- **반드시 위 '검색된 문서' 내용에서만 답변하세요!**
- 당신의 사전 학습 지식으로 답변하지 마세요. 검색 결과에 있는 정보만 사용하세요.
- "보유한 정보에 포함되어 있지 않습니다" 같은 거부 답변 금지! 검색 결과에 내용이 있으면 반드시 추출해서 답변하세요.
- 숫자, 번호, 주소, 이름 등 구체적 정보가 문서에 있으면 그대로 인용하세요.

## 답변 형식
- 검색된 문서 내용을 직접 요약하여 답변 (링크만 달지 마세요!)
- 핵심을 구조적으로 정리 (제목, 요약, 상세 내용)
- 출처 링크 제공: [문서명](URL)
- 매칭 결과가 부족하면 "관련 자료가 제한적입니다" 안내
- 답변 마지막 출처:
  ---
  *Notion 사내 문서 검색 · {label} 팀 자료*

## 후속 질문
> 💡 **이런 것도 물어보세요**
> - 관련된 다른 정보 질문 (구체적으로 작성)
> - 같은 주제의 다른 문서 검색 질문
"""

    try:
        answer = await asyncio.to_thread(llm.generate, prompt, None, 0.3, 2048)
        return answer
    except Exception as e:
        logger.error("qdrant_answer_failed", error=str(e))
        return f"답변 생성 중 오류: {e}"
