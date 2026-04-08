"""Team Resource Agent — 팀별 자료 검색 (CS Agent 패턴).

DB HUB에서 동기화된 팀별 자료(Google Sheets, Notion 등)를
키워드 매칭으로 검색하여 링크와 설명을 반환.
"""
import asyncio
import re
from typing import Dict, List, Optional

import structlog

from app.core.llm import get_flash_client
from app.core.prompt_fragments import LANGUAGE_DETECTION_RULE
from app.db.mariadb import fetch_all

logger = structlog.get_logger(__name__)

_resource_cache: List[Dict] = []
_cache_loaded: bool = False
_last_sync: str = ""


async def warmup() -> int:
    import asyncio
    global _resource_cache, _cache_loaded, _last_sync

    def _load():
        return fetch_all(
            "SELECT id, parent_id, team, node_type, name, resource_type, url, description, "
            "depth, sort_order, synced_at "
            "FROM team_resources ORDER BY depth, sort_order"
        )

    rows = await asyncio.to_thread(_load)
    # Build ancestor path text for each node (improves search relevance)
    id_map = {r["id"]: r for r in rows}
    for r in rows:
        parts = []
        pid = r.get("parent_id")
        depth = 0
        while pid and pid in id_map and depth < 10:
            parent = id_map[pid]
            if parent.get("node_type") not in ("team",):
                parts.append(parent["name"])
            pid = parent.get("parent_id")
            depth += 1
        r["_ancestor_text"] = " ".join(reversed(parts))
    _resource_cache = rows
    _cache_loaded = True
    if rows:
        _last_sync = str(rows[0].get("synced_at", ""))
    logger.info("team_resources_warmup", count=len(rows))
    return len(rows)


def _tokenize(text: str) -> set:
    return set(re.findall(r'[가-힣a-zA-Z0-9]+', text.lower()))


def _word_overlap_score(query_tokens: set, target_text: str) -> float:
    if not target_text:
        return 0.0
    target_tokens = _tokenize(target_text)
    if not target_tokens:
        return 0.0
    overlap = query_tokens & target_tokens
    return len(overlap) / max(len(query_tokens), 1)


_TEAM_ALIASES = {
    "일본": "JBT", "일본사업": "JBT", "jbt": "JBT",
    "bcm": "BCM", "브랜드커뮤니케이션": "BCM", "브커": "BCM",
    "이스트": "GM EAST", "east": "GM EAST", "동남아": "GM EAST",
    "east1": "GM EAST", "east2": "GM EAST", "gm east": "GM EAST",
    "웨스트": "GM WEST", "west": "GM WEST", "gm west": "GM WEST",
    "it": "IT", "아이티": "IT",
    "크레이버": "Craver", "craver": "Craver",
}


_QUERY_EXPAND = {
    "복지포인트": "사내근로복지기금 복지 포인트",
    "복지카드": "사내근로복지기금 복지 카드 비즈플레이",
    "와이파이": "네트워크 Wi-Fi wifi 사용 안내",
    "인센티브": "성과급 보상 인센티브",
    "성과금": "성과급 보상",
}

# HR/People 관련 키워드 → PEOPLE 팀 가중치
_PEOPLE_BOOST_KW = [
    "퇴사", "퇴직", "연차", "휴가", "경조", "성과급", "성과금", "보상", "인센티브",
    "채용", "면접", "명함", "서류", "증명서", "급여", "복지", "교육", "핵심가치",
    "역량", "평가", "졸업", "출산", "건강검진", "전사휴무", "휴일대체",
    "잔디", "다우오피스", "vpn", "프린터", "와이파이", "wifi", "메일", "캘린더",
    "회의실", "커피", "분리수거", "시설", "비품",
]


