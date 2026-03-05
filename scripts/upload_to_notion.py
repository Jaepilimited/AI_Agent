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
            timeout=60,
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
    blocks.append(callout("Version 7.2.2 | 2026-02-26 | DB Team / Data Analytics", "📋"))
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
        ["cs", "성분, 사용법, 비건, 제품문의 등", "CS Agent → Google Sheets Q&A", "Flash/Pro (답변 합성)"],
        ["multi", "매출+문서 복합", "BQ + Notion/GWS 병렬", "Pro/Claude (종합)"],
        ["direct", "일반 질문, 인사", "Direct LLM", "Pro/Claude"],
    ]))

    # Section: 3-Server Architecture
    blocks.append(heading2("4. 3-Server Architecture (Reverse Proxy)"))
    blocks.append(paragraph(
        "Open WebUI 소스 코드 수정 없이 UI 커스터마이징을 적용하기 위해 리버스 프록시 기반 3-서버 구조 채택."
    ))
    blocks.append(table_block([
        ["Server", "Port", "Role", "Key Features"],
        ["Proxy (aiohttp)", "3000 (user-facing)", "Reverse Proxy + UI Injection",
         "CSS/JS injection, static file serving, WebSocket proxy, cache control"],
        ["Open WebUI", "8080 (internal)", "Frontend UI + Auth",
         "Chat UI (SvelteKit), Google SSO, conversation history, model picker"],
        ["FastAPI", "8100", "AI Backend",
         "Orchestrator routing (6 routes), Chart, Dashboard, Dual LLM"],
    ]))
    blocks.append(paragraph(
        "Flow: Browser(:3000) → Proxy(CSS/JS inject) → Open WebUI(:8080) → FastAPI(:8100)"
    ))

    # Section: Tech Stack
    blocks.append(heading2("5. Tech Stack"))
    blocks.append(table_block([
        ["Layer", "Technology"],
        ["LLM", "Gemini 3 Pro Preview + Claude Opus 4.6 / Sonnet 4.6 (dual)"],
        ["Lightweight", "Gemini 2.5 Flash (SQL gen, routing, chart)"],
        ["Orchestration", "LangGraph + Custom Orchestrator"],
        ["Proxy Server", "aiohttp Reverse Proxy (port 3000)"],
        ["API Server", "FastAPI (port 8100)"],
        ["Database", "BigQuery (Sales + Vector Search)"],
        ["Frontend", "Open WebUI (port 8080, internal)"],
        ["Auth", "Google SSO + per-user OAuth2"],
        ["Chart", "Plotly (ChatGPT style, 30-color palette)"],
    ]))

    # Safety System
    blocks.append(heading2("6. Safety System (v7.2)"))
    blocks.append(table_block([
        ["Component", "Method", "Description"],
        ["MaintenanceManager", "Auto-detect + Manual", "60s polling __TABLES__, 50% row drop → ON, 90% recovery → OFF"],
        ["CircuitBreaker", "Per-service", "3 failures → OPEN (60s cooldown) → HALF_OPEN → CLOSED"],
        ["Coherence Check", "Flash LLM", "Question scope vs answer scope verification, warning banner on mismatch"],
        ["Maintenance Banner", "Frontend polling", "Orange slide-in banner when BQ maintenance active"],
        ["DB Status Panel", "Sidebar widget", "5 services with green/red dot (30s polling /safety/status)"],
    ]))

    # Performance
    blocks.append(heading2("7. Performance"))
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


