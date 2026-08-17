"""Sync team resources from Notion DB HUB to MariaDB (tree structure).

Stores parent-child tree with node_type, depth, and notion_block_id.

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

TOKEN = os.getenv("NOTION_MCP_TOKEN")
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}
CLIENT = httpx.Client(timeout=30)

# 팀 토글들이 달려 있는 부모. 예전에는 별도 토글 블록이었는데 페이지 구조가 바뀌면서
# 그 블록이 빈 문단으로 남았고(자식 없음), 2026-07-30 부터 매일 0건으로 돌고 있었다.
# 지금은 팀 토글이 HUB 페이지의 직계 자식이라 페이지 ID 를 그대로 쓴다.
# 출처: [DB] 각 팀 노션 페이지 HUB
TEAM_DATA_TOGGLE_ID = "2e12b428-3b00-8011-ae32-e39bf73b7f7b"

SKIP_TEAMS = {"유통1(노션x)", "유통2(노션x)", ""}

# HUB 페이지에는 팀 토글 말고도 자동 생성된 로그 토글이 잔뜩 쌓인다
# ("🤖 AI 학습 현황 — ..." 80여 개). 팀으로 오인해 크롤링하지 않도록 걸러낸다.
_NON_TEAM_TOGGLE_MARKERS = ("🤖", "AI 학습 현황")

_URL_RE = re.compile(r'https?://[^\s<>"]+')
_GSHEET_RE = re.compile(r'docs\.google\.com/spreadsheets')
_GDRIVE_RE = re.compile(r'drive\.google\.com')
_NOTION_RE = re.compile(r'notion\.so/')


def _detect_type(url: str) -> str:
    if not url:
        return "other"
    if _GSHEET_RE.search(url):
        return "google_sheet"
    if _GDRIVE_RE.search(url):
        return "google_drive"
    if _NOTION_RE.search(url):
        return "notion"
    return "other"


def _detect_node_type(url: str) -> str:
    if not url:
        return "text"
    if _GSHEET_RE.search(url):
        return "sheet"
    if _NOTION_RE.search(url):
        return "page"
    return "sheet"  # Google Drive docs etc.


def _get_block_children(block_id: str) -> list:
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
    return "".join(t.get("plain_text", "") for t in rich_text_list).strip()


def _extract_href(rich_text_list: list) -> str:
    for t in rich_text_list:
        href = t.get("href")
        if href:
            return href
        mention = t.get("mention", {})
        mtype = mention.get("type", "")
        if mtype == "page":
            pid = mention["page"]["id"]
            return f"https://www.notion.so/{pid.replace('-', '')}"
        if mtype == "database":
            did = mention["database"]["id"]
            return f"https://www.notion.so/{did.replace('-', '')}"
    return ""


def _extract_block_content(block: dict) -> tuple:
    btype = block["type"]
    rt = []
    if btype in block and isinstance(block[btype], dict):
        rt = block[btype].get("rich_text", [])
    text = _extract_text(rt)
    href = _extract_href(rt)
    if not href:
        urls = _URL_RE.findall(text)
        if urls:
            href = urls[0]
    return text, href


def _query_database(db_id: str, max_records: int = 30) -> list:
    """Query a Notion database and return records (pages)."""
    all_records = []
    body: Dict = {"page_size": min(max_records, 100)}
    while len(all_records) < max_records:
        resp = CLIENT.post(f"https://api.notion.com/v1/databases/{db_id}/query", headers=HEADERS, json=body)
        if resp.status_code != 200:
            break
        data = resp.json()
        all_records.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        body["start_cursor"] = data["next_cursor"]
    return all_records[:max_records]


def _get_record_title(record: dict) -> str:
    """Extract title from a Notion database record's properties."""
    props = record.get("properties", {})
    for key, val in props.items():
        if val.get("type") == "title":
            return "".join(t.get("plain_text", "") for t in val.get("title", [])).strip()
    return ""


