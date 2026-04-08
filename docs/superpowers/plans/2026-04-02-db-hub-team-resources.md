# DB HUB 팀별 자료 시스템 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Notion DB HUB에서 팀별 자료를 크롤링하여 MariaDB에 정규화 저장하고, CS Agent와 동일한 패턴으로 검색 가능한 에이전트를 만든다. 매일 01:00 자동 동기화. System Status UI를 그룹화.

**Architecture:** Notion API로 팀별 토글/테이블을 재귀 크롤링 → 플랫 정규화 → MariaDB `team_resources` 테이블 저장. `TeamAgent`가 키워드 매칭으로 검색하여 링크/설명 반환. APScheduler로 매일 01:00 동기화. System Status drawer를 매출/마케팅/팀별/업무도구 4그룹으로 개편.

**Tech Stack:** Notion API (httpx), MariaDB (pymysql), APScheduler, FastAPI lifespan

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| CREATE | `app/agents/team_agent.py` | 팀별 자료 검색 에이전트 (CS Agent 패턴 복제) |
| CREATE | `scripts/sync_team_resources.py` | Notion → MariaDB 크롤러 (standalone + importable) |
| MODIFY | `app/db/mariadb.py` | `team_resources` 테이블 DDL 추가 |
| MODIFY | `app/agents/orchestrator.py` | team 라우트 + 키워드 + _handle_team |
| MODIFY | `app/core/safety.py` | team_resources 서비스 상태 등록 |
| MODIFY | `app/main.py` | 시작 시 warmup + APScheduler 01:00 cron |
| MODIFY | `app/frontend/chat.js` | System Status 그룹화 UI + CS→BP 이름 변경 |
| MODIFY | `app/static/style.css` | 그룹 접기/펼치기 스타일 |
| MODIFY | `app/frontend/chat.html` | 캐시 버스팅 버전 증가 |
| MODIFY | `requirements.txt` | apscheduler 추가 |

---

### Task 1: MariaDB 테이블 생성

**Files:**
- Modify: `app/db/mariadb.py` (DDL 섹션, `_SQLITE_SCHEMA` 문자열 근처)

- [ ] **Step 1: team_resources DDL을 mariadb.py에 추가**

`app/db/mariadb.py`의 `_ensure_tables()` 또는 `_SQLITE_SCHEMA` 문자열 끝에 추가:

```python
# team_resources 테이블 — DB HUB 팀별 자료 정규화 저장
_TEAM_RESOURCES_DDL = """
CREATE TABLE IF NOT EXISTS team_resources (
    id INT AUTO_INCREMENT PRIMARY KEY,
    team VARCHAR(50) NOT NULL COMMENT '팀명 (JBT, BCM, GM_EAST 등)',
    category VARCHAR(100) DEFAULT '' COMMENT '하위 카테고리 (MKT, BEA, BXM, 이커머스 등)',
    name VARCHAR(255) NOT NULL COMMENT '시트/페이지 이름',
    resource_type ENUM('google_sheet', 'notion', 'google_drive', 'other') NOT NULL DEFAULT 'other',
    url TEXT COMMENT '링크 URL',
    description TEXT DEFAULT '' COMMENT '비고/설명',
    synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '마지막 동기화 시각',
    INDEX idx_team (team),
    INDEX idx_team_cat (team, category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""
```

- [ ] **Step 2: 앱 시작 시 테이블 자동 생성 함수 추가**

`app/db/mariadb.py`에 함수 추가:

```python
def ensure_team_resources_table():
    """Create team_resources table if not exists."""
    try:
        execute(_TEAM_RESOURCES_DDL)
        logger.info("team_resources_table_ensured")
    except Exception as e:
        logger.warning("team_resources_table_error", error=str(e))
```

- [ ] **Step 3: 테이블 생성 확인**

Run: `python -X utf8 -c "from app.db.mariadb import ensure_team_resources_table; ensure_team_resources_table(); print('OK')"`

Expected: OK, 테이블 생성 완료

- [ ] **Step 4: Commit**

```bash
git add app/db/mariadb.py
git commit -m "feat: team_resources MariaDB 테이블 DDL 추가"
```

---

### Task 2: Notion 크롤러 스크립트

**Files:**
- Create: `scripts/sync_team_resources.py`

- [ ] **Step 1: Notion 크롤러 구현**

`scripts/sync_team_resources.py` — 전체 코드:

```python
"""Sync team resources from Notion DB HUB → MariaDB.

Usage:
    python scripts/sync_team_resources.py              # Full sync
    python scripts/sync_team_resources.py --dry-run    # Preview only
"""
import os
import re
import sys
import argparse
from datetime import datetime
from typing import List, Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

import httpx
import structlog

logger = structlog.get_logger(__name__)

# Notion API setup
TOKEN = os.getenv("NOTION_MCP_TOKEN")
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}
CLIENT = httpx.Client(timeout=30)

# DB HUB page → 팀별 중요 데이터 취합 toggle
DB_HUB_PAGE_ID = "2e12b4283b008011ae32e39bf73b7f7b"
TEAM_DATA_TOGGLE_ID = "3272b428-3b00-806d-aabf-cfbcd9237fb0"

# Teams to SKIP (marked with X or empty)
SKIP_TEAMS = {
    "DB", "KBT", "OP", "FI", "PEOPLE", "LOG",
    "유통1(노션x)", "유통2(노션x)", "B2B1", "B2B2", "SCM",
    "",  # empty toggles
}

# URL pattern detection
_URL_RE = re.compile(r'https?://[^\s<>"]+')
_GSHEET_RE = re.compile(r'docs\.google\.com/spreadsheets')
_GDRIVE_RE = re.compile(r'drive\.google\.com')
_NOTION_RE = re.compile(r'notion\.so/')


def _detect_type(url: str) -> str:
    """Detect resource type from URL."""
    if not url:
        return "other"
    if _GSHEET_RE.search(url):
        return "google_sheet"
    if _GDRIVE_RE.search(url):
        return "google_drive"
    if _NOTION_RE.search(url):
        return "notion"
    return "other"


def _get_block_children(block_id: str) -> list:
    """Fetch all children of a Notion block (handles pagination)."""
    all_results = []
    url = f"https://api.notion.com/v1/blocks/{block_id}/children?page_size=100"
    while url:
        resp = CLIENT.get(url, headers=HEADERS)
        if resp.status_code != 200:
            logger.warning("notion_fetch_failed", block_id=block_id, status=resp.status_code)
            break
        data = resp.json()
        all_results.extend(data.get("results", []))
        if data.get("has_more"):
            cursor = data.get("next_cursor")
            url = f"https://api.notion.com/v1/blocks/{block_id}/children?page_size=100&start_cursor={cursor}"
        else:
            url = None
    return all_results


def _extract_text(rich_text_list: list) -> str:
    """Extract plain text from Notion rich_text array."""
    return "".join(t.get("plain_text", "") for t in rich_text_list).strip()


def _extract_urls_from_text(text: str) -> List[str]:
    """Extract URLs from plain text."""
    return _URL_RE.findall(text)


def _parse_table_rows(table_block_id: str) -> List[Dict[str, str]]:
    """Parse a Notion table into list of dicts (header row = keys)."""
    rows = _get_block_children(table_block_id)
    if not rows:
        return []
    
    parsed = []
    header = None
    for row in rows:
        if row["type"] != "table_row":
            continue
        cells = row["table_row"]["cells"]
        cell_texts = [_extract_text(cell) for cell in cells]
        
        if header is None:
            header = cell_texts
            continue
        
        entry = {}
        for i, key in enumerate(header):
            entry[key] = cell_texts[i] if i < len(cell_texts) else ""
        parsed.append(entry)
    return parsed


def _crawl_block_recursive(
    block_id: str, team: str, category: str, depth: int = 0
) -> List[Dict]:
    """Recursively crawl Notion blocks and extract resources.
    
    Returns flat list of {team, category, name, resource_type, url, description}.
    """
    if depth > 5:  # Safety: max recursion depth
        return []
    
    resources = []
    children = _get_block_children(block_id)
    
    for child in children:
        btype = child["type"]
        bid = child["id"]
        
        if btype == "table":
            # Parse table rows — each row becomes a resource
            rows = _parse_table_rows(bid)
            for row in rows:
                # Detect name/url from common column patterns
                name = (
                    row.get("시트명") or row.get("name") or row.get("이름")
                    or row.get("시트") or row.get("제목") or row.get("카테고리")
                    or ""
                )
                url = (
                    row.get("URL") or row.get("url") or row.get("링크")
                    or row.get("링크 ") or row.get("Link") or ""
                )
                desc = row.get("비고") or row.get("description") or row.get("설명") or ""
                sub_cat = row.get("파트") or row.get("카테고리") or category
                
                # Skip empty rows
                if not name and not url:
                    continue
                # Extract URL from text if not in dedicated column
                if not url:
                    urls = _extract_urls_from_text(" ".join(row.values()))
                    url = urls[0] if urls else ""
                
                resources.append({
                    "team": team,
                    "category": sub_cat or category,
                    "name": name,
                    "resource_type": _detect_type(url),
                    "url": url,
                    "description": desc,
                })
        
        elif btype == "toggle":
            # Toggle = sub-category, recurse into it
            toggle_title = _extract_text(child["toggle"].get("rich_text", []))
            if not toggle_title:
                continue
            sub_category = toggle_title if category else toggle_title
            resources.extend(
                _crawl_block_recursive(bid, team, sub_category, depth + 1)
            )
        
        elif btype == "bulleted_list_item":
            # Bullet item might be a sub-category with children, or a leaf resource
            bullet_text = _extract_text(child["bulleted_list_item"].get("rich_text", []))
            if not bullet_text:
                continue
            
            # Check if it has children (sub-category) or is a leaf
            sub_children = _get_block_children(bid)
            if sub_children:
                # Has children → treat as sub-category
                resources.extend(
                    _crawl_block_recursive(bid, team, bullet_text, depth + 1)
                )
            else:
                # Leaf bullet → extract URL if present
                urls = _extract_urls_from_text(bullet_text)
                if urls:
                    resources.append({
                        "team": team,
                        "category": category,
                        "name": bullet_text.split("http")[0].strip(),
                        "resource_type": _detect_type(urls[0]),
                        "url": urls[0],
                        "description": "",
                    })
        
        elif btype == "paragraph":
            # Paragraph might contain a URL link
            para_text = _extract_text(child["paragraph"].get("rich_text", []))
            if not para_text:
                continue
            urls = _extract_urls_from_text(para_text)
            if urls:
                # Extract name from text before URL
                name_part = para_text.split("http")[0].strip()
                if not name_part:
                    name_part = para_text[:80]
                resources.append({
                    "team": team,
                    "category": category,
                    "name": name_part,
                    "resource_type": _detect_type(urls[0]),
                    "url": urls[0],
                    "description": "",
                })
    
    return resources


def crawl_all_teams() -> List[Dict]:
    """Crawl all teams from DB HUB and return flat resource list."""
    team_blocks = _get_block_children(TEAM_DATA_TOGGLE_ID)
    all_resources = []
    
    for block in team_blocks:
        if block["type"] != "toggle":
            continue
        team_name = _extract_text(block["toggle"].get("rich_text", []))
        
        # Skip teams marked with X
        if team_name in SKIP_TEAMS:
            logger.info("team_skipped", team=team_name)
            continue
        # Skip if team name contains "노션x" or "노션 x"
        if "노션x" in team_name.lower() or "노션 x" in team_name.lower():
            logger.info("team_skipped_notion_x", team=team_name)
            continue
        
        logger.info("crawling_team", team=team_name)
        team_resources = _crawl_block_recursive(block["id"], team_name, "")
        all_resources.extend(team_resources)
        logger.info("team_crawled", team=team_name, count=len(team_resources))
    
    return all_resources


def save_to_mariadb(resources: List[Dict]) -> int:
    """Save crawled resources to MariaDB (full replace)."""
    from app.db.mariadb import execute, ensure_team_resources_table
    
    ensure_team_resources_table()
    
    # Full replace: delete all, then insert
    execute("DELETE FROM team_resources")
    
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    count = 0
    for r in resources:
        if not r.get("name") and not r.get("url"):
            continue
        execute(
            "INSERT INTO team_resources (team, category, name, resource_type, url, description, synced_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (r["team"], r["category"], r["name"], r["resource_type"],
             r["url"], r["description"], now),
        )
        count += 1
    
    logger.info("team_resources_saved", count=count)
    return count


def sync(dry_run: bool = False) -> int:
    """Full sync: crawl Notion → save to MariaDB."""
    resources = crawl_all_teams()
    
    if dry_run:
        print(f"\n=== DRY RUN: {len(resources)} resources found ===\n")
        for r in resources:
            print(f"  [{r['team']}] {r['category']} | {r['name']} | {r['resource_type']} | {r['url'][:60] if r['url'] else 'N/A'}")
        return len(resources)
    
    return save_to_mariadb(resources)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync team resources from Notion DB HUB")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no DB writes")
    args = parser.parse_args()
    
    count = sync(dry_run=args.dry_run)
    print(f"\nSync complete: {count} resources")
```

