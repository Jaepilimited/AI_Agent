"""Upload today's update log to Notion as a single toggle block."""
import httpx
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv

load_dotenv()

PAGE_ID = "3032b428-3b00-80ae-8241-cedef71fc3be"
NOTION_VERSION = "2022-06-28"
token = os.getenv("NOTION_MCP_TOKEN") or os.getenv("NOTION_API_KEY")
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
    "Notion-Version": NOTION_VERSION,
}

# Read today's update log
with open("docs/update_log_2026-03-16.md", "r", encoding="utf-8") as f:
    content = f.read()


# ── Markdown → Notion blocks ──
def _rt(text):
    return [{"type": "text", "text": {"content": text[:2000]}}]


def paragraph(text):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _rt(text)}}


def heading2(text):
    return {"object": "block", "type": "heading_2", "heading_2": {"rich_text": _rt(text)}}


def heading3(text):
    return {"object": "block", "type": "heading_3", "heading_3": {"rich_text": _rt(text)}}


def bullet(text):
    return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": _rt(text)}}


def code_block(text):
    return {"object": "block", "type": "code", "code": {"rich_text": _rt(text), "language": "plain text"}}


blocks = []
lines = content.split("\n")
in_code = False
code_buf = []
table_buf = []

for line in lines:
    if line.strip().startswith("```"):
        if in_code:
            blocks.append(code_block("\n".join(code_buf)))
            code_buf = []
            in_code = False
        else:
            in_code = True
        continue
    if in_code:
        code_buf.append(line)
        continue

    stripped = line.strip()
    if not stripped:
        if table_buf:
            blocks.append(code_block("\n".join(table_buf)))
            table_buf = []
        continue

    if stripped.startswith("|") and "|" in stripped[1:]:
        if "---" in stripped:
            continue
        table_buf.append(stripped)
        continue
    elif table_buf:
        blocks.append(code_block("\n".join(table_buf)))
        table_buf = []

    if stripped.startswith("# "):
        continue  # H1 = toggle title
    elif stripped.startswith("### "):
        blocks.append(heading3(stripped[4:]))
    elif stripped.startswith("## "):
        blocks.append(heading2(stripped[3:]))
    elif stripped.startswith("- "):
        blocks.append(bullet(stripped[2:]))
    elif stripped.startswith("> "):
        blocks.append(bullet(stripped[2:]))
    else:
        blocks.append(paragraph(stripped))

if table_buf:
    blocks.append(code_block("\n".join(table_buf)))
if code_buf:
    blocks.append(code_block("\n".join(code_buf)))

print(f"Total blocks: {len(blocks)}")

# ── Create toggle block ──
CHUNK = 99
first_chunk = blocks[:CHUNK]

toggle_title = "2026-03-16 | v7.5.1 (QA 검증 + SQL 캐시 + 최신정보 검색 + 코드 품질)"
toggle_block = {
    "object": "block",
    "type": "toggle",
    "toggle": {
        "rich_text": [{"type": "text", "text": {"content": toggle_title}, "annotations": {"bold": True}}],
        "children": first_chunk,
    },
}

client = httpx.Client(timeout=30)

# Find the "새로운 업데이트가 위에 추가됩니다." block to insert after it
cursor = None
after_id = None
while True:
    url = f"https://api.notion.com/v1/blocks/{PAGE_ID}/children?page_size=100"
    if cursor:
        url += f"&start_cursor={cursor}"
    r = client.get(url, headers=headers)
    data = r.json()
    for b in data.get("results", []):
        if b["type"] == "paragraph":
            rt = b["paragraph"].get("rich_text", [])
            text = "".join(x.get("plain_text", "") for x in rt)
            if "새로운 업데이트가 위에 추가됩니다" in text:
                after_id = b["id"]
                break
    if after_id or not data.get("has_more"):
        break
    cursor = data.get("next_cursor")

if after_id:
    print(f"Inserting after block: {after_id}")
    resp = client.patch(
        f"https://api.notion.com/v1/blocks/{PAGE_ID}/children",
        headers=headers,
        json={"children": [toggle_block], "after": after_id},
    )
else:
    print("Anchor block not found, appending at end")
    resp = client.patch(
        f"https://api.notion.com/v1/blocks/{PAGE_ID}/children",
        headers=headers,
        json={"children": [toggle_block]},
    )
print(f"Toggle created: {resp.status_code}")
if resp.status_code != 200:
    print(resp.text[:500])
    sys.exit(1)

toggle_id = resp.json()["results"][0]["id"]
print(f"Toggle ID: {toggle_id}")

# Append remaining chunks to the toggle
remaining = blocks[CHUNK:]
for i in range(0, len(remaining), CHUNK):
    chunk = remaining[i : i + CHUNK]
    resp2 = client.patch(
        f"https://api.notion.com/v1/blocks/{toggle_id}/children",
        headers=headers,
        json={"children": chunk},
    )
    print(f"  Appended {len(chunk)} blocks: {resp2.status_code}")

print("Done!")