def _parse_table_rows(table_block_id: str) -> List[Dict[str, str]]:
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


# ============================================================
# Tree-based crawler — inserts nodes with parent_id
# ============================================================

_NOTION_PAGE_RE = re.compile(r'notion\.so/(?:skin1004/)?(?:[^/]*-)?([0-9a-f]{32})')

def _extract_notion_page_id(url: str) -> Optional[str]:
    """Extract Notion page/block ID from URL, format as UUID."""
    m = _NOTION_PAGE_RE.search(url)
    if not m:
        return None
    raw = m.group(1)
    # Format as UUID: 8-4-4-4-12
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"

# Track crawled page IDs to avoid infinite loops
_crawled_pages: set = set()

# Accumulate nodes in-memory, insert to DB after crawl
_nodes: List[Dict] = []
_node_counter = 0


def _add_node(parent_id: Optional[int], team: str, node_type: str, name: str,
              url: str = "", desc: str = "", depth: int = 0,
              block_id: str = "", sort_order: int = 0) -> int:
    """Add a node to the in-memory tree. Returns a temporary ID."""
    global _node_counter
    _node_counter += 1
    _nodes.append({
        "tmp_id": _node_counter,
        "parent_tmp_id": parent_id,
        "team": team,
        "node_type": node_type,
        "name": name.strip()[:500],
        "url": url.strip() if url else "",
        "description": desc.strip() if desc else "",
        "resource_type": _detect_type(url),
        "depth": depth,
        "sort_order": sort_order,
        "notion_block_id": block_id[:36] if block_id else "",
    })
    return _node_counter


MAX_DEPTH = 6           # Max tree depth
MAX_CHILDREN = 50       # Max children per node
MAX_PAGE_FOLLOWS = 8    # Max Notion pages/DBs to follow INTO per team
_page_follow_count: Dict[str, int] = {}  # team → follow count

def _can_follow_page(team: str) -> bool:
    count = _page_follow_count.get(team, 0)
    if count >= MAX_PAGE_FOLLOWS:
        return False
    _page_follow_count[team] = count + 1
    return True