- [ ] **Step 2: dry-run 테스트**

Run: `python -X utf8 scripts/sync_team_resources.py --dry-run`

Expected: 팀별 자료 목록 출력 (JBT 47건, BCM N건, GM EAST N건 등)

- [ ] **Step 3: 실제 동기화 테스트**

Run: `python -X utf8 scripts/sync_team_resources.py`

Expected: `Sync complete: N resources`

- [ ] **Step 4: DB 확인**

Run: `python -X utf8 -c "from app.db.mariadb import fetch_all; rows=fetch_all('SELECT team, COUNT(*) as cnt FROM team_resources GROUP BY team'); print(rows)"`

Expected: 팀별 건수 출력

- [ ] **Step 5: Commit**

```bash
git add scripts/sync_team_resources.py
git commit -m "feat: Notion DB HUB 크롤러 — 팀별 자료 → MariaDB 동기화"
```

---

### Task 3: 팀별 자료 검색 에이전트

**Files:**
- Create: `app/agents/team_agent.py`

- [ ] **Step 1: TeamAgent 구현 (CS Agent 패턴)**

`app/agents/team_agent.py` — 전체 코드:

```python
"""Team Resource Agent — CS Agent 패턴으로 팀별 자료 검색.

DB HUB에서 동기화된 팀별 자료(Google Sheets, Notion 페이지 등)를
키워드 매칭으로 검색하여 링크와 설명을 반환.
"""
import re
from typing import Dict, List, Optional

import structlog

from app.core.llm import get_flash_client, get_llm_client
from app.core.prompt_fragments import LANGUAGE_DETECTION_RULE
from app.db.mariadb import fetch_all

logger = structlog.get_logger(__name__)

# Module-level cache
_resource_cache: List[Dict] = []
_cache_loaded: bool = False
_last_sync: str = ""


async def warmup() -> int:
    """Load team resources from MariaDB into memory cache."""
    import asyncio
    global _resource_cache, _cache_loaded, _last_sync

    def _load():
        rows = fetch_all(
            "SELECT team, category, name, resource_type, url, description, synced_at "
            "FROM team_resources ORDER BY team, category, name"
        )
        return rows

    rows = await asyncio.to_thread(_load)
    _resource_cache = rows
    _cache_loaded = True
    if rows:
        _last_sync = str(rows[0].get("synced_at", ""))
    logger.info("team_resources_warmup", count=len(rows))
    return len(rows)


def _tokenize(text: str) -> set:
    """Tokenize text into Korean/English/numeric tokens."""
    return set(re.findall(r'[가-힣a-zA-Z0-9]+', text.lower()))


def _word_overlap_score(query_tokens: set, target_text: str) -> float:
    """Calculate word overlap score between query tokens and target text."""
    if not target_text:
        return 0.0
    target_tokens = _tokenize(target_text)
    if not target_tokens:
        return 0.0
    overlap = query_tokens & target_tokens
    return len(overlap) / max(len(query_tokens), 1)


# Team name aliases for flexible matching
_TEAM_ALIASES = {
    "일본": "JBT", "일본사업": "JBT", "jbt": "JBT",
    "bcm": "BCM", "브랜드커뮤니케이션": "BCM", "브커": "BCM",
    "이스트": "[GM]EAST", "east": "[GM]EAST", "동남아": "[GM]EAST",
    "east1": "[GM]EAST", "east2": "[GM]EAST",
    "웨스트": "[GM]WEST", "west": "[GM]WEST",
    "it": "IT", "아이티": "IT",
    "크레이버": "Craver", "craver": "Craver",
    "cs": "CS", "bp": "CS",
}


def search_resources(query: str, top_k: int = 10) -> List[Dict]:
    """Search team resources by keyword matching."""
    if not _cache_loaded or not _resource_cache:
        return []

    q_tokens = _tokenize(query)
    q_lower = query.lower()

    scored = []
    for r in _resource_cache:
        score = 0.0

        # Team name matching (+3.0)
        team_lower = r["team"].lower()
        if team_lower in q_lower:
            score += 3.0
        # Alias matching
        for alias, canonical in _TEAM_ALIASES.items():
            if alias in q_lower and r["team"] == canonical:
                score += 3.0
                break

        # Category matching (+2.0)
        if r["category"] and r["category"].lower() in q_lower:
            score += 2.0

        # Name matching (+2.0 * overlap)
        name_score = _word_overlap_score(q_tokens, r["name"])
        score += name_score * 2.0

        # Description matching (+0.5 * overlap)
        desc_score = _word_overlap_score(q_tokens, r["description"])
        score += desc_score * 0.5

        if score > 0:
            scored.append((score, r))

    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored[:top_k]]


def _format_resource_context(matched: List[Dict]) -> str:
    """Format matched resources for LLM context."""
    if not matched:
        return "검색 결과가 없습니다."

    lines = []
    for i, r in enumerate(matched, 1):
        meta = f"[{r['team']}]"
        if r["category"]:
            meta += f" {r['category']}"
        rtype = {"google_sheet": "📊 Google Sheet", "notion": "📋 Notion",
                 "google_drive": "📁 Google Drive", "other": "🔗 기타"}.get(r["resource_type"], "🔗")
        lines.append(f"{i}. {meta} | {rtype}\n   이름: {r['name']}\n   링크: {r['url'] or 'N/A'}")
        if r["description"]:
            lines.append(f"   비고: {r['description']}")
    return "\n".join(lines)


async def run(query: str, model_type: str = "gemini") -> str:
    """Search team resources and generate answer."""
    if not _cache_loaded:
        await warmup()

    matched = search_resources(query, top_k=8)
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
- 매칭된 자료의 이름과 링크를 보기 쉽게 정리하세요
- 링크는 클릭 가능하도록 마크다운 형식으로: [시트명](URL)
- 팀/카테고리별로 그룹화하여 보여주세요
- 매칭 결과가 없으면 "해당 자료를 찾을 수 없습니다" 안내
- 답변 마지막에 출처 표시:
  ---
  *팀별 자료 검색 · 마지막 동기화: {_last_sync}*

## 후속 질문 제안
> 💡 **이런 것도 물어보세요**
> - [관련 팀/카테고리의 다른 자료]
> - [같은 팀의 다른 시트]
"""

    try:
        answer = llm.generate(prompt, temperature=0.3, max_output_tokens=2048)
        return answer
    except Exception as e:
        logger.error("team_agent_failed", error=str(e))
        return f"팀별 자료 검색 중 오류가 발생했습니다: {e}"
```

