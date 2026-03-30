"""Debug image blocks to see their actual data."""
import httpx
import os
import sys
import json

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

blocks = []
cursor = None
while True:
    url = f"https://api.notion.com/v1/blocks/{TOGGLE_ID}/children?page_size=100"
    if cursor:
        url += f"&start_cursor={cursor}"
    resp = client.get(url, headers=headers)
    data = resp.json()
    blocks.extend(data.get("results", []))
    if not data.get("has_more"):
        break
    cursor = data.get("next_cursor")

for i, b in enumerate(blocks):
    if b["type"] == "image":
        print(f"Block {i} (ID: {b['id']}):")
        print(json.dumps(b["image"], indent=2, ensure_ascii=False))
        print()
