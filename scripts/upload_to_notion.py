#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Upload update log to Notion page as toggle blocks.

Usage:
  python -X utf8 scripts/upload_to_notion.py --updatelog
  python -X utf8 scripts/upload_to_notion.py --updatelog --file docs/update_log_2026-04-08.md
"""

import argparse
import os
import re
import sys
from pathlib import Path
from datetime import datetime

import requests

# Notion API
NOTION_TOKEN = os.getenv("NOTION_MCP_TOKEN", "")
if not NOTION_TOKEN:
    # Load from .env
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("NOTION_MCP_TOKEN="):
                NOTION_TOKEN = line.split("=", 1)[1].strip()
                break

NOTION_API = "https://api.notion.com/v1"
HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

# Update log page ID
UPDATE_LOG_PAGE_ID = "3252b4283b00802aaff3f33f63ec91de"


def md_to_rich_text(text: str) -> list:
    """Convert markdown inline formatting to Notion rich_text."""
    parts = []
    # Split by **bold** patterns
    segments = re.split(r'(\*\*.*?\*\*)', text)
    for seg in segments:
        if seg.startswith("**") and seg.endswith("**"):
            parts.append({
                "type": "text",
                "text": {"content": seg[2:-2]},
                "annotations": {"bold": True},
            })
        elif seg:
            parts.append({
                "type": "text",
                "text": {"content": seg},
            })
    return parts if parts else [{"type": "text", "text": {"content": text}}]


def md_to_blocks(md_content: str) -> list:
    """Convert markdown content to Notion blocks."""
    blocks = []
    lines = md_content.split("\n")
    in_table = False
    table_rows = []

    i = 0
    while i < len(lines):
        line = lines[i]

        # Skip empty lines
        if not line.strip():
            i += 1
            continue

        # Skip H1 title (will be used as toggle title)
        if line.startswith("# ") and not line.startswith("## "):
            i += 1
            continue

        # H2 → heading_2
        if line.startswith("## "):
            blocks.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {"rich_text": md_to_rich_text(line[3:].strip())},
            })
            i += 1
            continue

        # H3 → heading_3
        if line.startswith("### "):
            blocks.append({
                "object": "block",
                "type": "heading_3",
                "heading_3": {"rich_text": md_to_rich_text(line[4:].strip())},
            })
            i += 1
            continue

        # Table detection
        if "|" in line and line.strip().startswith("|"):
            if not in_table:
                in_table = True
                table_rows = []
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            # Skip separator rows (---|---)
            if all(re.match(r'^[-:]+$', c) for c in cells):
                i += 1
                continue
            table_rows.append(cells)
            i += 1
            # Check if next line is still table
            if i < len(lines) and "|" in lines[i] and lines[i].strip().startswith("|"):
                continue
            else:
                # End of table — build table block
                if table_rows:
                    width = max(len(r) for r in table_rows)
                    notion_rows = []
                    for row_cells in table_rows:
                        # Pad to width
                        padded = row_cells + [""] * (width - len(row_cells))
                        notion_row = {
                            "type": "table_row",
                            "table_row": {
                                "cells": [[{"type": "text", "text": {"content": c}}] for c in padded[:width]]
                            },
                        }
                        notion_rows.append(notion_row)
                    blocks.append({
                        "object": "block",
                        "type": "table",
                        "table": {
                            "table_width": width,
                            "has_column_header": True,
                            "has_row_header": False,
                            "children": notion_rows,
                        },
                    })
                in_table = False
                table_rows = []
                continue

        # Bullet list
        if line.startswith("- "):
            blocks.append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": md_to_rich_text(line[2:].strip())},
            })
            i += 1
            continue

        # Default → paragraph
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": md_to_rich_text(line.strip())},
        })
        i += 1

    return blocks


def upload_update_log(md_path: str):
    """Upload markdown update log to Notion as a toggle block."""
    content = Path(md_path).read_text(encoding="utf-8")

    # Extract title from H1
    title_match = re.search(r'^# (.+)$', content, re.MULTILINE)
    title = title_match.group(1) if title_match else f"Update Log — {datetime.now().strftime('%Y-%m-%d')}"

    # Convert to blocks
    children = md_to_blocks(content)

    # Notion API limit: max 100 blocks per request. Split if needed.
    # First, create the toggle heading
    toggle_block = {
        "object": "block",
        "type": "toggle",
        "toggle": {
            "rich_text": [
                {
                    "type": "text",
                    "text": {"content": f"📋 {title}"},
                    "annotations": {"bold": True},
                }
            ],
            "children": children[:99],  # Notion limit: 100 children per block
        },
    }

    # Prepend to page (add after title, before existing content)
    # Use "after" parameter if there's existing content, else just append
    url = f"{NOTION_API}/blocks/{UPDATE_LOG_PAGE_ID}/children"

    # First, get existing children to find the first block ID
    resp = requests.get(url, headers=HEADERS, params={"page_size": 1})
    if resp.status_code != 200:
        print(f"Error fetching page: {resp.status_code} {resp.text}")
        return False

    existing = resp.json().get("results", [])

    # Patch: prepend by adding new block
    payload = {"children": [toggle_block]}

    resp = requests.patch(url, headers=HEADERS, json=payload)
    if resp.status_code == 200:
        print(f"✅ Uploaded: {title}")
        print(f"   Page: https://notion.so/{UPDATE_LOG_PAGE_ID.replace('-', '')}")
        print(f"   Blocks: {len(children)}")
        return True
    else:
        print(f"❌ Upload failed: {resp.status_code}")
        print(f"   {resp.text[:500]}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Upload update log to Notion")
    parser.add_argument("--updatelog", action="store_true", help="Upload update log")
    parser.add_argument("--file", type=str, help="Specific markdown file to upload")
    args = parser.parse_args()

    if not args.updatelog:
        parser.print_help()
        return

    if args.file:
        md_path = args.file
    else:
        # Find the latest update log
        docs_dir = Path(__file__).resolve().parent.parent / "docs"
        logs = sorted(docs_dir.glob("update_log_*.md"), reverse=True)
        if not logs:
            print("No update log files found in docs/")
            return
        md_path = str(logs[0])

    print(f"Uploading: {md_path}")
    if not NOTION_TOKEN:
        print("❌ NOTION_MCP_TOKEN not found in .env")
        return

    upload_update_log(md_path)


if __name__ == "__main__":
    main()
