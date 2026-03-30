"""Read current blocks from Notion tester page."""
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
PAGE_ID = "3192b428-3b00-804f-808e-e2714e1ff52f"

all_blocks = []
cursor = None
while True:
    url = f"https://api.notion.com/v1/blocks/{PAGE_ID}/children?page_size=100"
    if cursor:
        url += f"&start_cursor={cursor}"
    resp = client.get(url, headers=headers)
    data = resp.json()
    all_blocks.extend(data.get("results", []))
    if not data.get("has_more"):
        break
    cursor = data.get("next_cursor")

for i, b in enumerate(all_blocks):
    btype = b["type"]
    text = ""
    if btype in ("paragraph", "heading_1", "heading_2", "heading_3",
                 "bulleted_list_item", "callout", "toggle"):
        rich_text = b[btype].get("rich_text", [])
        text = "".join(r.get("plain_text", "") for r in rich_text)
    elif btype == "image":
        text = "[IMAGE]"
    has_children = b.get("has_children", False)
    print(f"{i:2d}. [{btype:15s}] children={has_children} | {text[:80]}")
