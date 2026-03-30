"""Delete broken image blocks from the toggle heading."""
import httpx
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

token = os.getenv("NOTION_MCP_TOKEN")
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}
client = httpx.Client(timeout=30)

TOGGLE_ID = "3252b428-3b00-814e-9c5a-ccdf6f6498a6"


def get_children(block_id):
    blocks = []
    cursor = None
    while True:
        url = f"https://api.notion.com/v1/blocks/{block_id}/children?page_size=100"
        if cursor:
            url += f"&start_cursor={cursor}"
        resp = client.get(url, headers=headers)
        data = resp.json()
        blocks.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return blocks


def get_text(block):
    btype = block["type"]
    if btype in ("paragraph", "heading_1", "heading_2", "heading_3",
                 "bulleted_list_item", "callout", "toggle"):
        rt = block[btype].get("rich_text", [])
        return "".join(r.get("plain_text", "") for r in rt)
    return ""


# Read children
children = get_children(TOGGLE_ID)
print(f"Toggle has {len(children)} children")

# Find and delete broken image blocks
deleted = 0
for b in children:
    if b["type"] == "image":
        img = b["image"]
        # Check if image has no valid URL
        has_url = False
        if img.get("type") == "file" and img.get("file", {}).get("url"):
            has_url = True
        elif img.get("type") == "external" and img.get("external", {}).get("url"):
            has_url = True

        if not has_url:
            resp = client.delete(f"https://api.notion.com/v1/blocks/{b['id']}", headers=headers)
            print(f"  Deleted broken image {b['id']}: {resp.status_code}")
            deleted += 1
            time.sleep(0.15)

print(f"\nDeleted {deleted} broken images")

# Verify
time.sleep(1)
children = get_children(TOGGLE_ID)
print(f"\nToggle now has {len(children)} children:")
for i, b in enumerate(children):
    text = get_text(b) or b["type"]
    print(f"  {i:2d}. [{b['type']:15s}] {text[:70]}")
