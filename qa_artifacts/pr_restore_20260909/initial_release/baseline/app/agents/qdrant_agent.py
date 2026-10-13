"""Notion 사내 문서 검색 — Qdrant Cloud 벡터 검색.

로컬 JSON(notion_vectors_gemini.json)을 소스 오브 트루스로 유지하고,
Qdrant Cloud를 실제 벡터 검색 백엔드로 사용한다.

- 검색: Gemini embedding → Qdrant Cloud query → Gemini Flash 답변
- 업데이트: 파이프라인 실행 후 reload_vectors() → Cloud 전체 재업로드
"""

import asyncio
import json
import os
import re
import uuid
from pathlib import Path
from typing import Optional

_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I
)

def _normalize_id(raw_id, payload: dict) -> str:
    if isinstance(raw_id, int) and raw_id >= 0:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, str(raw_id)))
    if isinstance(raw_id, str) and _UUID_RE.match(raw_id):
        return raw_id
    seed = (payload.get("page_url") or "") + (payload.get("text") or "")[:80]
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed or str(uuid.uuid4())))

import structlog

from app.config import get_settings
from app.core.llm import get_flash_client
from app.core.prompt_fragments import LANGUAGE_DETECTION_RULE

logger = structlog.get_logger(__name__)

EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIM   = 1536
TOP_K           = 8
SCORE_THRESHOLD = 0.45
QUALITY_GATE    = 0.57  # 최상위 결과가 이 이하면 관련 자료 없음 처리
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


def resolve_team_filter(team_key: Optional[str]) -> Optional[str]:
    """@@키 → Qdrant payload 의 team 값.

    오케스트레이터의 데이터소스 키는 "GM EAST" 처럼 공백을 쓰는데 TEAM_MAP 은
    "gm_east" 로 갖고 있어 lower() 조회만으로는 빗나갔다. 그러면 필터가 None 이
    되어 **선택하지 않은 팀 문서까지 전부 검색**됐다 (2026-08-05 발견).

    범위 선택 기능은 매핑이 어긋났을 때 **닫히는 쪽**으로 실패해야 한다.
    모르는 키는 원문을 그대로 필터로 써서 결과가 비게 만든다 — 남의 팀 문서가
    섞여 나오는 것보다 아무것도 안 나오는 편이 안전하고, 신호도 분명하다.
    """
    if not team_key:
        return None
    raw = team_key.strip()
    k = raw.lower()
    if k in TEAM_MAP:
        return TEAM_MAP[k]
    # 공백/언더스코어 표기 차이 흡수 ("gm east" ↔ "gm_east" ↔ "gmeast")
    squashed = k.replace(" ", "").replace("_", "").replace("-", "")
    for alias, team in TEAM_MAP.items():
        if alias.replace(" ", "").replace("_", "").replace("-", "") == squashed:
            return team
    logger.warning("qdrant_team_key_unmapped", team_key=raw,
                   note="TEAM_MAP 에 없어 원문으로 필터한다 (전체 검색 방지)")
    return raw


_TEAM_COUNTS: Optional[dict] = None


