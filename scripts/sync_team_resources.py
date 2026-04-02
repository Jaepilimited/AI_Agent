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

TEAM_DATA_TOGGLE_ID = "3272b428-3b00-806d-aabf-cfbcd9237fb0"

SKIP_TEAMS = {"유통1(노션x)", "유통2(노션x)", ""}

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


def _crawl_recursive(block_id: str, parent_tmp_id: int, team: str, depth: int = 1):
    if depth > 10:
        return
    children = _get_block_children(block_id)
    sort = 0
    # Track current heading folder for grouping
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
                _add_node(heading_parent, team, nt, name, url, desc, depth, bid, sort)
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
            if title:
                node_id = _add_node(heading_parent, team, "page", title, page_url, depth=depth, block_id=bid, sort_order=sort)
                if has_ch:
                    _crawl_recursive(bid, node_id, team, depth + 1)
            continue

        # --- Child database ---
        if btype == "child_database":
            title = child.get("child_database", {}).get("title", "")
            db_url = f"https://www.notion.so/{bid.replace('-', '')}"
            if title:
                _add_node(heading_parent, team, "database", title, db_url, depth=depth, block_id=bid, sort_order=sort)
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
            _add_node(heading_parent, team, nt, name_part or href[:80], href, depth=depth, block_id=bid, sort_order=sort)
        elif text and len(text) >= 5:
            _add_node(heading_parent, team, "text", text[:120], desc=text, depth=depth, block_id=bid, sort_order=sort)

        # Recurse children (bulleted lists with sub-items)
        if has_ch:
            if btype in ("bulleted_list_item", "numbered_list_item") and text and not href:
                folder_id = _add_node(parent_tmp_id, team, "folder", text, depth=depth, block_id=bid, sort_order=sort)
                _crawl_recursive(bid, folder_id, team, depth + 1)
            else:
                _crawl_recursive(bid, heading_parent, team, depth + 1)


def crawl_all_teams():
    global _nodes, _node_counter
    _nodes = []
    _node_counter = 0

    team_blocks = _get_block_children(TEAM_DATA_TOGGLE_ID)
    for block in team_blocks:
        if block["type"] != "toggle":
            continue
        team_name = _extract_text(block["toggle"].get("rich_text", []))
        team_name = team_name.replace("[GM]", "GM ").replace("  ", " ").strip()
        if team_name in SKIP_TEAMS or "노션x" in team_name.lower() or "노션 x" in team_name.lower():
            logger.info("team_skipped", team=team_name)
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    nodes = crawl_all_teams()

    if args.dry_run:
        print(f"\n--- Tree Preview ({len(nodes)} nodes) ---\n")
        print_tree(nodes)
        print(f"\nSync complete: {len(nodes)} nodes (dry-run)")
        return

    count = save_to_mariadb(nodes)
    print(f"Sync complete: {count} nodes")


if __name__ == "__main__":
    main()
