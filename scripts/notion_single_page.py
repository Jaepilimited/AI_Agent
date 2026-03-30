"""Set up single tester page with 해결완료 + 미해결 sections."""
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

PAGE_RESOLVED = "3252b428-3b00-8070-964f-d92fa77d41ec"  # has images
PAGE_UNRESOLVED = "3192b428-3b00-804f-808e-e2714e1ff52f"  # empty now


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


def get_text(block):
    btype = block["type"]
    if btype in ("heading_1", "heading_2", "heading_3", "paragraph",
                 "toggle", "callout", "bulleted_list_item"):
        return "".join(r.get("plain_text", "") for r in block[btype].get("rich_text", []))
    return ""


# ── Step 1: Add 미해결 section to 해결완료 page ──
print("Step 1: Adding 미해결 section to single page...")
new_blocks = [
    {"object": "block", "type": "divider", "divider": {}},
    {
        "object": "block",
        "type": "heading_1",
        "heading_1": {
            "rich_text": [rt("미해결", bold=True)],
        },
    },
    {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [rt("이슈 캡쳐와 설명을 아래에 추가하세요.", color="gray")],
        },
    },
]
resp = client.patch(
    f"https://api.notion.com/v1/blocks/{PAGE_RESOLVED}/children",
    headers=headers,
    json={"children": new_blocks},
)
print(f"  Added: {resp.status_code}")

# ── Step 2: Verify single page ──
print("\nStep 2: Final structure:")
blocks = get_children(PAGE_RESOLVED)
for i, b in enumerate(blocks):
    btype = b["type"]
    text = get_text(b) or ("[IMAGE]" if btype == "image" else btype)
    print(f"  {i:2d}. [{btype:15s}] {text[:70]}")

print(f"\nSingle page ready: {PAGE_RESOLVED}")
print("Done!")