def _crawl_recursive(block_id: str, parent_tmp_id: int, team: str, depth: int = 1):
    if depth > MAX_DEPTH:
        return
    children = _get_block_children(block_id)
    if len(children) > MAX_CHILDREN:
        children = children[:MAX_CHILDREN]
    sort = 0
    heading_parent = parent_tmp_id

    for child in children:
        btype = child["type"]
        bid = child["id"]
        has_ch = child.get("has_children", False)
        sort += 1

        # --- Table: parse rows as leaf nodes ---
        if btype == "table":
            rows = _parse_table_rows(bid)
            for row in rows:
                name = (row.get("시트명") or row.get("name") or row.get("이름")
                        or row.get("시트") or row.get("제목") or "")
                url = (row.get("URL") or row.get("url") or row.get("링크")
                       or row.get("링크 ") or row.get("Link") or "")
                desc = row.get("비고") or row.get("description") or row.get("설명") or ""
                if not name and not url:
                    continue
                if not url:
                    urls = _URL_RE.findall(" ".join(row.values()))
                    url = urls[0] if urls else ""
                nt = _detect_node_type(url) if url else "text"
                node_id = _add_node(heading_parent, team, nt, name, url, desc, depth, bid, sort)
                # Follow into Notion pages to get deeper content
                if nt == "page" and url and _can_follow_page(team):
                    page_id = _extract_notion_page_id(url)
                    if page_id and page_id not in _crawled_pages:
                        _crawled_pages.add(page_id)
                        logger.info("following_notion_page", team=team, name=name[:40], depth=depth)
                        _crawl_recursive(page_id, node_id, team, depth + 1)
            continue

        # --- Toggle: create folder, recurse ---
        if btype == "toggle":
            title = _extract_text(child["toggle"].get("rich_text", []))
            if not title:
                continue
            folder_id = _add_node(parent_tmp_id, team, "folder", title, depth=depth, block_id=bid, sort_order=sort)
            _crawl_recursive(bid, folder_id, team, depth + 1)
            continue

        # --- Child page ---
        if btype == "child_page":
            title = child.get("child_page", {}).get("title", "")
            page_url = f"https://www.notion.so/{bid.replace('-', '')}"
            if title and bid not in _crawled_pages:
                _crawled_pages.add(bid)
                node_id = _add_node(heading_parent, team, "page", title, page_url, depth=depth, block_id=bid, sort_order=sort)
                if has_ch:
                    _crawl_recursive(bid, node_id, team, depth + 1)
            continue

        # --- Child database: query records and crawl each ---
        if btype == "child_database":
            title = child.get("child_database", {}).get("title", "")
            db_url = f"https://www.notion.so/{bid.replace('-', '')}"
            db_node_id = _add_node(heading_parent, team, "database", title or "database", db_url, depth=depth, block_id=bid, sort_order=sort)
            # Query database records
            if _can_follow_page(team):
                try:
                    records = _query_database(bid)
                    logger.info("querying_database", team=team, title=title[:30], records=len(records))
                    for rec in records:
                        rec_id = rec["id"]
                        rec_title = _get_record_title(rec)
                        rec_url = f"https://www.notion.so/{rec_id.replace('-', '')}"
                        if rec_id not in _crawled_pages:
                            _crawled_pages.add(rec_id)
                            rec_node = _add_node(db_node_id, team, "page", rec_title or "Untitled", rec_url, depth=depth+1, block_id=rec_id, sort_order=sort)
                            # Crawl inside each record page
                            _crawl_recursive(rec_id, rec_node, team, depth + 2)
                except Exception as e:
                    logger.warning("database_query_failed", db_id=bid, error=str(e))
            continue

        # --- Bookmark / Embed ---
        if btype == "bookmark":
            url = child.get("bookmark", {}).get("url", "")
            caption = _extract_text(child.get("bookmark", {}).get("caption", []))
            if url:
                nt = _detect_node_type(url)
                _add_node(heading_parent, team, nt, caption or url[:80], url, depth=depth, block_id=bid, sort_order=sort)
            continue
        if btype == "embed":
            url = child.get("embed", {}).get("url", "")
            if url:
                _add_node(heading_parent, team, "sheet", url[:80], url, depth=depth, block_id=bid, sort_order=sort)
            continue

        # --- Heading: create folder, subsequent siblings go under it ---
        if btype in ("heading_1", "heading_2", "heading_3"):
            text = _extract_text(child[btype].get("rich_text", []))
            if text:
                heading_parent = _add_node(parent_tmp_id, team, "folder", text, depth=depth, block_id=bid, sort_order=sort)
            continue

        # --- Text blocks: paragraph, bulleted_list_item, numbered_list_item ---
        text, href = _extract_block_content(child)
        if href:
            name_part = text.split("http")[0].strip() if "http" in text else text
            nt = _detect_node_type(href)
            node_id = _add_node(heading_parent, team, nt, name_part or href[:80], href, depth=depth, block_id=bid, sort_order=sort)
            # Follow into Notion pages
            if nt == "page" and href and _can_follow_page(team):
                page_id = _extract_notion_page_id(href)
                if page_id and page_id not in _crawled_pages:
                    _crawled_pages.add(page_id)
                    logger.info("following_notion_page", team=team, name=(name_part or "")[:40], depth=depth)
                    _crawl_recursive(page_id, node_id, team, depth + 1)
        elif text and len(text) >= 5:
            _add_node(heading_parent, team, "text", text[:120], desc=text, depth=depth, block_id=bid, sort_order=sort)

        # Recurse children (bulleted lists with sub-items)
        if has_ch:
            if btype in ("bulleted_list_item", "numbered_list_item") and text and not href:
                folder_id = _add_node(parent_tmp_id, team, "folder", text, depth=depth, block_id=bid, sort_order=sort)
                _crawl_recursive(bid, folder_id, team, depth + 1)
            else:
                _crawl_recursive(bid, heading_parent, team, depth + 1)