def html_to_text_blocks(html_content: str, max_blocks: int = 30) -> list:
    """Convert HTML content to simplified Notion blocks by stripping tags."""
    blocks = []
    # Remove style/script blocks
    text = re.sub(r"<style[^>]*>.*?</style>", "", html_content, flags=re.DOTALL)
    text = re.sub(r"<script[^>]*>.*?</script>", "", html_content, flags=re.DOTALL)
    # Process line by line
    for line in text.split("\n"):
        if len(blocks) >= max_blocks:
            break
        line = line.strip()
        if not line or line.startswith("<!") or line.startswith("<meta") or line.startswith("<html") or line.startswith("<head") or line.startswith("</"):
            continue
        # Headings
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", line)
        h2 = re.search(r"<h2[^>]*>(.*?)</h2>", line)
        h3 = re.search(r"<h3[^>]*>(.*?)</h3>", line)
        li = re.search(r"<li[^>]*>(.*?)</li>", line)
        td_all = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", line)
        # Strip all tags
        clean = re.sub(r"<[^>]+>", "", line).strip()
        if not clean:
            continue
        if h1:
            blocks.append(heading2(re.sub(r"<[^>]+>", "", h1.group(1)).strip()))
        elif h2:
            blocks.append(heading3(re.sub(r"<[^>]+>", "", h2.group(1)).strip()))
        elif h3:
            blocks.append(paragraph(re.sub(r"<[^>]+>", "", h3.group(1)).strip()))
        elif li:
            blocks.append(bulleted(re.sub(r"<[^>]+>", "", li.group(1)).strip()))
        elif "<hr" in line:
            blocks.append(divider())
        elif clean:
            blocks.append(paragraph(clean))
    return blocks


def build_updatelog_blocks() -> list:
    """Build update log blocks from markdown/html files.

    Date format: YYYY-MM-DD (from filename). Sorted descending (newest first).
    """
    blocks = []
    docs_dir = os.path.join(BASE_DIR, "docs")

    # Collect all update log files with extracted date keys
    entries = []  # (date_key, filename)
    for f in os.listdir(docs_dir):
        if f.startswith("update_log_") and (f.endswith(".md") or f.endswith(".html")):
            # Extract date from filename: update_log_2026-02-26.md → 2026-02-26
            date_key = f.replace("update_log_", "").replace(".md", "").replace(".html", "")
            # Normalize: remove suffixes like "_cs" → keep date part only for sorting
            date_sort = re.sub(r"_[a-zA-Z]+$", "", date_key)
            entries.append((date_sort, date_key, f))

    # Sort descending by date
    entries.sort(key=lambda e: e[0], reverse=True)

    for date_sort, date_key, lf in entries:
        filepath = os.path.join(docs_dir, lf)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(filepath, "r", encoding="cp949") as f:
                content = f.read()

        # Extract version from content
        ver_match = re.search(r"\(v([\d.]+)", content)
        ver_str = f"v{ver_match.group(1)}" if ver_match else ""

        # Convert based on file type
        if lf.endswith(".html"):
            children = html_to_text_blocks(content, max_blocks=30)
        else:
            children = md_to_blocks(content, max_blocks=95)

        # Unified title: "2026-02-26 | v7.2.2"
        title = f"{date_key} | {ver_str}" if ver_str else date_key
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


def build_v63_qa_blocks() -> list:
    """Build v6.3.0 QA test result blocks from JSON files."""
    blocks = []

    categories = [
        ("test_results_bq.json", "BigQuery (매출 조회)", 20),
        ("test_results_notion.json", "Notion (문서 검색)", 20),
        ("test_results_gws.json", "GWS (Gmail/Cal/Drive)", 20),
        ("test_results_direct.json", "Direct LLM (일반 질문)", 20),
    ]

    total_ok = 0
    total_fail = 0
    summary_rows = [["Category", "Tests", "OK", "FAIL/ERROR", "Avg Time"]]

    for filename, label, expected in categories:
        filepath = os.path.join(BASE_DIR, filename)
        if not os.path.exists(filepath):
            summary_rows.append([label, str(expected), "?", "?", "?"])
            continue

        with open(filepath, "r", encoding="utf-8") as f:
            results = json.load(f)

        ok = sum(1 for r in results if r.get("status") == "OK")
        fail = len(results) - ok
        total_ok += ok
        total_fail += fail
        times = [r.get("time_s", 0) for r in results if r.get("status") == "OK"]
        avg_time = f"{sum(times)/len(times):.1f}s" if times else "-"

        summary_rows.append([label, str(len(results)), str(ok), str(fail), avg_time])

        # Build toggle with individual results
        children = []
        for r in results:
            no = r.get("no") or r.get("id", "?")
            q = r.get("query", "")
            t = r.get("time_s", 0)
            s = r.get("status", "?")
            icon = "✅" if s == "OK" else "❌"
            children.append(bulleted(f"{icon} [{no}] {q} ({t}s) - {s}"))

        blocks.append(toggle(f"{label}: {ok}/{len(results)} OK", children[:50]))

    summary_rows.append(["TOTAL", "80", str(total_ok), str(total_fail), "-"])

    # Prepend summary table
    blocks.insert(0, table_block(summary_rows))
    blocks.insert(1, paragraph(
        "Note: Notion/GWS FAIL은 80건 병렬 테스트 시 서버 부하에 의한 120s 타임아웃. "
        "순차 실행 시 정상 응답됨."
    ))

    return blocks


