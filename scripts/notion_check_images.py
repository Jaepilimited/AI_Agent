"""Check image blocks inside the toggle heading for broken URLs."""
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


# Step 1: Get top-level blocks
top_blocks = get_children(PAGE_ID)
print(f"Top-level blocks: {len(top_blocks)}")
for i, b in enumerate(top_blocks):
    btype = b["type"]
    text = ""
    if btype in ("heading_1", "heading_2", "paragraph", "toggle"):
        rt = b[btype].get("rich_text", [])
        text = "".join(r.get("plain_text", "") for r in rt)
    print(f"  {i}. [{btype}] children={b.get('has_children')} | {text[:60]}")

# Step 2: Get children of the toggle heading (index 0)
toggle_id = top_blocks[0]["id"]
print(f"\nToggle heading children (ID: {toggle_id}):")
children = get_children(toggle_id)
print(f"  Found {len(children)} children")

broken = []
ok = []
for i, b in enumerate(children):
    btype = b["type"]
    if btype == "image":
        img = b["image"]
        img_type = img.get("type", "?")
        if img_type == "external":
            url = img.get("external", {}).get("url", "")
        elif img_type == "file":
            url = img.get("file", {}).get("url", "")
        else:
            url = "?"

        # Test if URL is accessible
        try:
            resp = client.head(url, follow_redirects=True, timeout=10)
            status = resp.status_code
        except Exception as e:
            status = f"ERR: {e}"

        is_ok = status == 200
        marker = "OK" if is_ok else "BROKEN"
        print(f"  {i:2d}. [image/{img_type}] {marker} (HTTP {status}) | {url[:80]}...")
        if is_ok:
            ok.append(i)
        else:
            broken.append((i, b["id"], status))
    else:
        text = ""
        if btype in ("paragraph", "toggle", "heading_1", "heading_2", "bulleted_list_item", "callout"):
            rt = b[btype].get("rich_text", [])
            text = "".join(r.get("plain_text", "") for r in rt)
        print(f"  {i:2d}. [{btype:15s}] {text[:70]}")

print(f"\n=== Summary ===")
print(f"  OK images: {len(ok)}")
print(f"  Broken images: {len(broken)}")
for idx, bid, status in broken:
    print(f"    Block {idx} (ID: {bid}): HTTP {status}")
