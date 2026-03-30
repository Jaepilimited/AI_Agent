"""Update heading text and add 미해결 section. Does NOT touch image blocks."""
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
PAGE_ID = "3192b428-3b00-804f-808e-e2714e1ff52f"


def rt(text, bold=False, color="default"):
    return {"type": "text", "text": {"content": text}, "annotations": {"bold": bold, "color": color}}


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


# Step 1: Update heading text — remove "AI 자동 수정"
blocks = get_children(PAGE_ID)
heading_id = blocks[0]["id"]
print(f"Step 1: Updating heading (ID: {heading_id})...")
resp = client.patch(
    f"https://api.notion.com/v1/blocks/{heading_id}",
    headers=headers,
    json={
        "heading_1": {
            "rich_text": [rt("해결 완료", bold=True)],
            "is_toggleable": True,
        }
    },
)
print(f"  Heading updated: {resp.status_code}")
if resp.status_code != 200:
    print(f"  Error: {resp.text[:300]}")

# Step 2: Delete trailing empty paragraph if present
last_block = blocks[-1]
last_text = ""
if last_block["type"] == "paragraph":
    last_rt = last_block["paragraph"].get("rich_text", [])
    last_text = "".join(r.get("plain_text", "") for r in last_rt).strip()

if not last_text and last_block["type"] == "paragraph":
    resp = client.delete(f"https://api.notion.com/v1/blocks/{last_block['id']}", headers=headers)
    print(f"Step 2: Deleted trailing empty paragraph: {resp.status_code}")

# Step 3: Add divider + 미해결 heading at the bottom
print("Step 3: Adding divider + 미해결 heading...")
new_blocks = [
    {"object": "block", "type": "divider", "divider": {}},
    {
        "object": "block",
        "type": "heading_1",
        "heading_1": {
            "rich_text": [rt("미해결", bold=True)],
            "is_toggleable": False,
        },
    },
    {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": []},
    },
]

resp = client.patch(
    f"https://api.notion.com/v1/blocks/{PAGE_ID}/children",
    headers=headers,
    json={"children": new_blocks},
)
print(f"  Added: {resp.status_code}")

# Step 4: Verify
time.sleep(1)
final = get_children(PAGE_ID)
print(f"\nFinal structure ({len(final)} blocks):")
for i, b in enumerate(final):
    btype = b["type"]
    text = ""
    if btype in ("heading_1", "paragraph", "toggle", "callout", "bulleted_list_item"):
        text = "".join(r.get("plain_text", "") for r in b[btype].get("rich_text", []))
    elif btype == "image":
        text = "[IMAGE]"
    elif btype == "divider":
        text = "────"
    toggleable = ""
    if btype == "heading_1" and b["heading_1"].get("is_toggleable"):
        toggleable = " [TOGGLEABLE]"
    print(f"  {i:2d}. [{btype:15s}]{toggleable} {text[:60]}")

print("\nDone!")
