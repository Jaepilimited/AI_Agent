"""Restructure Notion tester page: wrap resolved issues in toggleable heading, add 미해결 section."""
import httpx
import os
import sys
import time
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
PAGE_ID = "3192b428-3b00-804f-808e-e2714e1ff52f"


def get_all_children(block_id):
    """Get all children of a block (paginated)."""
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


def block_to_create_payload(block):
    """Convert a read block into a create-compatible payload (strip read-only fields)."""
    btype = block["type"]

    # Handle image blocks
    if btype == "image":
        img = block["image"]
        img_payload = {"type": img["type"]}
        if img["type"] == "file":
            # File URLs expire, use as external URL (best effort)
            img_payload = {"type": "external", "external": {"url": img["file"]["url"]}}
        elif img["type"] == "external":
            img_payload = {"type": "external", "external": {"url": img["external"]["url"]}}
        caption = img.get("caption", [])
        img_payload["caption"] = caption
        return {"object": "block", "type": "image", "image": img_payload}

    # Handle toggle blocks (need to fetch and include children)
    if btype == "toggle":
        toggle_data = block["toggle"]
        children = []
        if block.get("has_children"):
            child_blocks = get_all_children(block["id"])
            children = [block_to_create_payload(cb) for cb in child_blocks]
        payload = {
            "object": "block",
            "type": "toggle",
            "toggle": {
                "rich_text": toggle_data.get("rich_text", []),
                "color": toggle_data.get("color", "default"),
            }
        }
        if children:
            payload["toggle"]["children"] = children
        return payload

    # Handle text-based blocks
    text_types = {
        "paragraph": "paragraph",
        "heading_1": "heading_1",
        "heading_2": "heading_2",
        "heading_3": "heading_3",
        "bulleted_list_item": "bulleted_list_item",
        "callout": "callout",
    }

    if btype in text_types:
        block_data = block[btype]
        payload = {
            "object": "block",
            "type": btype,
            btype: {
                "rich_text": block_data.get("rich_text", []),
            }
        }
        # Preserve color
        if "color" in block_data:
            payload[btype]["color"] = block_data["color"]
        # Callout icon
        if btype == "callout" and "icon" in block_data:
            payload[btype]["icon"] = block_data["icon"]
        return payload

    # Handle divider
    if btype == "divider":
        return {"object": "block", "type": "divider", "divider": {}}

    # Fallback: return as paragraph with type label
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"[{btype} block]"}}]}
    }


def delete_block(block_id):
    resp = client.delete(f"https://api.notion.com/v1/blocks/{block_id}", headers=headers)
    return resp.status_code


def get_block_text(block):
    btype = block["type"]
    if btype in ("paragraph", "heading_1", "heading_2", "heading_3",
                 "bulleted_list_item", "callout", "toggle"):
        rich_text = block[btype].get("rich_text", [])
        return "".join(r.get("plain_text", "") for r in rich_text)
    return ""


# ── Step 1: Read current page ──
print("Step 1: Reading current page...")
blocks = get_all_children(PAGE_ID)
print(f"  Found {len(blocks)} blocks")

for i, b in enumerate(blocks):
    text = get_block_text(b) or ("[IMAGE]" if b["type"] == "image" else b["type"])
    print(f"  {i:2d}. [{b['type']:15s}] {text[:70]}")

# ── Step 2: Build payloads for all content blocks (skip heading_1 at index 0) ──
print("\nStep 2: Building block payloads...")
content_blocks = []
for i, b in enumerate(blocks):
    if i == 0:
        continue  # Skip the heading — we'll create a new toggleable one
    text = get_block_text(b).strip()
    if i == len(blocks) - 1 and not text and b["type"] == "paragraph":
        continue  # Skip trailing empty paragraph
    payload = block_to_create_payload(b)
    content_blocks.append(payload)
    time.sleep(0.1)  # Rate limit for child fetches

print(f"  Built {len(content_blocks)} content block payloads")

# ── Step 3: Delete all existing blocks (reverse order) ──
print("\nStep 3: Deleting existing blocks...")
for i in range(len(blocks) - 1, -1, -1):
    status = delete_block(blocks[i]["id"])
    if status == 200:
        print(f"  Deleted block {i}")
    else:
        print(f"  FAILED to delete block {i}: {status}")
    time.sleep(0.15)

# ── Step 4: Create toggleable heading_1 "해결 완료" ──
print("\nStep 4: Creating toggleable heading_1...")

def rt(text, bold=False, color="default"):
    return {"type": "text", "text": {"content": text}, "annotations": {"bold": bold, "color": color}}

# Notion API: heading_1 with is_toggleable=true + children
# Max 100 children per request, so split if needed
CHUNK = 98  # Leave room

first_chunk = content_blocks[:CHUNK]
heading_block = {
    "object": "block",
    "type": "heading_1",
    "heading_1": {
        "rich_text": [
            rt("해결 완료", bold=True),
        ],
        "is_toggleable": True,
        "children": first_chunk,
    }
}

# Also create "미해결" heading
mihaegyeol_heading = {
    "object": "block",
    "type": "heading_1",
    "heading_1": {
        "rich_text": [
            rt("미해결", bold=True),
        ],
        "is_toggleable": False,
    }
}

# Empty paragraph for user to add content
empty_para = {
    "object": "block",
    "type": "paragraph",
    "paragraph": {"rich_text": []}
}

resp = client.patch(
    f"https://api.notion.com/v1/blocks/{PAGE_ID}/children",
    headers=headers,
    json={"children": [heading_block, empty_para, mihaegyeol_heading, empty_para]},
)
print(f"  Created: {resp.status_code}")
if resp.status_code != 200:
    print(f"  Error: {resp.text[:500]}")
    sys.exit(1)

heading_id = resp.json()["results"][0]["id"]
print(f"  Toggle heading ID: {heading_id}")

# Append remaining chunks if any
remaining = content_blocks[CHUNK:]
for i in range(0, len(remaining), CHUNK):
    chunk = remaining[i:i + CHUNK]
    resp2 = client.patch(
        f"https://api.notion.com/v1/blocks/{heading_id}/children",
        headers=headers,
        json={"children": chunk},
    )
    print(f"  Appended {len(chunk)} blocks: {resp2.status_code}")
    time.sleep(0.3)

# ── Step 5: Verify ──
print("\nStep 5: Verifying...")
time.sleep(1)
final_blocks = get_all_children(PAGE_ID)
print(f"  Page now has {len(final_blocks)} top-level blocks:")
for i, b in enumerate(final_blocks):
    text = get_block_text(b) or ("[IMAGE]" if b["type"] == "image" else b["type"])
    has_ch = b.get("has_children", False)
    print(f"  {i}. [{b['type']}] children={has_ch} | {text[:70]}")

print("\nDone! Page restructured with toggleable heading.")