def index_team_counts(refresh: bool = False) -> dict:
    """색인에 팀별 조각이 몇 개 들어 있나 — 로컬 JSON(소스 오브 트루스) 기준.

    ⛔ 손으로 적은 팀 목록을 두지 않는다. 이 프로젝트가 반복해서 당한 사고가
       "코드에 적어둔 값 목록이 낡아 조용히 0건" 이다 (Continent1·마케팅 team).
       여기서도 **색인이 스스로 말하게** 한다.

    `resolve_team_filter()` 가 낸 값으로 조회하면 그 팀이 실제로 검색될 수 있는지
    바로 알 수 있다 — 0 이면 그 `@@` 칩은 눌러도 빈손이다.
    """
    global _TEAM_COUNTS
    if _TEAM_COUNTS is not None and not refresh:
        return _TEAM_COUNTS
    try:
        raw = json.loads(_LOCAL_JSON.read_text(encoding="utf-8"))
        points = raw if isinstance(raw, list) else (raw.get("points") or raw.get("vectors") or [])
        counts: dict = {}
        for pt in points:
            payload = pt.get("payload", pt)
            team = payload.get("team")
            if team:
                counts[team] = counts.get(team, 0) + 1
        _TEAM_COUNTS = counts
    except Exception as e:
        # ⚠️ 삼키지 말 것 — 여기가 조용하면 "자료 없음"과 "파일 못 읽음"이 같아 보인다
        logger.warning("qdrant_index_counts_failed", error=str(e), path=str(_LOCAL_JSON))
        _TEAM_COUNTS = {}
    return _TEAM_COUNTS


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
        payload = pt.get("payload", {})
        points.append(PointStruct(
            id=_normalize_id(pt.get("id"), payload),
            vector=v,
            payload=payload,
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
        # ⛔ **문서 날짜를 안 넘기면 LLM 이 낡은 문서를 고르고도 모른다** (2026-08-18 확인).
        #    "야근 식대 지원한도"를 물었을 때 `복리후생`(15,000원)과 2023-03-31 자
        #    `Database (FAQ, Terms)`(10,000원)가 **서로 다른 값**을 담고 있었는데,
        #    앱은 2023년 것을 골라 10,000원이라고 답했다. 제보자가 "15000원임"이라고
        #    바로잡았고(2026-07-08) 그게 맞았다. 날짜가 컨텍스트에 없으니 LLM 은
        #    어느 쪽이 최신인지 알 방법이 없었다.
        edited = str(p.get("last_edited_time") or "")[:10]
        header = f"[{i}] ({score:.2f}) {team} > {title}"
        if section:
            header += f" > {section}"
        header += f"  [문서 수정일: {edited or '미상'}]"
        chunks.append(f"{header}\n{text}\n출처: {url}")
    return "\n\n---\n\n".join(chunks)


async def run(query: str, team_key: Optional[str] = None, model_type: str = "gemini") -> str:
    team_filter = resolve_team_filter(team_key)
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

    if not results or results[0]["score"] < QUALITY_GATE:
        # ⛔ **팀을 지정했으면 구글로 새지 않는다** (2026-08-25).
        #    `@@JBT`·`@@B2B1` 은 색인 조각이 0건이라 여기로 떨어지는데, 그대로
        #    Google 검색 답변이 나갔다. 사내 자료를 물었는데 인터넷 글이 답으로
        #    오는 것이고, 꼬리말의 "사내 문서와 무관" 한 줄로는 그 사실이 읽히지
        #    않는다 — 잡음은 답처럼 보인다(넓혀 찾기에서 이미 겪은 실패).
        #    범위를 좁혀 물은 사람에게는 **없다고 말하는 편이 낫다.**
        if team_filter:
            indexed = index_team_counts().get(team_filter, 0)
            logger.warning("qdrant_pinned_empty", team_key=team_key,
                           team_filter=team_filter, indexed=indexed, query=query[:80])
            if indexed == 0:
                return (
                    f"**{team_filter}** 팀 자료는 사내 문서 색인에 아직 없습니다 (색인 0건).\n\n"
                    "팀 지정을 빼고 다시 물어보시면 전체 사내 문서에서 찾아드립니다."
                )
            return (
                f"**{team_filter}** 팀 자료 {indexed}건 안에서는 '{query}'와 관련된 문서를 "
                "찾지 못했습니다.\n\n다른 키워드로 묻거나, 팀 지정을 빼고 전체에서 찾아보세요."
            )

        # 팀 지정이 없을 때만 → 사내 문서에 없는 질문으로 보고 Google Search 폴백
        try:
            flash = get_flash_client()
            search_prompt = f"""{LANGUAGE_DETECTION_RULE}

사용자의 질문에 Google 검색 결과를 활용해 정확하고 유익하게 답변하세요.

## 질문
{query}

## 답변 지침
- 검색 결과에 기반해 명확하고 구체적으로 답변
- 정보가 있으면 출처(URL) 인용
- 질문 언어에 맞춰 답변
- 불필요한 면책 문구 없이 바로 답변 시작
"""
            answer = await asyncio.to_thread(
                flash.generate_with_search, search_prompt, None, 0.3, 2048
            )
            return answer + "\n\n---\n*Google 검색 기반 답변 · 사내 문서와 무관*"
        except Exception as e:
            logger.warning("qdrant_search_fallback_failed", error=str(e))
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
- 검색된 문서에 관련 내용이 있으면 **즉시 답변하세요**. "찾을 수 없습니다"로 시작하지 마세요.
- 검색된 문서에 전혀 관련 내용이 없을 때만 "관련 자료가 없습니다"라고 안내하세요.
- 숫자, 번호, 주소, 이름 등 구체적 정보가 문서에 있으면 그대로 인용하세요.

## 답변 형식
- 검색된 문서 내용을 직접 요약하여 답변 (링크만 달지 마세요!)
- 핵심을 구조적으로 정리 (제목, 요약, 상세 내용)
- 출처 링크 제공: [문서명](URL)
- 부분적으로만 답변 가능해도 아는 범위에서 먼저 답변하고, 보완이 필요한 부분만 언급하세요
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
        return answer + _vintage_note(results, answer)
    except Exception as e:
        logger.error("qdrant_answer_failed", error=str(e))
        return f"답변 생성 중 오류: {e}"


# 사내 문서가 몇 년 전 것이면 그 사실 자체가 답의 일부다
_STALE_DAYS = 365


def _vintage_note(results: list[dict], answer: str = "") -> str:
    """근거 문서가 오래됐으면 **코드가** 연식을 밝힌다.

    ⛔ 프롬프트로 시켰더니 LLM 이 그냥 빠뜨렸다 (2026-08-18 실측 — 질문에서 명시적으로
       요청했는데도 안 적었다). 사내 규정은 바뀌는데 문서는 안 따라오므로, 연식은
       **답의 일부**다. 그래서 LLM 이 아니라 코드가 붙인다.

    실제 사고: "야근 식대 지원한도"에 2023-03-31 자 FAQ 의 10,000원으로 답했다.
    같은 워크스페이스의 `복리후생` 문서에는 15,000원으로 적혀 있었지만 그 청크는
    상위에 오르지 않았다(질문이 FAQ 의 문답 형태와 더 닮았다). 제보자가 "15000원임"
    이라고 바로잡기 전까지 아무도 몰랐다 (2026-07-08 붐따).
    ⚠️ 검색 순위를 최신순으로 바꾸는 것은 답이 아니다 — 최신이 곧 관련 있는 것은
       아니다. 대신 **연식을 보이게 해서 사람이 의심할 수 있게** 한다.
    """
    from datetime import datetime, timezone

    # ⚠️ 판정 기준은 **답변이 실제로 인용한 문서**다.
    #    예전엔 "상위 3건 모두 오래됐을 때"만 봤는데, 그러면 상위에 최신 청크가 하나만
    #    섞여도 경고가 사라진다 — 붐따 #105 가 정확히 그랬다 (2026-08-25 실측).
    #    답변은 2023-03-31 자 FAQ **하나만** 인용했는데 경고가 안 붙었다.
    #    검색 상위에 무엇이 걸렸는지가 아니라 **답을 만든 근거**가 낡았는지가 중요하다.
    #    출처 링크가 없는 답변도 있으므로 그때만 1순위로 되돌아간다.
    _hex = re.compile(r"[^0-9a-f]")
    _ans_hex = _hex.sub("", (answer or "").lower())
    cited = []
    for r in (results or []):
        url = str((r.get("payload") or {}).get("page_url") or "")
        if not url or not answer:
            continue
        pid = _hex.sub("", url.split("/")[-1].lower())[-32:]
        if url in answer or (len(pid) == 32 and pid in _ans_hex):
            cited.append(r)

    pool = cited or (results or [])[:1]
    top = []
    for r in pool:
        raw = str((r.get("payload") or {}).get("last_edited_time") or "")[:10]
        if len(raw) == 10:
            try:
                top.append(datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc))
            except ValueError:
                pass
    if not top:
        # ⛔ `last_edited_time == ""` 는 버그가 아니라 **표식**이다 (`ingest_page.py`):
        #    노션 인테그레이션에 공유되지 않아 공개 링크를 긁어 넣은 페이지다.
        #    실측 (2026-08-25): 색인 1,854 청크 중 388개(20.9%)가 이 상태이고,
        #    그 22개 페이지에 **복리후생·근태/휴가·보상·채용·퇴사** 가 몰려 있다.
        #    그래서 "값이 다르면 최신 문서를 따르라" 가 **그 문서들에선 작동할 수 없다**
        #    — 붐따 #105 가 그 경우다 (15,000원 문서는 날짜가 없고, 10,000원 문서는
        #    2023-03-31 이라 LLM 이 날짜 있는 쪽을 골랐다).
        #    값을 코드가 고를 수는 없다. **모른다는 사실을 보이게** 한다.
        if pool:
            logger.warning("notion_answer_from_undated_docs",
                           titles=[str((r.get("payload") or {}).get("page_title"))
                                   for r in pool][:3])
            return ("\n\n> ⏳ 참고한 사내 문서의 **수정 시점을 알 수 없습니다** "
                    "(노션 공개 링크로 수집된 문서). 더 최신 규정이 따로 있을 수 있으니 "
                    "중요한 건이면 담당 부서에 확인해 주세요.")
        return ""
    newest = max(top)          # 인용한 것 중 가장 최신 — 그것도 낡았을 때만 경고한다
    age = (datetime.now(timezone.utc) - newest).days
    if age < _STALE_DAYS:
        return ""
    logger.warning("notion_answer_from_stale_docs", newest=newest.date().isoformat(), age_days=age)
    return (f"\n\n> ⏳ 참고한 사내 문서 중 가장 최근 것이 **{newest:%Y-%m-%d}** 자입니다 "
            f"({age // 365}년 이상 지남). 규정이 그 뒤에 바뀌었을 수 있으니 "
            f"중요한 건이면 담당 부서에 확인해 주세요.")