- [ ] **Step 2: Commit**

```bash
git add app/agents/team_agent.py
git commit -m "feat: TeamAgent — 팀별 자료 검색 에이전트 (CS Agent 패턴)"
```

---

### Task 4: Orchestrator 라우팅 추가

**Files:**
- Modify: `app/agents/orchestrator.py` — `_SOURCE_ROUTE_MAP`, `_CS_KEYWORDS` 근처, `_handle_cs` 근처

- [ ] **Step 1: SOURCE_ROUTE_MAP에 팀별 자료 추가**

`app/agents/orchestrator.py` line 104-115, `_SOURCE_ROUTE_MAP` dict에 추가:

```python
# 기존 항목 뒤에 추가
"BP (CS Q&A)": "team",  # CS Q&A → BP로 이름 변경, team 라우트로 통합
"팀별 자료": "team",
```

기존 `"CS Q&A": "cs"` 항목은 유지 (하위 호환).

- [ ] **Step 2: _TEAM_KEYWORDS 리스트 추가**

`_CS_KEYWORDS` 리스트 근처에 추가:

```python
_TEAM_KEYWORDS = [
    "자료", "시트", "스프레드시트", "링크", "문서 링크",
    "어디있어", "어디 있어", "찾아줘", "위치",
    "jbt", "bcm", "east", "west", "이스트", "웨스트",
    "bea", "bxm", "플래그십",
    "예산 시트", "pr 시트", "운영 시트", "대시보드",
    "팀 자료", "팀별 자료", "db hub", "데이터 허브",
]
```

