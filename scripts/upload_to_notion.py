"""
Upload report content to Notion page as structured blocks.
Supports 3 report types:
  - PRD: overwrite (replace full content)
  - Update Log: incremental (prepend new entries)
  - QA Detail Report: daily collection (append new day)

Usage:
  python scripts/upload_to_notion.py              # Upload all
  python scripts/upload_to_notion.py --prd         # PRD only
  python scripts/upload_to_notion.py --updatelog   # Update log only
  python scripts/upload_to_notion.py --qa          # QA report only
"""
import httpx
import json
import re
import os
import sys
import time

# ── Configuration ──
PAGE_ID = "3032b428-3b00-80ae-8241-cedef71fc3be"
NOTION_VERSION = "2022-06-28"
MAX_TEXT_LEN = 1900  # Notion limit is 2000, leave margin
MAX_BLOCKS_PER_CALL = 100
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_token():
    sys.path.insert(0, BASE_DIR)
    from app.config import get_settings
    return get_settings().notion_mcp_token


def headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def rich_text(text: str, bold=False, code=False, color="default") -> list:
    """Create rich_text array, splitting long text into chunks."""
    chunks = []
    while text:
        chunk = text[:MAX_TEXT_LEN]
        text = text[MAX_TEXT_LEN:]
        annotations = {"bold": bold, "code": code, "color": color}
        chunks.append({
            "type": "text",
            "text": {"content": chunk},
            "annotations": annotations,
        })
    return chunks if chunks else [{"type": "text", "text": {"content": ""}}]


def paragraph(text: str, bold=False, color="default") -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": rich_text(text, bold=bold, color=color)},
    }


def heading1(text: str) -> dict:
    return {
        "object": "block",
        "type": "heading_1",
        "heading_1": {"rich_text": rich_text(text)},
    }


def heading2(text: str) -> dict:
    return {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": rich_text(text)},
    }


def heading3(text: str) -> dict:
    return {
        "object": "block",
        "type": "heading_3",
        "heading_3": {"rich_text": rich_text(text)},
    }


def toggle(text: str, children: list = None) -> dict:
    block = {
        "object": "block",
        "type": "toggle",
        "toggle": {"rich_text": rich_text(text, bold=True)},
    }
    if children:
        block["toggle"]["children"] = children[:MAX_BLOCKS_PER_CALL]
    return block


def bulleted(text: str, bold=False) -> dict:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": rich_text(text, bold=bold)},
    }


def divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


def callout(text: str, emoji: str = "📌") -> dict:
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": rich_text(text),
            "icon": {"type": "emoji", "emoji": emoji},
        },
    }


def table_block(rows: list[list[str]]) -> dict:
    """Create a simple table block. rows[0] is header."""
    width = len(rows[0]) if rows else 1
    table_rows = []
    for row in rows:
        cells = []
        for cell in row:
            cells.append(rich_text(str(cell)[:MAX_TEXT_LEN]))
        # Pad if needed
        while len(cells) < width:
            cells.append(rich_text(""))
        table_rows.append({
            "object": "block",
            "type": "table_row",
            "table_row": {"cells": cells},
        })
    return {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": width,
            "has_column_header": True,
            "has_row_header": False,
            "children": table_rows,
        },
    }


def md_to_blocks(md_text: str, max_blocks: int = 95) -> list:
    """Convert markdown text to Notion blocks (simplified)."""
    blocks = []
    lines = md_text.split("\n")
    i = 0
    while i < len(lines) and len(blocks) < max_blocks:
        line = lines[i].rstrip()

        # Skip code fences
        if line.strip().startswith("```"):
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                i += 1
            i += 1
            continue

        # Empty line
        if not line.strip():
            i += 1
            continue

        # Headers
        if line.startswith("### "):
            blocks.append(heading3(clean_md(line[4:])))
        elif line.startswith("## "):
            blocks.append(heading2(clean_md(line[3:])))
        elif line.startswith("# "):
            blocks.append(heading1(clean_md(line[2:])))
        # Table
        elif "|" in line and line.strip().startswith("|"):
            table_rows = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].split("|")[1:-1]]
                if not all(re.match(r"^[-:]+$", c) for c in cells):
                    table_rows.append([clean_md(c) for c in cells])
                i += 1
            if table_rows:
                blocks.append(table_block(table_rows))
            continue
        # Divider
        elif line.strip() == "---":
            blocks.append(divider())
        # Blockquote
        elif line.startswith("> "):
            blocks.append(callout(clean_md(line[2:]), "💡"))
        # Bullet
        elif line.lstrip().startswith("- ") or line.lstrip().startswith("* "):
            blocks.append(bulleted(clean_md(line.lstrip().lstrip("-* "))))
        # Numbered list
        elif re.match(r"^\s*\d+\.\s", line):
            text = re.sub(r"^\s*\d+\.\s*", "", line)
            blocks.append(bulleted(clean_md(text)))
        # Regular text
        else:
            blocks.append(paragraph(clean_md(line)))

        i += 1

    return blocks