def crawl_all_teams(only_teams: Optional[set] = None):
    global _nodes, _node_counter, _crawled_pages, _page_follow_count
    _nodes = []
    _node_counter = 0
    _crawled_pages = set()
    _page_follow_count = {}

    team_blocks = _get_block_children(TEAM_DATA_TOGGLE_ID)
    for block in team_blocks:
        if block["type"] != "toggle":
            continue
        team_name = _extract_text(block["toggle"].get("rich_text", []))
        team_name = team_name.replace("[GM]", "GM ").replace("  ", " ").strip()
        if any(m in team_name for m in _NON_TEAM_TOGGLE_MARKERS):
            continue  # 자동 생성 로그 토글 — 팀이 아니다
        if team_name in SKIP_TEAMS or "노션x" in team_name.lower() or "노션 x" in team_name.lower():
            logger.info("team_skipped", team=team_name)
            continue
        if only_teams and team_name not in only_teams:
            continue
        logger.info("crawling_team", team=team_name)
        # Create team root node
        team_root = _add_node(None, team_name, "team", team_name, depth=0, block_id=block["id"])
        _crawl_recursive(block["id"], team_root, team_name, depth=1)
        team_count = sum(1 for n in _nodes if n["team"] == team_name)
        logger.info("team_crawled", team=team_name, count=team_count)

    return _nodes


def save_to_mariadb(nodes: List[Dict]) -> int:
    from app.db.mariadb import execute, execute_lastid, ensure_team_resources_table
    ensure_team_resources_table()

    # Clear existing (FK cascade handles children)
    execute("SET FOREIGN_KEY_CHECKS=0")
    execute("DELETE FROM team_resources")
    execute("SET FOREIGN_KEY_CHECKS=1")

    if not nodes:
        return 0

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tmp_to_real = {}

    # Sort by depth so parents are inserted before children
    sorted_nodes = sorted(nodes, key=lambda n: n["depth"])

    for node in sorted_nodes:
        parent_real_id = None
        if node["parent_tmp_id"] is not None:
            parent_real_id = tmp_to_real.get(node["parent_tmp_id"])

        real_id = execute_lastid(
            "INSERT INTO team_resources (parent_id, team, node_type, name, url, description, "
            "resource_type, depth, sort_order, notion_block_id, synced_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (parent_real_id, node["team"], node["node_type"], node["name"],
             node["url"], node["description"], node["resource_type"],
             node["depth"], node["sort_order"], node["notion_block_id"], now)
        )
        tmp_to_real[node["tmp_id"]] = real_id

    return len(nodes)


def print_tree(nodes: List[Dict]):
    """Print tree structure for dry-run preview."""
    # Build parent → children map
    children_map = {}
    node_map = {}
    for n in nodes:
        node_map[n["tmp_id"]] = n
        pid = n["parent_tmp_id"]
        children_map.setdefault(pid, []).append(n)

    def _print(parent_id, indent=0):
        for child in sorted(children_map.get(parent_id, []), key=lambda x: x["sort_order"]):
            icon = {"team": "🏢", "folder": "📁", "sheet": "📊", "page": "📋",
                    "database": "🗃️", "text": "📝"}.get(child["node_type"], "•")
            url_hint = f" → {child['url'][:50]}" if child["url"] else ""
            print(f"{'  ' * indent}{icon} {child['name'][:60]}{url_hint}")
            _print(child["tmp_id"], indent + 1)

    # Start from root (parent_id=None)
    _print(None)