def search_resources(query: str, top_k: int = 10, allowed_resources: Optional[Dict[str, list]] = None) -> List[Dict]:
    if not _cache_loaded or not _resource_cache:
        return []

    # Expand query with synonyms
    expanded = query
    for key, expansion in _QUERY_EXPAND.items():
        if key in query:
            expanded = f"{query} {expansion}"
            break

    q_tokens = _tokenize(expanded)
    q_lower = expanded.lower()

    # Detect if query is HR/People-related → boost PEOPLE team results
    _people_boost = any(kw in q_lower for kw in _PEOPLE_BOOST_KW)

    # allowed_ids: set of allowed resource IDs (None = all)
    allowed_ids = None
    if allowed_resources is not None:
        allowed_ids = set()
        for team, ids in allowed_resources.items():
            if ids:
                allowed_ids.update(ids)

    scored = []
    for r in _resource_cache:
        # Skip non-leaf nodes (team, folder)
        if r.get("node_type") in ("team", "folder"):
            continue
        # Filter by allowed IDs
        if allowed_ids is not None and r.get("id") not in allowed_ids:
            continue
        score = 0.0
        team_lower = r["team"].lower()
        if team_lower in q_lower:
            score += 3.0
        # PEOPLE team boost for HR/IT queries
        if _people_boost and r["team"] == "PEOPLE":
            score += 2.0
        for alias, canonical in _TEAM_ALIASES.items():
            if alias in q_lower and r["team"] == canonical:
                score += 3.0
                break
        score += _word_overlap_score(q_tokens, r["name"]) * 2.0
        # Ancestor path boosts relevance (e.g., KPIs → 10월 KPI)
        ancestor = r.get("_ancestor_text") or ""
        score += _word_overlap_score(q_tokens, ancestor) * 1.5
        desc = r.get("description") or ""
        score += _word_overlap_score(q_tokens, desc) * 0.5
        # Direct chunk matching (handles Korean 붙여쓰기)
        name_lower = r["name"].lower()
        desc_lower = desc.lower()
        for size in (3, 2):
            for i in range(len(q_lower) - size + 1):
                chunk = q_lower[i:i+size]
                if not re.match(r'[가-힣a-z]+$', chunk):
                    continue
                w = 0.8 if size == 3 else 0.4
                if chunk in name_lower:
                    score += w
                if chunk in desc_lower:
                    score += w * 0.4
        if score > 0:
            scored.append((score, r))

    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored[:top_k]]


def _format_resource_context(matched: List[Dict]) -> str:
    if not matched:
        return "검색 결과가 없습니다."
    lines = []
    total_chars = 0
    max_total = 6000  # LLM context budget for resource content
    for i, r in enumerate(matched, 1):
        meta = f"[{r['team']}]"
        rtype = {"google_sheet": "📊 Google Sheet", "notion": "📋 Notion",
                 "google_drive": "📁 Google Drive", "other": "🔗 기타"}.get(r.get("resource_type", "other"), "🔗")
        lines.append(f"{i}. {meta} | {rtype}\n   이름: {r['name']}\n   링크: {r.get('url') or 'N/A'}")
        desc = r.get("description", "")
        if desc and total_chars < max_total:
            # Include full description content (page text from Playwright crawl)
            budget = max_total - total_chars
            content = desc[:budget]
            lines.append(f"   내용:\n{content}")
            total_chars += len(content)
    return "\n".join(lines)


async def run(query: str, model_type: str = "gemini", allowed_resources: Optional[Dict[str, list]] = None) -> str:
    if not _cache_loaded:
        await warmup()

    matched = search_resources(query, top_k=8, allowed_resources=allowed_resources)
    context = _format_resource_context(matched)

    llm = get_flash_client()
    prompt = f"""{LANGUAGE_DETECTION_RULE}

당신은 SKIN1004의 사내 자료 검색 도우미입니다.
아래는 사용자의 질문과 매칭된 팀별 자료 목록입니다.

## 사용자 질문
{query}

## 검색된 자료 ({len(matched)}건)
{context}

## 답변 규칙
- **자료에 '내용' 필드가 있으면 그 내용을 직접 요약하여 답변하세요** (링크만 달지 마세요!)
- 링크가 있으면 출처로 함께 제공: [자료명](URL)
- 내용이 없고 링크만 있는 자료는 링크를 안내하세요
- 팀/카테고리별로 그룹화하여 보여주세요
- 매칭 결과가 없으면 "해당 자료를 찾을 수 없습니다" 안내
- 답변 마지막에 출처 표시:
  ---
  *팀별 자료 검색 · 마지막 동기화: {_last_sync}*

## 후속 질문
> 💡 **이런 것도 물어보세요**
> - [관련 팀의 다른 자료]
> - [같은 카테고리의 다른 시트]
"""

    try:
        answer = await asyncio.to_thread(llm.generate, prompt, temperature=0.3, max_output_tokens=2048)
        return answer
    except Exception as e:
        logger.error("team_agent_failed", error=str(e))
        return f"팀별 자료 검색 중 오류가 발생했습니다: {e}"