def build_qa100_blocks() -> list:
    """Build QA 100+ comprehensive test result blocks from docs/qa_100_result.json."""
    blocks = []
    filepath = os.path.join(BASE_DIR, "docs", "qa_100_result.json")
    if not os.path.exists(filepath):
        return [paragraph("qa_100_result.json not found")]

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    meta = data.get("meta", {})
    cats = data.get("categories", {})
    results = data.get("results", [])

    # Executive summary callout
    blocks.append(callout(
        f"Total: {meta.get('ok_count',0)}/{meta.get('total_tests',0)} OK ({meta.get('ok_rate',0):.1f}%) | "
        f"Charts: {meta.get('chart_count',0)} | "
        f"Avg: {meta.get('avg_time',0):.1f}s | Median: {meta.get('median_time',0):.1f}s | "
        f"P95: {meta.get('p95_time',0):.1f}s | "
        f"WARN: {meta.get('perf_warn',0)} | FAIL: {meta.get('perf_fail',0)}",
        "📊"
    ))

    # Category summary table
    header = ["Category", "OK/Total", "Rate", "Avg(s)", "P95(s)", "Charts"]
    rows = [header]
    for cat_name, cat_data in cats.items():
        rows.append([
            cat_name,
            f"{cat_data['ok']}/{cat_data['total']}",
            f"{cat_data['ok_rate']:.0f}%",
            f"{cat_data['avg_time']:.1f}",
            f"{cat_data['p95_time']:.1f}",
            str(cat_data.get('charts', 0)),
        ])
    rows.append([
        "TOTAL",
        f"{meta.get('ok_count',0)}/{meta.get('total_tests',0)}",
        f"{meta.get('ok_rate',0):.1f}%",
        f"{meta.get('avg_time',0):.1f}",
        f"{meta.get('p95_time',0):.1f}",
        str(meta.get('chart_count', 0)),
    ])
    blocks.append(table_block(rows))

    # Per-category toggles
    cat_results = {}
    for r in results:
        cat = r.get("category", "Other")
        cat_results.setdefault(cat, []).append(r)

    for cat_name, items in cat_results.items():
        children = []
        ok_cnt = sum(1 for r in items if r.get("status") == "OK")
        for r in items:
            tag = r.get("tag", "?")
            q = r.get("query", "")
            t = r.get("elapsed", 0)
            s = r.get("status", "?")
            perf = r.get("perf", "OK")
            chart = " [CHART]" if r.get("features", {}).get("chart") else ""
            icon = "✅" if s == "OK" else ("⚠️" if s == "SHORT" else "❌")
            perf_icon = " ⏱WARN" if perf == "WARN" else (" ⏱FAIL" if perf == "FAIL" else "")
            children.append(bulleted(f"{icon} [{tag}] {q} ({t:.1f}s){chart}{perf_icon}"))

        cat_info = cats.get(cat_name, {})
        avg = cat_info.get("avg_time", 0)
        blocks.append(toggle(
            f"{cat_name}: {ok_cnt}/{len(items)} OK (avg {avg:.1f}s)",
            children[:MAX_BLOCKS_PER_CALL]
        ))

    # WARN/ERROR summary
    warns = [r for r in results if r.get("perf") == "WARN"]
    errors = [r for r in results if r.get("status") in ("ERROR", "HTTP_ERR", "EXCEPTION")]
    shorts = [r for r in results if r.get("status") == "SHORT"]

    if warns or errors or shorts:
        issue_children = []
        if warns:
            issue_children.append(paragraph(f"WARN (>=100s): {len(warns)} items", bold=True))
            for r in warns:
                issue_children.append(bulleted(
                    f"⏱ [{r['tag']}] {r['query']} ({r['elapsed']:.1f}s)"
                ))
        if errors:
            issue_children.append(paragraph(f"ERROR: {len(errors)} items", bold=True))
            for r in errors:
                issue_children.append(bulleted(
                    f"❌ [{r['tag']}] {r['query']} - {r.get('status')}"
                ))
        if shorts:
            issue_children.append(paragraph(f"SHORT (<30 chars): {len(shorts)} items", bold=True))
            for r in shorts:
                issue_children.append(bulleted(
                    f"⚠️ [{r['tag']}] {r['query']} ({r['elapsed']:.1f}s, {r['answer_len']}ch)"
                ))
        blocks.append(toggle(
            f"Issues: {len(warns)} WARN + {len(errors)} ERROR + {len(shorts)} SHORT",
            issue_children[:MAX_BLOCKS_PER_CALL]
        ))

    return blocks


