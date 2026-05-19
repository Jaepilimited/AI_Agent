#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Notion 학습 데이터 Q&A 테스트 및 노션 업로드.

각 벡터화된 페이지에서 핵심 Q&A 생성 후 노션에 업로드.

Usage:
  python -X utf8 scripts/notion_qa_test.py
"""

import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import requests

# ── 경로 설정 ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── .env 로드 ──────────────────────────────────────────────────────────────
env_path = ROOT / ".env"
env: dict[str, str] = {}
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")

GEMINI_API_KEY = env.get("GEMINI_API_KEY", "")
NOTION_TOKEN = env.get("NOTION_MCP_TOKEN", "")
NOTION_PAGE_ID = "3032b4283b0080ae8241cedef71fc3be"  # AI 사람만들기 로그

NOTION_API = "https://api.notion.com/v1"
HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

TEAM_LABELS = {
    "B2B2": "B2B2 (해외영업)",
    "BCM": "BCM (브랜드컨텐츠마케팅)",
    "CS": "CS (고객서비스)",
    "B2B1": "B2B1 (국내영업)",
    "DB": "DB (데이터분석)",
    "PEOPLE": "PEOPLE (인사)",
    "Craver": "Craver",
    "KBT": "KBT",
    "JBT": "JBT",
    "[GM]WEST": "GM WEST",
    "[GM]EAST": "GM EAST",
}


def load_pages() -> dict[tuple, str]:
    """JSON에서 팀/페이지별 청크 텍스트 합산."""
    vpath = ROOT / "data" / "notion_vectors_gemini.json"
    store = json.loads(vpath.read_text(encoding="utf-8"))

    pages: dict[tuple, dict] = {}
    for pt in store:
        p = pt.get("payload", {})
        team = p.get("team", "?")
        title = p.get("page_title", "?")
        url = p.get("page_url", "")
        key = (team, title)
        if key not in pages:
            pages[key] = {"chunks": [], "url": url}
        pages[key]["chunks"].append(p.get("text", ""))

    # 팀 → 페이지 → 텍스트 합산
    result = {}
    for (team, title), data in pages.items():
        combined = "\n\n".join(data["chunks"])
        result[(team, title)] = {"text": combined, "url": data["url"]}
    return result


def generate_qa(title: str, team: str, text: str) -> str:
    """Gemini Flash로 Q&A 생성."""
    from google import genai as google_genai
    client = google_genai.Client(api_key=GEMINI_API_KEY)

    prompt = f"""SKIN1004 사내 노션 문서를 분석하고 실용적인 Q&A를 작성하라.

팀: {team}
페이지 제목: {title}
내용:
{text[:3000]}

지시:
1. 직원이 실제로 업무 중 이 페이지를 찾아볼 때 가장 많이 물어볼 질문 1~2개를 선별하라.
2. 각 질문에 대해 문서 내용 기반으로 정확하고 구체적인 답변을 작성하라.
3. 한국어로 작성하라.
4. 아래 형식을 엄수하라:

**Q1. [질문]**
A: [답변]

**Q2. [질문]** (있을 경우)
A: [답변]"""

    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )
    return resp.text.strip()


def md_to_rich_text(text: str) -> list:
    import re
    parts = []
    segments = re.split(r"(\*\*.*?\*\*)", text)
    for seg in segments:
        if seg.startswith("**") and seg.endswith("**"):
            parts.append({
                "type": "text",
                "text": {"content": seg[2:-2]},
                "annotations": {"bold": True},
            })
        elif seg:
            parts.append({"type": "text", "text": {"content": seg}})
    return parts or [{"type": "text", "text": {"content": text}}]


def build_notion_blocks(qa_by_team: dict) -> list:
    """Q&A 결과를 Notion 블록 리스트로 변환."""
    blocks = []
    for team, pages in sorted(qa_by_team.items()):
        label = TEAM_LABELS.get(team, team)
        # 팀 헤더
        blocks.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": f"[{label}]"}}]},
        })
        for (title, url, qa_text) in pages:
            # 페이지 제목
            title_rt = [{"type": "text", "text": {"content": title}}]
            if url:
                title_rt = [{"type": "text", "text": {"content": title, "link": {"url": url}}}]
            blocks.append({
                "object": "block",
                "type": "heading_3",
                "heading_3": {"rich_text": title_rt},
            })
            # Q&A 내용 — 줄별 paragraph
            for line in qa_text.split("\n"):
                line = line.strip()
                if not line:
                    continue
                blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": md_to_rich_text(line)},
                })
            # 구분선
            blocks.append({"object": "block", "type": "divider", "divider": {}})
    return blocks


def upload_to_notion(blocks: list, title: str) -> bool:
    """Notion 페이지에 toggle 블록으로 업로드."""
    # Notion: 최대 100 children per block
    toggle_block = {
        "object": "block",
        "type": "toggle",
        "toggle": {
            "rich_text": [{"type": "text", "text": {"content": f"📚 {title}"}, "annotations": {"bold": True}}],
            "children": blocks[:99],
        },
    }

    url = f"{NOTION_API}/blocks/{NOTION_PAGE_ID}/children"
    resp = requests.patch(url, headers=HEADERS, json={"children": [toggle_block]})
    if resp.status_code == 200:
        print(f"✅ 노션 업로드 완료: {title}")
        print(f"   https://notion.so/{NOTION_PAGE_ID.replace('-', '')}")
        return True
    else:
        print(f"❌ 업로드 실패: {resp.status_code}")
        print(f"   {resp.text[:500]}")
        return False


def main():
    if not GEMINI_API_KEY:
        print("❌ GEMINI_API_KEY not found in .env")
        sys.exit(1)
    if not NOTION_TOKEN:
        print("❌ NOTION_MCP_TOKEN not found in .env")
        sys.exit(1)

    print("📂 노션 벡터 데이터 로드 중...")
    pages = load_pages()
    print(f"   총 {len(pages)}개 페이지 발견\n")

    # 팀별 Q&A 수집
    qa_by_team: dict[str, list] = defaultdict(list)

    total = len(pages)
    for i, ((team, title), data) in enumerate(sorted(pages.items()), 1):
        print(f"[{i}/{total}] {team} / {title}")
        try:
            qa = generate_qa(title, team, data["text"])
            print(f"   Q&A 생성 완료 ({len(qa)}자)")
            qa_by_team[team].append((title, data["url"], qa))
            time.sleep(1)  # API rate limit 방지
        except Exception as e:
            print(f"   ⚠️ 오류: {e}")
            qa_by_team[team].append((title, data["url"], f"생성 실패: {e}"))

    print("\n📋 Notion 블록 빌드 중...")
    blocks = build_notion_blocks(qa_by_team)
    print(f"   총 {len(blocks)}개 블록")

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    toggle_title = f"Notion 학습 데이터 Q&A 테스트 — {now}"

    print("\n📤 노션 업로드 중...")
    upload_to_notion(blocks, toggle_title)


if __name__ == "__main__":
    main()