def clean_md(text: str) -> str:
    """Remove markdown formatting."""
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return text.strip()


# ── API helpers ──

def append_blocks(token: str, parent_id: str, blocks: list):
    """Append blocks to a Notion page/block, batching if needed."""
    hdrs = headers(token)
    for start in range(0, len(blocks), MAX_BLOCKS_PER_CALL):
        batch = blocks[start:start + MAX_BLOCKS_PER_CALL]
        r = httpx.patch(
            f"https://api.notion.com/v1/blocks/{parent_id}/children",
            headers=hdrs,
            json={"children": batch},
            timeout=30,
        )
        if r.status_code != 200:
            print(f"  ERROR appending blocks: {r.status_code} {r.text[:300]}")
            return False
        time.sleep(0.3)  # Rate limit
    return True


def get_children(token: str, block_id: str) -> list:
    """Get all child blocks of a page/block."""
    hdrs = headers(token)
    results = []
    cursor = None
    while True:
        url = f"https://api.notion.com/v1/blocks/{block_id}/children?page_size=100"
        if cursor:
            url += f"&start_cursor={cursor}"
        r = httpx.get(url, headers=hdrs, timeout=15)
        if r.status_code != 200:
            break
        data = r.json()
        results.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return results


def delete_block(token: str, block_id: str):
    """Delete a block."""
    for attempt in range(3):
        try:
            r = httpx.delete(
                f"https://api.notion.com/v1/blocks/{block_id}",
                headers=headers(token),
                timeout=30,
            )
            return r.status_code == 200
        except httpx.ReadTimeout:
            time.sleep(1)
    return False


def clear_page(token: str, page_id: str):
    """Remove all blocks from a page."""
    children = get_children(token, page_id)
    for child in children:
        delete_block(token, child["id"])
        time.sleep(0.3)
    print(f"  Cleared {len(children)} blocks")


# ── Report builders ──

def build_prd_blocks() -> list:
    """Build PRD section blocks from the latest PRD markdown."""
    filepath = os.path.join(BASE_DIR, "docs", "SKIN1004_Enterprise_AI_PRD_v5.md")
    if not os.path.exists(filepath):
        return [paragraph("PRD file not found")]

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Extract key sections (not the full document - too large)
    blocks = []

    # Version info
    blocks.append(callout("Version 6.0.2 | 2026-02-12 | DB Team / Data Analytics", "📋"))
    blocks.append(paragraph(""))

    # Section 1: Project Overview
    sec1 = extract_section(content, "# 1. Project Overview", "# 2.")
    if sec1:
        blocks.append(heading2("1. Project Overview"))
        blocks.extend(md_to_blocks(sec1, max_blocks=15))

    # Section 2: Architecture (summary)
    blocks.append(heading2("2. System Architecture"))
    blocks.append(paragraph(
        "Orchestrator-Worker 멀티 에이전트 구조. "
        "매출 데이터 → Text-to-SQL, 사내 문서 → Notion Direct API, "
        "개인 업무 → Google Workspace OAuth2. "
        "키워드 우선 분류 + LLM 라우팅으로 질문 유형 자동 판별."
    ))

    # Section 3: Routing
    blocks.append(heading2("3. Query Routing"))
    blocks.append(table_block([
        ["Route", "Trigger", "Handler", "LLM"],
        ["bigquery", "매출, 판매량, 수량 등", "SQL Agent → BigQuery", "Flash (SQL) + Pro/Claude (답변)"],
        ["notion", "노션, 문서, 가이드 등", "Notion Agent → API", "Flash (검색) + Pro/Claude (답변)"],
        ["gws", "메일, 드라이브, 캘린더 등", "GWS Agent → OAuth2", "ReAct Agent"],
        ["multi", "매출+문서 복합", "BQ + Notion/GWS 병렬", "Pro/Claude (종합)"],
        ["direct", "일반 질문, 인사", "Direct LLM", "Pro/Claude"],
    ]))

    # Section: Tech Stack
    blocks.append(heading2("4. Tech Stack"))
    blocks.append(table_block([
        ["Layer", "Technology"],
        ["LLM", "Gemini 2.5 Pro + Claude Sonnet 4.5 (dual)"],
        ["Lightweight", "Gemini 2.5 Flash (SQL gen, routing, chart)"],
        ["Orchestration", "LangGraph + Custom Orchestrator"],
        ["API Server", "FastAPI (port 8100)"],
        ["Database", "BigQuery (Sales + Vector Search)"],
        ["Frontend", "Open WebUI (Docker port 3000)"],
        ["Auth", "Google SSO + per-user OAuth2"],
    ]))

    # Performance
    blocks.append(heading2("5. Performance"))
    blocks.append(table_block([
        ["Metric", "Before", "After"],
        ["SQL Query Response", "38-42s", "11-13s"],
        ["Notion Search", "7min+ (full crawl)", "2-3s (allowlist)"],
        ["Classification", "LLM every time", "Keyword-first + LLM fallback"],
        ["Answer + Chart", "Sequential", "Parallel (ThreadPoolExecutor)"],
    ]))

    blocks.append(paragraph(""))
    blocks.append(paragraph(
        "전체 PRD 문서: docs/SKIN1004_Enterprise_AI_PRD_v5.md (로컬 파일)",
    ))

    return blocks


