"""Restructure Notion tester page: insert resolution after each issue, convert to toggles."""
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


def get_all_blocks():
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
    return all_blocks


def delete_block(block_id):
    resp = client.delete(f"https://api.notion.com/v1/blocks/{block_id}", headers=headers)
    return resp.status_code


def get_block_text(block):
    btype = block["type"]
    if btype in ("paragraph", "heading_1", "heading_2", "heading_3", "bulleted_list_item", "callout", "toggle"):
        rich_text = block[btype].get("rich_text", [])
        return "".join(r.get("plain_text", "") for r in rich_text)
    return ""


def get_image_url(block):
    """Get image URL from a block (file or external)."""
    if block["type"] != "image":
        return None, None
    img = block["image"]
    if img.get("type") == "file":
        return "file", img["file"]["url"]
    elif img.get("type") == "external":
        return "external", img["external"]["url"]
    return None, None


# ── Resolution texts for each issue ──
RESOLUTIONS = {
    0: ("해결 ✅", "외국어 질문 → 외국어로 대답",
        "format_answer + direct LLM 프롬프트에 '질문 언어 감지 → 같은 언어 응답' 규칙 추가. "
        "테스트: 스페인어 'cuanto es la venta de chile de 2025' → 스페인어로 답변 확인."),
    1: ("부분 해결 ⚠️", "인도네시아 매출 후속 질문 컨텍스트",
        "대화 맥락 기반 후속 질문 라우팅은 모델 의존적. SQL 캐시 + 컨텍스트 전달 개선으로 정확도 향상. "
        "추가 개선을 위해 대화 맥락 내 국가/플랫폼 자동 추출 로직 검토 필요."),
    2: ("해결 ✅", "틱톡샵 접속방법 → Notion 라우팅",
        "'접속 방법', '접속방법', '사용법', '가이드' + 플랫폼명(틱톡, 쇼피, 아마존 등) 조합 시 Notion으로 라우팅. "
        "테스트: '틱톡샵 접속 방법 알려줘' → Notion에서 VPN 5단계 가이드 정확 응답."),
    3: ("해결 ✅", "회사 소개 명확화",
        "시스템 프롬프트에 SKIN1004 회사 소개 추가: '마다가스카르 센텔라 아시아티카 기반 클린 뷰티 스킨케어 브랜드. "
        "주요 제품: 센텔라 앰플, 크림, 토너. 글로벌 시장: 한국, 미국, 일본, 동남아 등.'"),
    4: ("해결 ✅", "팀별 매출 차트 Y축 라벨",
        "Chart.js horizontal_bar 렌더링 수정. Y축 tick callback에서 인덱스(0,1,2) 대신 실제 팀 이름(B2B1, B2B2...) 표시. "
        "tooltip label callback도 수정하여 정확한 매출 금액 표시."),
    5: ("확인 완료 ℹ️", "CIS 신규 거래처수",
        "'러시아 신규 거래처' = 1개 (데이터 정확). CIS 전체로 조회하면 5개국 10개 거래처 "
        "(우크라이나 3, 아제르바이잔 3, 카자흐스탄 2, 러시아 1, 아르메니아 1). 질문 범위에 따른 차이."),
    6: ("해결 ✅", "대통령 정보 환각 수정",
        "웹검색 키워드에 '대통령', '총리', '선거' 등 추가 + 키워드 체크를 길이 체크보다 먼저 실행. "
        "테스트: '우리나라 대통령이 누구입?' → '이재명, 2025.6.4 취임' Google Search 실시간 응답."),
}

# ── Issue block mapping (issue_idx → list of block indices) ──
# Based on the page structure analysis
ISSUE_BLOCKS = {
    0: [2, 3],           # IMAGE, 외국어 텍스트
    1: [5, 6, 7],        # IMAGE, 인도네시아 텍스트 x2
    2: [9, 10],          # IMAGE, 틱톡샵 텍스트
    3: [12, 13],         # IMAGE, 회사소개 텍스트
    4: [14, 15],         # IMAGE, 차트 텍스트
    5: [17, 18],         # IMAGE, 신규거래처 텍스트
    6: [19, 20],         # IMAGE, 대통령 텍스트
}

# ── Step 1: Read current blocks ──
print("Step 1: Reading current page blocks...")
blocks = get_all_blocks()
print(f"  Found {len(blocks)} blocks")