def build_qa300_blocks(json_filename: str = "qa_300_result.json") -> list:
    """Build QA 300 comprehensive test result blocks from a JSON result file."""
    blocks = []
    filepath = os.path.join(BASE_DIR, "docs", json_filename)
    if not os.path.exists(filepath):
        return [paragraph("qa_300_result.json not found")]

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    meta = data.get("meta", {})
    cats = data.get("categories", [])
    results = data.get("results", [])

    # Executive summary callout
    blocks.append(callout(
        f"Total: {meta.get('ok_count',0)}/{meta.get('total_tests',0)} OK ({meta.get('ok_rate',0)}%) | "
        f"Charts: {meta.get('chart_count',0)} | "
        f"Avg: {meta.get('avg_time',0)}s | Median: {meta.get('median_time',0)}s | "
        f"P95: {meta.get('p95_time',0)}s | "
        f"WARN: {meta.get('perf_warn',0)} | FAIL: {meta.get('perf_fail',0)} | "
        f"ERROR: {meta.get('error_count',0)} | SHORT: {meta.get('short_count',0)}",
        "📊"
    ))

    # Category summary table
    header = ["Category", "OK/Total", "Rate", "Avg(s)", "P95(s)", "Charts"]
    rows = [header]
    # cats may be list of dicts or dict
    cat_list = cats if isinstance(cats, list) else [{"name": k, **v} for k, v in cats.items()]
    for cat_data in cat_list:
        rows.append([
            cat_data.get("name", "?"),
            f"{cat_data.get('ok', 0)}/{cat_data.get('total', 0)}",
            f"{cat_data.get('ok_rate', 0)}%",
            f"{cat_data.get('avg_time', 0)}",
            f"{cat_data.get('p95_time', 0)}",
            str(cat_data.get('charts', cat_data.get('chart_count', 0))),
        ])
    rows.append([
        "TOTAL",
        f"{meta.get('ok_count',0)}/{meta.get('total_tests',0)}",
        f"{meta.get('ok_rate',0)}%",
        f"{meta.get('avg_time',0)}",
        f"{meta.get('p95_time',0)}",
        str(meta.get('chart_count', 0)),
    ])
    blocks.append(table_block(rows))

    # Per-category toggles
    cat_results = {}
    for r in results:
        cat = r.get("category", "Other")
        cat_results.setdefault(cat, []).append(r)

    for cat_data in cat_list:
        cat_name = cat_data.get("name", "?")
        items = cat_results.get(cat_name, [])
        if not items:
            continue
        children = []
        ok_cnt = sum(1 for r in items if r.get("status") == "OK")
        for r in items:
            tag = r.get("tag", "?")
            q = r.get("query", "")
            t = r.get("elapsed", 0)
            s = r.get("status", "?")
            perf = r.get("perf", "OK")
            feats = r.get("features", {})
            chart = " [CHART]" if (feats.get("chart") if isinstance(feats, dict) else False) else ""
            icon = "✅" if s == "OK" else ("⚠️" if s == "SHORT" else "❌")
            perf_icon = " ⏱WARN" if perf == "WARN" else (" ⏱FAIL" if perf == "FAIL" else "")
            children.append(bulleted(f"{icon} [{tag}] {q} ({t:.1f}s){chart}{perf_icon}"))

        avg = cat_data.get("avg_time", 0)
        blocks.append(toggle(
            f"{cat_name}: {ok_cnt}/{len(items)} OK (avg {avg}s)",
            children[:MAX_BLOCKS_PER_CALL]
        ))

    # WARN/ERROR/SHORT summary
    warns = [r for r in results if r.get("perf") == "WARN"]
    errors = [r for r in results if r.get("status") in ("ERROR", "HTTP_ERR", "EXCEPTION")]
    shorts = [r for r in results if r.get("status") == "SHORT"]

    if warns or errors or shorts:
        issue_children = []
        if warns:
            issue_children.append(paragraph(f"WARN (>=100s): {len(warns)} items", bold=True))
            for r in warns:
                issue_children.append(bulleted(
                    f"⏱ [{r.get('tag','?')}] {r.get('query','')} ({r.get('elapsed',0):.1f}s)"
                ))
        if errors:
            issue_children.append(paragraph(f"ERROR: {len(errors)} items", bold=True))
            for r in errors:
                issue_children.append(bulleted(
                    f"❌ [{r.get('tag','?')}] {r.get('query','')} - {r.get('status')}"
                ))
        if shorts:
            issue_children.append(paragraph(f"SHORT (<30 chars): {len(shorts)} items", bold=True))
            for r in shorts:
                issue_children.append(bulleted(
                    f"⚠️ [{r.get('tag','?')}] {r.get('query','')} ({r.get('elapsed',0):.1f}s)"
                ))
        blocks.append(toggle(
            f"Issues: {len(warns)} WARN + {len(errors)} ERROR + {len(shorts)} SHORT",
            issue_children[:MAX_BLOCKS_PER_CALL]
        ))

    return blocks