def build_updatelog_blocks() -> list:
    """Build update log blocks from markdown files."""
    blocks = []

    # Find all update log files, sorted descending
    log_files = []
    docs_dir = os.path.join(BASE_DIR, "docs")
    for f in os.listdir(docs_dir):
        if f.startswith("update_log_") and f.endswith(".md"):
            log_files.append(f)
    log_files.sort(reverse=True)

    # Also check for test reports
    report_files = []
    for f in os.listdir(docs_dir):
        if f.startswith("test_report") and f.endswith(".md"):
            report_files.append(f)
    report_files.sort(reverse=True)

    # Latest comprehensive test report
    if report_files:
        filepath = os.path.join(docs_dir, report_files[0])
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        # Extract just the summary section
        summary = extract_section(content, "## 1. ", "## 2.")
        children = md_to_blocks(summary, max_blocks=30) if summary else []
        blocks.append(toggle(
            f"2026-02-12 | v6.1 종합 QA 테스트 (112개 질문, 92% 성공률)",
            children or [paragraph("See test_report_comprehensive_2026-02-12.md")]
        ))

    # Update logs
    for lf in log_files:
        filepath = os.path.join(docs_dir, lf)
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        # Extract date and version from content
        date_match = re.search(r"(\d{4}년 \d+월 \d+일)", content)
        ver_match = re.search(r"\(v([\d.]+)\)", content)
        title_match = re.search(r"^# (.+)$", content, re.MULTILINE)

        date_str = date_match.group(1) if date_match else lf.replace("update_log_", "").replace(".md", "")
        ver_str = f"v{ver_match.group(1)}" if ver_match else ""

        # Get first 20 blocks of content
        children = md_to_blocks(content, max_blocks=25)
        title = f"{date_str} | {ver_str}" if ver_str else date_str
        blocks.append(toggle(title, children or [paragraph(content[:500])]))

    return blocks


def build_qa_blocks() -> list:
    """Build QA detail report blocks (summary + per-round stats)."""
    blocks = []

    # Overall summary
    blocks.append(table_block([
        ["Round", "Domain", "Queries", "OK", "Issues"],
        ["Round 1", "BigQuery", "20", "19", "1 SHORT"],
        ["Round 1", "Notion", "20", "18", "1 EXCEPTION, 1 MISS"],
        ["Round 1", "GWS", "15", "13", "1 EXCEPTION, 1 EMPTY"],
        ["Round 2", "BigQuery", "15", "15", "-"],
        ["Round 2", "Notion", "12", "10", "1 EXCEPTION, 1 MISS"],
        ["Round 2", "GWS", "10", "8", "1 EXCEPTION, 1 EMPTY"],
        ["Round 3", "Edge Cases", "15", "15", "-"],
        ["Regression", "Bug Fixes", "5", "5", "-"],
        ["TOTAL", "", "112", "103", "9 (all fixed)"],
    ]))
    blocks.append(paragraph(""))

    # Parse each result file for summary
    result_files = [
        ("test_team_bigquery_result.txt", "Round 1 - BigQuery (20 queries)"),
        ("test_team_notion_result.txt", "Round 1 - Notion (20 queries)"),
        ("test_team_gws_result.txt", "Round 1 - GWS (15 queries)"),
        ("test_team_r2_bigquery_result.txt", "Round 2 - BigQuery (15 queries)"),
        ("test_team_r2_notion_result.txt", "Round 2 - Notion (12 queries)"),
        ("test_team_r2_gws_result.txt", "Round 2 - GWS (10 queries)"),
        ("test_team_r3_edge_result.txt", "Round 3 - Edge Cases (15 queries)"),
        ("test_regression_result.txt", "Regression - Bug Fixes (5 queries)"),
    ]

    for filename, title in result_files:
        filepath = os.path.join(BASE_DIR, filename)
        if not os.path.exists(filepath):
            continue

        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        # Extract individual tests as toggle children
        children = []
        # Match [ID]... then Q: on same or following lines (up to 3 lines gap)
        pattern = r"\[([A-Z0-9\-]+)\]([^\n]*)\n(?:[^\n]*\n){0,3}?Q:\s*([^\n]+)"
        status_pattern = r"Status:\s*([^\n]+)"
        for m in re.finditer(pattern, content):
            tid = m.group(1).strip()
            cat = m.group(2).strip()
            question = m.group(3).strip()
            # Find status line after this match
            remaining = content[m.end():]
            sm = re.search(status_pattern, remaining[:500])
            status = sm.group(1).strip() if sm else ""
            status_short = "OK" if "OK" in status else ("FAIL" if "EXCEPTION" in status else "OTHER")
            time_m = re.search(r"([\d.]+)s", status)
            time_s = f"{float(time_m.group(1)):.0f}s" if time_m else ""
            icon = "✅" if status_short == "OK" else "❌"
            children.append(bulleted(f"{icon} [{tid}] {question}  ({time_s})"))

        if not children:
            children = [paragraph("No entries parsed")]

        blocks.append(toggle(title, children[:MAX_BLOCKS_PER_CALL]))

    blocks.append(paragraph(""))
    blocks.append(paragraph(
        "전체 상세 보고서 (질문+답변 전문): docs/qa_detail_report_2026-02-12.pdf (71페이지)"
    ))

    return blocks