# ── Step 2: Delete AI summary section (blocks 25+) ──
print("\nStep 2: Deleting bottom AI summary section...")
deleted = 0
for b in blocks:
    bid = b["id"]
    text = get_block_text(b)
    # Delete blocks that are part of the AI summary we added
    if text.startswith("AI \uc790\ub3d9 \uc218\uc815") or text.startswith("AI 자동 수정"):
        # Found the start of AI summary - delete from here
        break

# Delete all blocks from index 25 onwards (the summary section + empty paragraphs before it)
for i in range(len(blocks) - 1, 20, -1):  # Delete from bottom up, stop at index 21
    bid = blocks[i]["id"]
    text = get_block_text(blocks[i]).strip()
    btype = blocks[i]["type"]
    # Delete empty paragraphs and all AI summary blocks
    if i >= 25 or (i >= 21 and not text):
        status = delete_block(bid)
        if status == 200:
            deleted += 1
        time.sleep(0.1)
print(f"  Deleted {deleted} blocks")

# ── Step 3: Update "미해결" heading to "해결 완료" ──
print("\nStep 3: Updating heading...")
heading_id = blocks[0]["id"]
resp = client.patch(
    f"https://api.notion.com/v1/blocks/{heading_id}",
    headers=headers,
    json={
        "heading_1": {
            "rich_text": [rt("해결 완료 ", bold=True), rt("(2026-03-16 AI 자동 수정)", color="gray")],
        }
    },
)
print(f"  Heading updated: {resp.status_code}")

# ── Step 4: Delete empty separator paragraphs ──
print("\nStep 4: Cleaning empty paragraphs...")
empty_ids = []
for idx in [1, 4, 8, 11, 16]:  # Known empty paragraph indices
    if idx < len(blocks):
        bid = blocks[idx]["id"]
        text = get_block_text(blocks[idx]).strip()
        if not text:
            empty_ids.append(bid)
for bid in empty_ids:
    delete_block(bid)
    time.sleep(0.1)
print(f"  Removed {len(empty_ids)} empty paragraphs")

# ── Step 5: Re-read blocks after deletions ──
print("\nStep 5: Re-reading page...")
time.sleep(1)
blocks = get_all_blocks()
print(f"  Now {len(blocks)} blocks")
for i, b in enumerate(blocks):
    text = get_block_text(b) or ("[IMAGE]" if b["type"] == "image" else b["type"])
    print(f"  {i:2d}. [{b['type']:15s}] {text[:60]}")

# ── Step 6: Insert resolution callouts after each issue ──
# We need to find each issue's LAST text block and insert after it
print("\nStep 6: Inserting resolution notes...")

# Map issue descriptions to find them in the current blocks
ISSUE_MARKERS = [
    ("외국어로 물어보면", 0),
    ("인도네시아 매출에 관한 내용", 1),
    ("쇼피 인도네시아 3월", 1),  # second paragraph of issue 1
    ("틱톡샵 접속방법", 2),
    ("우리 회사 뭐하는 회사", 3),
    ("y축은 팀", 4),
    ("신규 거래처수", 5),
    ("LLM 자체 환각", 6),
]

# Find the last block for each issue
issue_last_block = {}
for i, b in enumerate(blocks):
    text = get_block_text(b)
    for marker, issue_idx in ISSUE_MARKERS:
        if marker in text:
            issue_last_block[issue_idx] = b["id"]

print(f"  Found {len(issue_last_block)} issue markers")

# Insert resolution after each issue's last block (reverse order to preserve positions)
for issue_idx in sorted(issue_last_block.keys(), reverse=True):
    block_id = issue_last_block[issue_idx]
    status, title, detail = RESOLUTIONS[issue_idx]

    # Create a toggle block with resolution inside
    if "해결 ✅" in status:
        color = "green_background"
        emoji = "✅"
    elif "부분" in status:
        color = "orange_background"
        emoji = "⚠️"
    else:
        color = "blue_background"
        emoji = "ℹ️"

    resolution_block = {
        "object": "block",
        "type": "toggle",
        "toggle": {
            "rich_text": [
                rt(f"{emoji} {status}: ", bold=True),
                rt(title),
            ],
            "color": color,
            "children": [
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [rt(detail)],
                    },
                }
            ],
        },
    }

    resp = client.patch(
        f"https://api.notion.com/v1/blocks/{PAGE_ID}/children",
        headers=headers,
        json={"children": [resolution_block], "after": block_id},
    )
    if resp.status_code == 200:
        print(f"  Issue {issue_idx}: inserted after block")
    else:
        print(f"  Issue {issue_idx}: FAILED {resp.status_code} - {resp.text[:200]}")
    time.sleep(0.3)

print("\nDone! Page restructured.")