def _enrich_with_playwright(teams: List[str], min_desc_len: int = 200):
    """Post-sync: use Playwright to crawl Notion pages with short descriptions."""
    # ⛔ **가드가 엉뚱한 것을 검사하고 있었다** (2026-08-18 로그 분석).
    #    아래 import 는 헬퍼 모듈만 확인한다 — playwright 자체는 `crawl_page_playwright`
    #    **안에서** import 되므로, 없으면 여기서 안 걸리고 **페이지마다 터진다.**
    #    그래서 하루 30건 × 7일 = 210건이 `playwright_page_failed` 로 쌓였고,
    #    메시지가 "No module named 'playwright'"인데도 개별 페이지 오류처럼 보여
    #    **기능이 통째로 죽은 사실이 묻혔다.** 진짜 오류를 가리는 소음이기도 하다.
    #    의존성은 **한 번만, 실제로** 확인하고 없으면 조용히 건너뛴다.
    try:
        from scripts.crawl_notion_pages import get_pages_to_crawl, crawl_page_playwright, update_description
    except ImportError:
        logger.warning("playwright_enrichment_skipped", reason="crawl_notion_pages not importable")
        return
    try:
        import playwright  # noqa: F401
    except ImportError:
        # 서버에는 브라우저 바이너리까지 필요해 설치하지 않는다. 보강만 건너뛰고
        # 동기화 자체는 정상 완료한다 — 페이지마다 실패를 찍는 것보다 낫다.
        logger.warning("playwright_enrichment_skipped", reason="playwright not installed",
                       impact="노션 페이지 본문 보강 없음 (동기화 자체는 정상)")
        return

    pages = get_pages_to_crawl(teams, min_desc_len=min_desc_len)
    if not pages:
        logger.info("playwright_enrichment_skip", reason="no short-desc pages")
        return

    logger.info("playwright_enrichment_start", teams=teams, pages=len(pages))
    crawled = 0
    for row in pages[:30]:  # Cap at 30 pages per sync
        url = row.get("url", "")
        if not url or not url.startswith("http"):
            continue
        try:
            title, text = crawl_page_playwright(url, timeout_ms=15000)
            if text and len(text) > 50:
                update_description(row["id"], title, text)
                crawled += 1
                logger.info("playwright_page_enriched", id=row["id"], name=row["name"][:30], chars=len(text))
        except Exception as e:
            logger.warning("playwright_page_failed", id=row["id"], error=str(e)[:60])
    logger.info("playwright_enrichment_done", crawled=crawled, total=len(pages))


def sync(dry_run: bool = False, teams: Optional[list] = None, no_playwright: bool = False) -> int:
    """Programmatic entry point for daily cron job."""
    only = set(teams) if teams else None
    nodes = crawl_all_teams(only_teams=only)
    if dry_run:
        return len(nodes)
    count = save_to_mariadb(nodes)
    if not no_playwright:
        enrich_teams = list(only) if only else list({n["team"] for n in nodes if n["node_type"] == "team"})
        if enrich_teams:
            _enrich_with_playwright(enrich_teams)
    return count


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--teams", nargs="+", help="Only crawl specific teams (e.g. CS IT PEOPLE)")
    parser.add_argument("--no-playwright", action="store_true", help="Skip Playwright enrichment")
    args = parser.parse_args()

    only = set(args.teams) if args.teams else None
    nodes = crawl_all_teams(only_teams=only)

    if args.dry_run:
        print(f"\n--- Tree Preview ({len(nodes)} nodes) ---\n")
        print_tree(nodes)
        print(f"\nSync complete: {len(nodes)} nodes (dry-run)")
        return

    count = save_to_mariadb(nodes)
    print(f"Sync complete: {count} nodes")

    # Post-sync: enrich short descriptions with Playwright
    if not args.no_playwright:
        enrich_teams = list(only) if only else [n["team"] for n in nodes if n["node_type"] == "team"]
        enrich_teams = list(set(enrich_teams))
        if enrich_teams:
            _enrich_with_playwright(enrich_teams)


if __name__ == "__main__":
    main()