def extract_section(content: str, start_marker: str, end_marker: str) -> str:
    """Extract a section between two markers."""
    start = content.find(start_marker)
    if start < 0:
        return ""
    end = content.find(end_marker, start + len(start_marker))
    if end < 0:
        return content[start:]
    return content[start:end]


# ── Main ──

def main():
    token = get_token()
    args = sys.argv[1:]
    do_all = not args
    do_prd = "--prd" in args or do_all
    do_log = "--updatelog" in args or do_all
    do_qa = "--qa" in args or do_all

    print(f"Target page: {PAGE_ID}")

    # Clear existing content
    print("Clearing page...")
    clear_page(token, PAGE_ID)
    time.sleep(0.5)

    # Build top-level structure
    all_blocks = []

    # Page description
    all_blocks.append(callout(
        "SKIN1004 AI Agent 개발 리포트 아카이브\n"
        "PRD: 내용 수정 시 덮어쓰기 | Update Log: 증분 누적 | QA Report: 매일 하루치 모음",
        "🤖"
    ))
    all_blocks.append(paragraph(""))

    # Append top-level blocks first
    print("Adding page header...")
    append_blocks(token, PAGE_ID, all_blocks)
    time.sleep(0.3)

    # ── PRD Section ──
    if do_prd:
        print("Building PRD section...")
        prd_blocks = build_prd_blocks()
        section = [
            heading1("📋 PRD (Product Requirements Document)"),
        ]
        append_blocks(token, PAGE_ID, section)
        time.sleep(0.3)

        # PRD content as a toggle
        prd_toggle = toggle("PRD v6.2.0 (2026-02-12) - 전체 내용 보기", prd_blocks[:95])
        append_blocks(token, PAGE_ID, [prd_toggle])
        time.sleep(0.3)

        # If more blocks, add as additional toggle children
        if len(prd_blocks) > 95:
            remaining = prd_blocks[95:]
            append_blocks(token, PAGE_ID, remaining)

        append_blocks(token, PAGE_ID, [divider()])
        print(f"  PRD: {len(prd_blocks)} blocks added")

    # ── Update Log Section ──
    if do_log:
        print("Building Update Log section...")
        log_blocks = build_updatelog_blocks()
        section = [
            heading1("📝 Update Log"),
            paragraph("새로운 업데이트가 위에 추가됩니다."),
        ]
        append_blocks(token, PAGE_ID, section)
        time.sleep(0.3)
        append_blocks(token, PAGE_ID, log_blocks)
        time.sleep(0.3)
        append_blocks(token, PAGE_ID, [divider()])
        print(f"  Update Log: {len(log_blocks)} blocks added")

    # ── QA Detail Report Section ──
    if do_qa:
        print("Building QA Report section...")
        qa_blocks = build_qa_blocks()
        section = [
            heading1("🧪 QA Test Reports"),
            paragraph("매일 하루치 테스트 결과를 모아서 기록합니다."),
            heading2("2026-02-12 종합 QA 테스트 (112 queries)"),
        ]
        append_blocks(token, PAGE_ID, section)
        time.sleep(0.3)
        append_blocks(token, PAGE_ID, qa_blocks)
        print(f"  QA Report: {len(qa_blocks)} blocks added")

    print("\nDone! Check Notion page.")


if __name__ == "__main__":
    main()
