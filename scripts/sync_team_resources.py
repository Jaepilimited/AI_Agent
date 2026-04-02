"""Sync team resources from Notion DB HUB to MariaDB.

Usage:
    python scripts/sync_team_resources.py              # Full sync
    python scripts/sync_team_resources.py --dry-run    # Preview only
"""
import os
import re
import sys
import argparse
from datetime import datetime
from typing import List, Dict

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

SKIP_TEAMS = {
    "유통1(노션x)", "유통2(노션x)",
    "",
}

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
    """Extract first URL from rich_text href or mention."""
    for t in rich_text_list:
        href = t.get("href")
        if href:
            return href
        # Notion mention (page/database link)
        mention = t.get("mention", {})
        mtype = mention.get("type", "")
        if mtype == "page":
            pid = mention["page"]["id"]
            return f"https://www.notion.so/{pid.replace('-', '')}"
        if mtype == "database":
            did = mention["database"]["id"]
            return f"https://www.notion.so/{did.replace('-', '')}"
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


def _make_resource(team: str, category: str, name: str, url: str, desc: str = "") -> Dict:
    return {
        "team": team, "category": category,
        "name": name.strip(), "resource_type": _detect_type(url),
        "url": url.strip(), "description": desc.strip(),
    }


def _extract_block_content(block: dict) -> tuple:
    """Extract (text, href) from any block's rich_text + href/mention."""
    btype = block["type"]
    rt = []
    if btype in block and isinstance(block[btype], dict):
        rt = block[btype].get("rich_text", [])
    text = _extract_text(rt)
    href = _extract_href(rt)
    # Fallback: check inline URLs in text
    if not href:
        urls = _URL_RE.findall(text)
        if urls:
            href = urls[0]
    return text, href


def _crawl_block_recursive(block_id: str, team: str, category: str, depth: int = 0) -> List[Dict]:
    if depth > 8:
        return []
    resources = []
    children = _get_block_children(block_id)
    for child in children:
        btype = child["type"]
        bid = child["id"]
        has_ch = child.get("has_children", False)

        # --- Table: parse rows ---
        if btype == "table":
            rows = _parse_table_rows(bid)
            for row in rows:
                name = (row.get("시트명") or row.get("name") or row.get("이름")
                        or row.get("시트") or row.get("제목") or "")
                url = (row.get("URL") or row.get("url") or row.get("링크")
                       or row.get("링크 ") or row.get("Link") or "")
                desc = row.get("비고") or row.get("description") or row.get("설명") or ""
                sub_cat = row.get("파트") or row.get("카테고리") or category
                if not name and not url:
                    continue
                if not url:
                    urls = _URL_RE.findall(" ".join(row.values()))
                    url = urls[0] if urls else ""
                resources.append(_make_resource(team, sub_cat or category, name, url, desc))
            continue

        # --- Toggle: title becomes category, recurse ---
        if btype == "toggle":
            title = _extract_text(child["toggle"].get("rich_text", []))
            resources.extend(_crawl_block_recursive(bid, team, title or category, depth + 1))
            continue

        # --- Child page: title as name, Notion link ---
        if btype == "child_page":
            title = child.get("child_page", {}).get("title", "")
            page_url = f"https://www.notion.so/{bid.replace('-', '')}"
            if title:
                resources.append(_make_resource(team, category, title, page_url))
            if has_ch:
                resources.extend(_crawl_block_recursive(bid, team, title or category, depth + 1))
            continue

        # --- Child database ---
        if btype == "child_database":
            title = child.get("child_database", {}).get("title", "")
            db_url = f"https://www.notion.so/{bid.replace('-', '')}"
            if title:
                resources.append(_make_resource(team, category, title, db_url))
            continue

        # --- Bookmark / Embed: direct URL ---
        if btype == "bookmark":
            url = child.get("bookmark", {}).get("url", "")
            caption = _extract_text(child.get("bookmark", {}).get("caption", []))
            if url:
                resources.append(_make_resource(team, category, caption or url[:80], url))
            continue
        if btype == "embed":
            url = child.get("embed", {}).get("url", "")
            if url:
                resources.append(_make_resource(team, category, url[:80], url))
            continue

        # --- All other text blocks: paragraph, bulleted_list_item, numbered_list_item, heading_* ---
        text, href = _extract_block_content(child)
        if href:
            name_part = text.split("http")[0].strip() if "http" in text else text
            resources.append(_make_resource(team, category, name_part or href[:80], href))

        # Recurse into children if present (bulleted lists, numbered lists, etc.)
        if has_ch:
            sub_cat = text if (btype in ("bulleted_list_item", "numbered_list_item") and text and not href) else category
            resources.extend(_crawl_block_recursive(bid, team, sub_cat, depth + 1))

    return resources


def crawl_all_teams() -> List[Dict]:
    team_blocks = _get_block_children(TEAM_DATA_TOGGLE_ID)
    all_resources = []
    for block in team_blocks:
        if block["type"] != "toggle":
            continue
        team_name = _extract_text(block["toggle"].get("rich_text", []))
        # Normalize team names: [GM]EAST → GM EAST
        team_name = team_name.replace("[GM]", "GM ").replace("  ", " ").strip()
        if team_name in SKIP_TEAMS or "노션x" in team_name.lower() or "노션 x" in team_name.lower():
            logger.info("team_skipped", team=team_name)
            continue
        logger.info("crawling_team", team=team_name)
        team_resources = _crawl_block_recursive(block["id"], team_name, "")
        all_resources.extend(team_resources)
        logger.info("team_crawled", team=team_name, count=len(team_resources))
    return all_resources


def save_to_mariadb(resources: List[Dict]) -> int:
    from app.db.mariadb import execute, ensure_team_resources_table
    ensure_team_resources_table()
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
    resources = crawl_all_teams()
    if dry_run:
        print(f"\n=== DRY RUN: {len(resources)} resources found ===\n")
        for r in resources:
            print(f"  [{r['team']}] {r['category']} | {r['name']} | {r['resource_type']} | {(r['url'] or 'N/A')[:60]}")
        return len(resources)
    return save_to_mariadb(resources)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync team resources from Notion DB HUB")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no DB writes")
    args = parser.parse_args()
    count = sync(dry_run=args.dry_run)
    print(f"\nSync complete: {count} resources")