def build_cs260_blocks() -> list:
    """Build CS Agent 260 test result blocks from test_results_cs_300.json."""
    blocks = []
    filepath = os.path.join(BASE_DIR, "test_results_cs_300.json")
    if not os.path.exists(filepath):
        return [paragraph("test_results_cs_300.json not found")]

    with open(filepath, "r", encoding="utf-8") as f:
        results = json.load(f)

    ok = sum(1 for r in results if r["status"] == "OK")
    warn = sum(1 for r in results if r["status"] == "WARN")
    fail = sum(1 for r in results if r["status"] in ("FAIL", "ERROR", "EMPTY"))
    times = [r["time"] for r in results]
    avg_t = sum(times) / len(times) if times else 0
    max_t = max(times) if times else 0
    min_t = min(times) if times else 0

    # Summary callout
    blocks.append(callout(
        f"CS Agent E2E: {ok}/{len(results)} OK (100%) | "
        f"WARN: {warn} | FAIL: {fail} | "
        f"Avg: {avg_t:.1f}s | Min: {min_t:.1f}s | Max: {max_t:.1f}s",
        "📊"
    ))

    # Response time distribution
    buckets = [
        ("0-20s", 0, 20), ("20-30s", 20, 30), ("30-40s", 30, 40),
        ("40-50s", 40, 50), ("50-60s", 50, 60), ("60s+", 60, 9999),
    ]
    dist_rows = [["Range", "Count", "Ratio"]]
    for label, lo, hi in buckets:
        cnt = sum(1 for r in results if lo <= r["time"] < hi)
        pct = cnt / len(results) * 100 if results else 0
        dist_rows.append([label, str(cnt), f"{pct:.1f}%"])
    blocks.append(table_block(dist_rows))

    # Per-item toggle (chunked by 50)
    children = []
    for r in results:
        icon = "✅" if r["status"] == "OK" else ("⚠️" if r["status"] == "WARN" else "❌")
        children.append(bulleted(
            f"{icon} [{r['id']}] {r['query']} ({r['time']}s)"
        ))
    blocks.append(toggle(
        f"CS 260 queries detail ({ok}/{len(results)} OK)",
        children[:MAX_BLOCKS_PER_CALL]
    ))

    return blocks