- [ ] **Step 3: _keyword_classify에 team 라우팅 추가**

CS 체크 직전 (line 745 근처)에 추가:

```python
# Team resource check — team data lookups
if any(kw in q for kw in self._TEAM_KEYWORDS):
    return "team"
```

- [ ] **Step 4: _handle_team 메서드 추가**

`_handle_cs` 메서드 근처에 추가:

```python
async def _handle_team(
    self,
    query: str,
    messages: List[Dict[str, str]],
    conversation_context: str,
    model_type: str,
    user_email: str = "",
) -> dict:
    """Team Resource Agent — 팀별 자료 검색."""
    from app.agents.team_agent import run as run_team_agent

    contextualized_query = query
    if conversation_context:
        contextualized_query = f"[이전 대화]\n{conversation_context}\n\n[현재 질문]\n{query}"
    try:
        result = await run_team_agent(contextualized_query, model_type=model_type)
        return {"source": "team", "answer": result}
    except Exception as e:
        logger.error("orchestrator_team_failed", error=str(e))
        return {"source": "team", "answer": f"팀별 자료 검색 중 오류가 발생했습니다: {str(e)}"}
```

- [ ] **Step 5: handlers dict에 team 추가**

`route_and_stream` 메서드 내 `handlers = {` dict (line 386 근처)에 추가:

```python
"team": self._handle_team,
```

- [ ] **Step 6: _allowed_routes에 team 추가**

`_allowed_routes` 메서드 (line 117)에서 `"direct"`와 함께 `"team"`도 기본 허용하도록:

```python
routes = {"direct", "team"}  # direct + team은 항상 허용
```

- [ ] **Step 7: Commit**

```bash
git add app/agents/orchestrator.py
git commit -m "feat: orchestrator에 team 라우트 + 키워드 + _handle_team 추가"
```

---

### Task 5: Safety 서비스 등록 + 스케줄러

**Files:**
- Modify: `app/core/safety.py` — `get_safety_status()` 내 서비스 등록
- Modify: `app/main.py` — warmup + APScheduler
- Modify: `requirements.txt` — apscheduler 추가

- [ ] **Step 1: requirements.txt에 apscheduler 추가**

```
apscheduler>=3.10.0
```

- [ ] **Step 2: safety.py에 team_resources 서비스 등록**

`get_safety_status()` 함수 내, CS Q&A 등록 코드 근처에 추가:

```python
# Team Resources (DB HUB)
try:
    from app.agents.team_agent import _resource_cache, _cache_loaded, _last_sync
    if _cache_loaded:
        services["팀별 자료"] = {"status": "ok", "detail": f"{len(_resource_cache)} entries, sync: {_last_sync[:16]}"}
    else:
        services["팀별 자료"] = {"status": "error", "detail": "loading"}
except Exception:
    services["팀별 자료"] = {"status": "ok", "detail": "not loaded"}
```

- [ ] **Step 3: main.py에 warmup + 스케줄러 추가**

`app/main.py`의 lifespan 함수 내, CS warmup 근처에 추가:

```python
# Team resources warmup
asyncio.create_task(_warmup_team_resources())

# APScheduler: daily 01:00 sync
from apscheduler.schedulers.asyncio import AsyncIOScheduler
scheduler = AsyncIOScheduler()
scheduler.add_job(_sync_team_resources_job, "cron", hour=1, minute=0, id="team_sync_daily")
scheduler.start()
logger.info("scheduler_started", jobs=["team_sync_daily_01:00"])
```

그리고 warmup/sync 함수:

```python
async def _warmup_team_resources():
    """Pre-load team resources from MariaDB at startup."""
    try:
        from app.agents.team_agent import warmup
        count = await warmup()
        logger.info("team_resources_warmup_done", count=count)
    except Exception as e:
        logger.warning("team_resources_warmup_failed", error=str(e))


async def _sync_team_resources_job():
    """Daily 01:00 cron job: Notion → MariaDB sync."""
    try:
        import asyncio
        from scripts.sync_team_resources import sync
        count = await asyncio.to_thread(sync, dry_run=False)
        # Reload cache after sync
        from app.agents.team_agent import warmup
        await warmup()
        logger.info("team_resources_daily_sync_done", count=count)
    except Exception as e:
        logger.error("team_resources_daily_sync_failed", error=str(e))
```

- [ ] **Step 4: pip install**

Run: `pip install apscheduler>=3.10.0`

- [ ] **Step 5: Commit**

```bash
git add app/core/safety.py app/main.py requirements.txt
git commit -m "feat: 팀별 자료 Safety 등록 + APScheduler 매일 01:00 동기화"
```

---

### Task 6: System Status 그룹화 UI

**Files:**
- Modify: `app/frontend/chat.js` — `DATA_SOURCE_KEYS`, `SERVICE_ICONS`, `pollSystemStatus()`
- Modify: `app/static/style.css` — 그룹 접기/펼치기 스타일
- Modify: `app/frontend/chat.html` — 캐시 버스팅

- [ ] **Step 1: DATA_SOURCE_KEYS를 그룹 구조로 변경**

`app/frontend/chat.js` line 196-204, 기존 플랫 배열을 그룹 객체로 변경:

```javascript
// ===== Data Source Filter =====
// Grouped data sources — shown with collapsible groups in System Status
var SOURCE_GROUPS = [
    {
        id: "sales", label: "매출 데이터", emoji: "📊",
        keys: ["BigQuery 매출", "BigQuery 제품", "BQ Shopify", "BQ 플랫폼"]
    },
    {
        id: "marketing", label: "마케팅 데이터", emoji: "📈",
        keys: ["BQ 광고데이터", "BQ 마케팅비용", "BQ 인플루언서", "BQ 아마존검색",
               "BQ 아마존리뷰", "BQ 큐텐리뷰", "BQ 쇼피리뷰", "BQ 스마트스토어", "BQ 메타광고"]
    },
    {
        id: "team", label: "팀별 자료", emoji: "🏢",
        keys: ["팀별 자료", "BP (CS Q&A)"]
    },
    {
        id: "tools", label: "업무 도구", emoji: "📧",
        keys: ["Notion 문서", "Google Workspace"]
    },
];
// Flat list for backward compatibility
var DATA_SOURCE_KEYS = [];
SOURCE_GROUPS.forEach(function(g) { g.keys.forEach(function(k) { DATA_SOURCE_KEYS.push(k); }); });
```

- [ ] **Step 2: SOURCE_ROUTE_MAP에 새 항목 추가**

```javascript
var SOURCE_ROUTE_MAP = {
    // 기존 항목 유지...
    "BP (CS Q&A)": "cs",
    "팀별 자료": "team",
};
```

- [ ] **Step 3: SERVICE_ICONS에 새 항목 추가**

```javascript
"BP (CS Q&A)": {
    label: "BP",
    svg: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>'
},
"팀별 자료": {
    label: "팀자료",
    svg: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>'
},
```

- [ ] **Step 4: pollSystemStatus()를 그룹 렌더링으로 변경**

`pollSystemStatus()` 함수 (line 2212)의 HTML 빌드 부분을 그룹 기반으로 교체:

```javascript
function pollSystemStatus() {
    fetch("/safety/status")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data || !data.services) return;
        var container = document.getElementById("status-items");
        var issues = [];
        var html = "";

        // Toolbar
        html += '<div class="source-select-toolbar">' +
          '<button class="source-btn-all" id="source-select-all">전체 선택</button>' +
          '<button class="source-btn-none" id="source-deselect-all">전체 해제</button>' +
          '<span class="source-count-label" id="source-count-label">' + enabledSources.length + '/' + DATA_SOURCE_KEYS.length + '</span>' +
          '</div>';

        // Render groups
        SOURCE_GROUPS.forEach(function(group) {
            var groupEnabled = 0;
            var groupItems = "";

            group.keys.forEach(function(name) {
                var svc = data.services[name] || { status: "ok", detail: "" };
                var st = svc.status || "ok";
                var info = SERVICE_ICONS[name] || { label: name, svg: "" };
                var isQueryable = DATA_SOURCE_KEYS.indexOf(name) >= 0;
                var isChecked = enabledSources.indexOf(name) >= 0;
                if (isChecked) groupEnabled++;

                if (st !== "ok") issues.push(info.label + ": " + (st === "updating" ? "업데이트 중" : "오류"));

                var checkboxHtml = isQueryable
                    ? '<label class="status-checkbox-label"><input type="checkbox" class="status-source-cb" data-source="' + name + '"' + (isChecked ? ' checked' : '') + '></label>'
                    : '';

                groupItems +=
                    '<div class="status-item' + (st !== "ok" ? " status-alert" : "") + '">' +
                    '<div class="status-item-row">' +
                    checkboxHtml +
                    '<span class="status-dot' + (st !== "ok" ? " error" : "") + '"></span>' +
                    '<span class="status-icon">' + info.svg + '</span>' +
                    '<span class="status-name">' + info.label + '</span>' +
                    '<span class="status-label' + (st !== "ok" ? (st === "updating" ? " updating" : " error") : "") + '">' +
                    ({"ok":"정상","updating":"업데이트 중","error":"오류"}[st] || st) + '</span>' +
                    (svc.detail ? '<span class="status-detail">' + svc.detail + '</span>' : '') +
                    '</div></div>';
            });

            var collapsed = localStorage.getItem("status_group_" + group.id) === "collapsed";
            html +=
                '<div class="status-group">' +
                '<div class="status-group-header" data-group="' + group.id + '">' +
                '<span class="status-group-toggle">' + (collapsed ? '▶' : '▼') + '</span>' +
                '<span>' + group.emoji + ' ' + group.label + '</span>' +
                '<span class="status-group-count">' + groupEnabled + '/' + group.keys.length + '</span>' +
                '</div>' +
                '<div class="status-group-body' + (collapsed ? ' collapsed' : '') + '">' +
                groupItems +
                '</div></div>';
        });

        container.innerHTML = html;

        // Attach listeners
        document.getElementById("source-select-all").addEventListener("click", function() {
            enabledSources = DATA_SOURCE_KEYS.slice();
            saveEnabledSources(); pollSystemStatus(); updateSourceFilterBadge();
        });
        document.getElementById("source-deselect-all").addEventListener("click", function() {
            enabledSources = [];
            saveEnabledSources(); pollSystemStatus(); updateSourceFilterBadge();
        });
        container.querySelectorAll(".status-source-cb").forEach(function(cb) {
            cb.addEventListener("change", function() {
                toggleSource(this.getAttribute("data-source"));
                document.getElementById("source-count-label").textContent = enabledSources.length + '/' + DATA_SOURCE_KEYS.length;
                updateSourceFilterBadge();
            });
        });
        // Group toggle (collapse/expand)
        container.querySelectorAll(".status-group-header").forEach(function(hdr) {
            hdr.addEventListener("click", function() {
                var gid = this.getAttribute("data-group");
                var body = this.nextElementSibling;
                var arrow = this.querySelector(".status-group-toggle");
                if (body.classList.contains("collapsed")) {
                    body.classList.remove("collapsed");
                    arrow.textContent = "▼";
                    localStorage.removeItem("status_group_" + gid);
                } else {
                    body.classList.add("collapsed");
                    arrow.textContent = "▶";
                    localStorage.setItem("status_group_" + gid, "collapsed");
                }
            });
        });

        // Sidebar inline indicator
        var inlineEl = document.getElementById("sidebar-status-inline");
        if (inlineEl) {
            inlineEl.innerHTML = issues.length > 0
                ? '<span class="status-inline-warn">⚠ ' + issues.length + '</span>'
                : '';
        }
    });
}
```

