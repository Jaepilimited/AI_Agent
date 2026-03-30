"""Read both tester pages to see current state."""
import httpx
import os
import sys

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

PAGES = {
    "미해결건": "3192b428-3b00-804f-808e-e2714e1ff52f",
    "해결완료": "3252b428-3b00-8070-964f-d92fa77d41ec",
}


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
    if btype in ("heading_1", "heading_2", "heading_3", "paragraph",
                 "toggle", "callout", "bulleted_list_item"):
        rt = block[btype].get("rich_text", [])
        return "".join(r.get("plain_text", "") for r in rt)
    return ""


for name, pid in PAGES.items():
    # Get page title
    resp = client.get(f"https://api.notion.com/v1/pages/{pid}", headers=headers)
    page_data = resp.json()
    title = ""
    for prop in page_data.get("properties", {}).values():
        if prop.get("type") == "title":
            title = "".join(t.get("plain_text", "") for t in prop.get("title", []))
            break

    print(f"\n{'='*60}")
    print(f"[{name}] Page title: {title}")
    print(f"  ID: {pid}")
    print(f"{'='*60}")

    blocks = get_children(pid)
    print(f"  {len(blocks)} blocks:")
    for i, b in enumerate(blocks):
        btype = b["type"]
        text = get_text(b) or ("[IMAGE]" if btype == "image" else btype)
        toggleable = " [T]" if btype == "heading_1" and b["heading_1"].get("is_toggleable") else ""
        print(f"  {i:2d}. [{btype:15s}]{toggleable} {text[:70]}")
