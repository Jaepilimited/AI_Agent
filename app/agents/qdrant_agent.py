"""Notion 사내 문서 검색 — 로컬 벡터 검색.

Qdrant Craver에서 가져온 데이터를 Gemini embedding-001로 재임베딩하여 로컬 저장.
검색: Gemini embedding → 로컬 코사인 유사도 → Gemini Flash 답변 생성.
데이터 업데이트: Qdrant Craver에서 재다운로드 + 재임베딩.
"""

import asyncio
import json
from pathlib import Path
from typing import Optional

import numpy as np
import structlog

from app.config import get_settings
from app.core.llm import get_flash_client
from app.core.prompt_fragments import LANGUAGE_DETECTION_RULE

logger = structlog.get_logger(__name__)

EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIM = 1536
TOP_K = 8
SCORE_THRESHOLD = 0.3

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

# ── Local vector store (numpy-backed, pre-normalized for fast cosine) ──
_vecs: Optional[np.ndarray] = None        # shape (N, D), float32, L2-normalized
_payloads: list[dict] = []                # parallel array, len N
_teams: Optional[np.ndarray] = None       # shape (N,), dtype=object (team strings)
_loaded = False


def _load_store():
    global _vecs, _payloads, _teams, _loaded
    if _loaded:
        return
    path = Path(__file__).resolve().parent.parent.parent / "data" / "notion_vectors_gemini.json"
    if not path.exists():
        logger.warning("notion_vectors_not_found", path=str(path))
        _loaded = True
        return
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    payloads: list[dict] = []
    vec_rows: list[list[float]] = []
    teams_list: list[str] = []
    for pt in raw:
        v = pt.get("vector")
        if not v:
            continue
        p = pt.get("payload", {})
        payloads.append(p)
        vec_rows.append(v)
        teams_list.append(p.get("team", ""))

    arr = np.asarray(vec_rows, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    _vecs = arr / norms                              # pre-normalize once
    _payloads = payloads
    _teams = np.asarray(teams_list, dtype=object)
    _loaded = True
    logger.info("notion_vectors_loaded", count=len(_payloads), dim=arr.shape[1] if arr.size else 0)


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


def _search(vector, team_filter=None, top_k=TOP_K):
    _load_store()
    if _vecs is None or _vecs.size == 0:
        return []

    q = np.asarray(vector, dtype=np.float32)
    qn = np.linalg.norm(q)
    if qn == 0:
        return []
    q = q / qn

    sims = _vecs @ q                                 # cosine: both sides normalized
    if team_filter is not None and _teams is not None:
        sims = np.where(_teams == team_filter, sims, -1.0)

    above = np.where(sims >= SCORE_THRESHOLD)[0]
    if above.size == 0:
        return []

    if above.size > top_k:
        scores_above = sims[above]
        part = np.argpartition(-scores_above, top_k)[:top_k]
        selected = above[part[np.argsort(-scores_above[part])]]
    else:
        selected = above[np.argsort(-sims[above])]

    return [{"score": float(sims[i]), "payload": _payloads[i]} for i in selected]


def _format_results(results):
    if not results:
        return "검색 결과 없음"
    chunks = []
    for i, r in enumerate(results, 1):
        p = r["payload"]
        score = r["score"]
        team = p.get("team", "?")
        title = p.get("page_title", "?")
        section = p.get("section_path", "")
        text = p.get("text", "")[:2000]
        url = p.get("page_url", "")
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

    logger.info("qdrant_search_done", result_count=len(results), top_score=results[0]["score"] if results else 0)

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
