"""Set up two tester pages: clean 미해결건, tidy 해결완료."""
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

PAGE_UNRESOLVED = "3192b428-3b00-804f-808e-e2714e1ff52f"  # 미해결건
PAGE_RESOLVED = "3252b428-3b00-8070-964f-d92fa77d41ec"      # 해결완료


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


def delete_block(bid):
    resp = client.delete(f"https://api.notion.com/v1/blocks/{bid}", headers=headers)
    return resp.status_code


# ══════════════════════════════════════════════════════════
# 1. 미해결건 페이지 — 해결된 내용 전부 삭제
# ══════════════════════════════════════════════════════════
print("=" * 50)
print("[미해결건] Cleaning up resolved content...")
blocks = get_children(PAGE_UNRESOLVED)
print(f"  Current: {len(blocks)} blocks")

# Delete all blocks (reverse order)
for i in range(len(blocks) - 1, -1, -1):
    status = delete_block(blocks[i]["id"])
    time.sleep(0.12)
print(f"  Deleted all {len(blocks)} blocks")

# Add clean empty state
new_blocks = [
    {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [
                rt("이슈 캡쳐와 설명을 아래에 추가하세요.", color="gray"),
            ],
        },
    },
]
resp = client.patch(
    f"https://api.notion.com/v1/blocks/{PAGE_UNRESOLVED}/children",
    headers=headers,
    json={"children": new_blocks},
)
print(f"  Added placeholder: {resp.status_code}")

# ══════════════════════════════════════════════════════════
# 2. 해결완료 페이지 — 하단 divider + 빈 문단 제거
# ══════════════════════════════════════════════════════════
print("\n" + "=" * 50)
print("[해결완료] Cleaning up trailing blocks...")
blocks = get_children(PAGE_RESOLVED)
print(f"  Current: {len(blocks)} blocks")

# Delete trailing empty paragraph + divider
to_delete = []
for b in reversed(blocks):
    btype = b["type"]
    if btype == "paragraph":
        text = "".join(r.get("plain_text", "") for r in b["paragraph"].get("rich_text", [])).strip()
        if not text:
            to_delete.append(b["id"])
            continue
    if btype == "divider":
        to_delete.append(b["id"])
        continue
    # Stop when we hit a toggle block with content
    if btype == "heading_1" and "미해결" in "".join(r.get("plain_text", "") for r in b["heading_1"].get("rich_text", [])):
        to_delete.append(b["id"])
        continue
    break

for bid in to_delete:
    delete_block(bid)
    time.sleep(0.12)
print(f"  Removed {len(to_delete)} trailing blocks")

# ══════════════════════════════════════════════════════════
# 3. Verify both pages
# ══════════════════════════════════════════════════════════
print("\n" + "=" * 50)
print("Final verification:")

for name, pid in [("미해결건", PAGE_UNRESOLVED), ("해결완료", PAGE_RESOLVED)]:
    blocks = get_children(pid)
    print(f"\n[{name}] {len(blocks)} blocks:")
    for i, b in enumerate(blocks):
        btype = b["type"]
        text = ""
        if btype in ("heading_1", "paragraph", "toggle", "callout", "bulleted_list_item"):
            text = "".join(r.get("plain_text", "") for r in b[btype].get("rich_text", []))
        elif btype == "image":
            text = "[IMAGE]"
        elif btype == "divider":
            text = "────"
        print(f"  {i:2d}. [{btype:15s}] {text[:70]}")

print("\nDone!")