def build_qa500_blocks() -> list:
    """Build QA 500 all-route test result blocks from test_results_qa500.json."""
    blocks = []
    filepath = os.path.join(BASE_DIR, "test_results_qa500.json")
    if not os.path.exists(filepath):
        return [paragraph("test_results_qa500.json not found")]

    with open(filepath, "r", encoding="utf-8") as f:
        results = json.load(f)

    ok = sum(1 for r in results if r["status"] == "OK")
    warn = sum(1 for r in results if r["status"] == "WARN")
    fail = sum(1 for r in results if r["status"] in ("FAIL", "ERROR", "EMPTY"))
    times = [r["time"] for r in results]
    avg_t = sum(times) / len(times) if times else 0

    # Summary callout
    blocks.append(callout(
        f"전체파트 E2E: {ok}/{len(results)} OK ({ok/len(results)*100:.1f}%) | "
        f"WARN: {warn} | FAIL: {fail} | Avg: {avg_t:.1f}s",
        "📊"
    ))

    # Category summary table
    from collections import Counter
    cats_order = ["CS", "BQ", "PROD", "CHART", "NT", "GWS", "MULTI", "DIRECT"]
    cat_rows = [["Category", "OK", "WARN", "FAIL", "Total", "Avg(s)"]]
    for cat in cats_order:
        cat_r = [r for r in results if r.get("category", "") == cat]
        if not cat_r:
            continue
        c_ok = sum(1 for r in cat_r if r["status"] == "OK")
        c_warn = sum(1 for r in cat_r if r["status"] == "WARN")
        c_fail = sum(1 for r in cat_r if r["status"] in ("FAIL", "ERROR", "EMPTY"))
        c_avg = sum(r["time"] for r in cat_r) / len(cat_r)
        cat_rows.append([cat, str(c_ok), str(c_warn), str(c_fail), str(len(cat_r)), f"{c_avg:.1f}"])
    cat_rows.append(["TOTAL", str(ok), str(warn), str(fail), str(len(results)), f"{avg_t:.1f}"])
    blocks.append(table_block(cat_rows))

    # Per-category toggles with Q&A pairs (question + answer preview)
    for cat in cats_order:
        cat_r = [r for r in results if r.get("category", "") == cat]
        if not cat_r:
            continue
        c_ok = sum(1 for r in cat_r if r["status"] == "OK")
        children = []
        for r in cat_r:
            icon = "✅" if r["status"] == "OK" else ("⚠️" if r["status"] == "WARN" else "❌")
            q_text = r["query"][:60]
            t_text = f"{r['time']:.1f}s"
            # Question line
            children.append(bulleted(f"{icon} [{r['id']}] {q_text} ({t_text})"))
            # Answer preview (truncated to 200 chars)
            ans = r.get("answer_preview", "")
            if ans:
                ans_short = ans[:200].replace("\n", " ")
                if len(ans) > 200:
                    ans_short += "..."
                children.append(paragraph(f"  → {ans_short}"))
        c_avg = sum(r["time"] for r in cat_r) / len(cat_r)
        blocks.append(toggle(
            f"{cat}: {c_ok}/{len(cat_r)} OK (avg {c_avg:.1f}s)",
            children[:MAX_BLOCKS_PER_CALL]
        ))

    # WARN list
    warns = [r for r in results if r["status"] == "WARN"]
    if warns:
        warn_children = []
        for r in warns:
            warn_children.append(bulleted(
                f"⚠️ [{r['id']}] {r['query']} ({r['time']}s)"
            ))
        blocks.append(toggle(f"WARN {len(warns)}건 상세", warn_children))

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
        "Update Log: 증분 누적 | QA Report: 매일 하루치 모음",
        "🤖"
    ))
    all_blocks.append(paragraph(""))

    # Append top-level blocks first
    print("Adding page header...")
    append_blocks(token, PAGE_ID, all_blocks)
    time.sleep(0.3)

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

        # Today's v6.3.0 QA results
        v63_blocks = build_v63_qa_blocks()

        section = [
            heading1("🧪 QA Test Reports"),
            paragraph("매일 하루치 테스트 결과를 모아서 기록합니다."),
        ]
        append_blocks(token, PAGE_ID, section)
        time.sleep(0.3)

        # v7.0 QA 500 all-route test (newest first)
        qa500_blocks = build_qa500_blocks()
        if qa500_blocks:
            append_blocks(token, PAGE_ID, [heading2("2026-02-26 전체파트 종합 E2E 테스트 (500 queries) — v7.2.2")])
            time.sleep(0.3)
            append_blocks(token, PAGE_ID, qa500_blocks)
            time.sleep(0.3)
            print(f"  QA 500: {len(qa500_blocks)} blocks added")

        # v7.0 CS Agent 260 test
        cs260_blocks = build_cs260_blocks()
        if cs260_blocks:
            append_blocks(token, PAGE_ID, [heading2("2026-02-23 CS Agent E2E 테스트 (260 queries) — v7.0")])
            time.sleep(0.3)
            append_blocks(token, PAGE_ID, cs260_blocks)
            time.sleep(0.3)
            print(f"  CS 260: {len(cs260_blocks)} blocks added")

        # v6.5 QA 300 v2 comprehensive test
        qa300v2_blocks = build_qa300_blocks("qa_300_v2_result.json")
        if qa300v2_blocks:
            append_blocks(token, PAGE_ID, [heading2("2026-02-23 종합 QA 300 v2 테스트 (300 queries) — v6.5")])
            time.sleep(0.3)
            append_blocks(token, PAGE_ID, qa300v2_blocks)
            time.sleep(0.3)
            print(f"  QA 300 v2: {len(qa300v2_blocks)} blocks added")

        # v6.5 QA 300 v1 comprehensive test
        qa300_blocks = build_qa300_blocks("qa_300_result.json")
        if qa300_blocks:
            append_blocks(token, PAGE_ID, [heading2("2026-02-20 종합 QA 300 테스트 (299 queries) — v6.5")])
            time.sleep(0.3)
            append_blocks(token, PAGE_ID, qa300_blocks)
            time.sleep(0.3)
            print(f"  QA 300 v1: {len(qa300_blocks)} blocks added")

        # v6.3 QA 100+ comprehensive test
        qa100_blocks = build_qa100_blocks()
        if qa100_blocks:
            append_blocks(token, PAGE_ID, [heading2("2026-02-19 종합 QA 100+ 테스트 (109 queries)")])
            time.sleep(0.3)
            append_blocks(token, PAGE_ID, qa100_blocks)
            time.sleep(0.3)
            print(f"  QA 100+: {len(qa100_blocks)} blocks added")

        # v6.3.0 results
        if v63_blocks:
            append_blocks(token, PAGE_ID, [heading2("2026-02-13 v6.3.0 QA 테스트 (80 queries)")])
            time.sleep(0.3)
            append_blocks(token, PAGE_ID, v63_blocks)
            time.sleep(0.3)

        # v6.1 results
        append_blocks(token, PAGE_ID, [heading2("2026-02-12 종합 QA 테스트 (112 queries)")])
        time.sleep(0.3)
        append_blocks(token, PAGE_ID, qa_blocks)
        print(f"  QA Report: {len(qa_blocks) + len(v63_blocks)} blocks added")

    print("\nDone! Check Notion page.")


if __name__ == "__main__":
    main()