- [ ] **Step 5: CSS 그룹 스타일 추가**

`app/static/style.css`에 추가:

```css
/* System Status Groups */
.status-group { margin-bottom: 8px; }
.status-group-header {
    display: flex; align-items: center; gap: 8px;
    padding: 8px 12px; cursor: pointer;
    background: var(--bg-hover); border-radius: 8px;
    font-size: var(--text-sm); font-weight: 600;
    user-select: none; transition: background 0.15s;
}
.status-group-header:hover { background: var(--bg-sidebar-hover); }
.status-group-toggle { font-size: 10px; color: var(--text-tertiary); width: 12px; }
.status-group-count {
    margin-left: auto; font-size: var(--text-xs);
    color: var(--text-tertiary); font-weight: 400;
}
.status-group-body { padding-left: 8px; }
.status-group-body.collapsed { display: none; }
.status-detail {
    font-size: var(--text-2xs); color: var(--text-tertiary);
    margin-left: auto; max-width: 120px; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap;
}
```

- [ ] **Step 6: chat.html 캐시 버스팅**

`app/frontend/chat.html`:
- `style.css?v=116` → `style.css?v=117`
- `chat.js?v=127` → `chat.js?v=128`

- [ ] **Step 7: Commit**

```bash
git add app/frontend/chat.js app/static/style.css app/frontend/chat.html
git commit -m "feat: System Status 그룹화 UI — 매출/마케팅/팀별/업무도구 4그룹 + 접기/펼치기"
```

---

### Task 7: 통합 테스트 + 정리

**Files:**
- All modified files

- [ ] **Step 1: 서버 시작 테스트**

Run: `python -X utf8 -m uvicorn app.main:app --host 0.0.0.0 --port 3001 --reload`

확인사항:
- `team_resources_warmup_done` 로그 출력
- `scheduler_started` 로그 출력
- `/safety/status` 응답에 `팀별 자료` 서비스 포함

- [ ] **Step 2: 팀 자료 검색 테스트**

브라우저에서 `http://localhost:3001` 접속 후:
- "JBT PR 시트 어디있어?" → 링크 반환 확인
- "BCM 예산 관리 시트" → 링크 반환 확인
- System Status drawer 열어서 그룹 확인

- [ ] **Step 3: Commit (최종)**

```bash
git add -A
git commit -m "feat: DB HUB 팀별 자료 시스템 완성 — Notion 동기화 + 검색 + 그룹 UI"
```
