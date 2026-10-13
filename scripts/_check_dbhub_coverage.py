"""
DB-HUB 노션 페이지 커버리지 체크 스크립트
- DB-HUB 하위 모든 페이지 멘션/링크 수집
- 로컬 JSON(notion_vectors_gemini.json)과 비교
- 학습된 페이지 ✅ / 미학습 ❌ 팀별 리포트
"""

import sys
import io
import json
import os
import re
import requests
from dotenv import load_dotenv
from pathlib import Path

# Windows 콘솔 UTF-8 강제
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_MCP_TOKEN")
DB_HUB_ID = "2e12b4283b008011ae32e39bf73b7f7b"
HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}
BASE_DIR = Path(__file__).resolve().parent.parent

def normalize_page_id(raw_id: str) -> str:
    """page_id를 하이픈 없는 32자 형태로 정규화"""
    return raw_id.replace("-", "").lower()

def url_to_page_id(url: str) -> str:
    """다양한 노션 URL 형식에서 page_id 추출"""
    # https://app.notion.com/p/2e12b4283b008011ae32e39bf73b7f7b
    # https://app.notion.com/p/제목-2e12b4283b008011ae32e39bf73b7f7b
    # https://www.notion.so/page-id-no-dashes
    # Extract 32-char hex
    patterns = [
        r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',  # UUID
        r'[0-9a-f]{32}',  # 32-char no dashes
    ]
    for pat in patterns:
        matches = re.findall(pat, url, re.IGNORECASE)
        if matches:
            # Take the last match (page id is usually at the end)
            return normalize_page_id(matches[-1])
    return ""

def get_notion_blocks(block_id: str) -> list:
    """노션 블록 자식 목록 가져오기 (페이지네이션 처리)"""
    url = f"https://api.notion.com/v1/blocks/{block_id}/children"
    results = []
    cursor = None
    while True:
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        resp = requests.get(url, headers=HEADERS, params=params)
        if resp.status_code == 200:
            data = resp.json()
            results.extend(data.get("results", []))
            if data.get("has_more"):
                cursor = data.get("next_cursor")
            else:
                break
        elif resp.status_code == 404:
            # 권한 없음
            break
        else:
            print(f"  [ERR] {block_id}: {resp.status_code} {resp.text[:100]}")
            break
    return results

def extract_page_ids_from_rich_text(rich_texts: list) -> list:
    """rich_text 배열에서 page mention id 및 href link 추출"""
    found = []
    for rt in rich_texts:
        # mention type
        if rt.get("type") == "mention":
            mention = rt.get("mention", {})
            if mention.get("type") == "page":
                pid = normalize_page_id(mention["page"]["id"])
                found.append(("mention", pid))
        # text type with link
        if rt.get("type") == "text":
            href = rt.get("href", "") or ""
            link = (rt.get("text", {}) or {}).get("link", {}) or {}
            link_url = link.get("url", "") or href
            if link_url and ("notion" in link_url or link_url.startswith("/")):
                pid = url_to_page_id(link_url)
                if pid:
                    found.append(("link", pid))
        # annotations href
        href = rt.get("href", "") or ""
        if href and "notion" in href:
            pid = url_to_page_id(href)
            if pid:
                found.append(("href", pid))
    return found

def get_block_page_id(block: dict) -> str:
    """블록이 child_page 또는 linked_to_page 타입이면 page_id 반환"""
    btype = block.get("type", "")
    if btype == "child_page":
        return normalize_page_id(block["id"])
    if btype == "linked_to_page":
        ltp = block.get("linked_to_page", {})
        pid = ltp.get("page_id", "") or ltp.get("id", "")
        return normalize_page_id(pid) if pid else ""
    return ""

def get_page_title(page_id: str) -> str:
    """페이지 제목 가져오기"""
    resp = requests.get(f"https://api.notion.com/v1/pages/{page_id}", headers=HEADERS)
    if resp.status_code == 200:
        data = resp.json()
        props = data.get("properties", {})
        # title property
        for key in ["title", "Name", "이름", "제목"]:
            if key in props:
                title_list = props[key].get("title", [])
                if title_list:
                    return "".join(t.get("plain_text", "") for t in title_list)
        # fallback: search all properties
        for prop_val in props.values():
            title_list = prop_val.get("title", [])
            if title_list:
                return "".join(t.get("plain_text", "") for t in title_list)
    elif resp.status_code == 404:
        return "[권한없음/삭제됨]"
    return "[제목불명]"

def scan_toggle_for_pages(toggle_block_id: str, team_name: str) -> list:
    """
    토글 블록 내부를 탐색하여 연결된 페이지 목록 수집
    반환: [(page_id, source_type), ...]
    """
    found_pages = []
    children = get_notion_blocks(toggle_block_id)

    for block in children:
        btype = block.get("type", "")
        bid = block.get("id", "")

        # 1. child_page / linked_to_page 직접 처리
        pid = get_block_page_id(block)
        if pid:
            found_pages.append((pid, btype))
            continue

        # 2. rich_text에서 mention/link 추출
        block_data = block.get(btype, {}) or {}
        rich_texts = block_data.get("rich_text", [])
        if rich_texts:
            for source_type, pid in extract_page_ids_from_rich_text(rich_texts):
                if pid:
                    found_pages.append((pid, source_type))

        # 3. 토글/bulleted_list 등 하위 자식이 있을 경우 재귀 탐색
        if block.get("has_children") and btype in (
            "toggle", "bulleted_list_item", "numbered_list_item",
            "column_list", "column", "table", "table_row"
        ):
            sub = scan_toggle_for_pages(bid, team_name)
            found_pages.extend(sub)

    return found_pages

def main():
    print("=" * 60)
    print("DB-HUB 노션 페이지 커버리지 분석")
    print("=" * 60)

    # 1. 로컬 JSON에서 학습된 page_id 목록 수집
    json_path = BASE_DIR / "data" / "notion_vectors_gemini.json"
    local_data = json.loads(json_path.read_text(encoding="utf-8"))

    learned_page_ids = set()
    learned_meta = {}  # page_id -> {title, team, url}
    for item in local_data:
        payload = item.get("payload", {})
        pid = normalize_page_id(payload.get("page_id", ""))
        url = payload.get("page_url", "")
        # URL에서도 추출 시도
        url_pid = url_to_page_id(url)

        if pid:
            learned_page_ids.add(pid)
            if pid not in learned_meta:
                learned_meta[pid] = {
                    "title": payload.get("page_title", ""),
                    "team": payload.get("team", ""),
                    "url": url,
                }
        if url_pid and url_pid != pid:
            learned_page_ids.add(url_pid)
            if url_pid not in learned_meta:
                learned_meta[url_pid] = {
                    "title": payload.get("page_title", ""),
                    "team": payload.get("team", ""),
                    "url": url,
                }

    print(f"\n로컬 JSON 학습 페이지 수: {len(learned_page_ids)}")

    # 2. DB-HUB 루트 블록 탐색
    print(f"\nDB-HUB ({DB_HUB_ID}) 루트 블록 탐색 중...")
    root_blocks = get_notion_blocks(DB_HUB_ID)
    print(f"  루트 블록 수: {len(root_blocks)}")

    # 팀별 토글 블록 식별
    team_toggles = []
    for block in root_blocks:
        btype = block.get("type", "")
        bid = block.get("id", "")
        block_data = block.get(btype, {}) or {}
        rich_texts = block_data.get("rich_text", [])

        toggle_text = "".join(rt.get("plain_text", "") for rt in rich_texts)

        # 팀 이름 패턴 감지
        team_keywords = ["Craver", "DB", "KBT", "JBT", "GM", "EAST", "WEST", "BCM",
                        "PEOPLE", "IT", "CS", "B2B", "BT", "GS"]
        is_team = any(kw in toggle_text for kw in team_keywords)

        if btype == "toggle" and (is_team or block.get("has_children")):
            team_toggles.append((bid, toggle_text or "[제목없음]", btype))
            print(f"  토글 발견: '{toggle_text}' (id={bid})")

    # 토글이 없으면 has_children인 모든 블록 시도
    if not team_toggles:
        print("  명시 토글 없음 — has_children 블록 전체 스캔")
        for block in root_blocks:
            btype = block.get("type", "")
            bid = block.get("id", "")
            block_data = block.get(btype, {}) or {}
            rich_texts = block_data.get("rich_text", [])
            toggle_text = "".join(rt.get("plain_text", "") for rt in rich_texts)
            if block.get("has_children"):
                team_toggles.append((bid, toggle_text or f"[{btype}]", btype))
                print(f"  has_children 블록: '{toggle_text}' type={btype} (id={bid})")

    # 3. 각 토글 탐색
    print(f"\n총 {len(team_toggles)}개 섹션 탐색 시작...")

    team_results = {}  # team_name -> [(page_id, page_title, learned)]
    all_found_page_ids = set()

    for toggle_id, team_name, btype in team_toggles:
        print(f"\n  [{team_name}] 탐색 중...")
        found = scan_toggle_for_pages(toggle_id, team_name)
        unique_found = {}
        for pid, src in found:
            if pid not in unique_found:
                unique_found[pid] = src

        print(f"    발견된 페이지 {len(unique_found)}개")
        all_found_page_ids.update(unique_found.keys())

        team_pages = []
        for pid, src in unique_found.items():
            # 제목: 로컬 JSON에 있으면 거기서, 없으면 API 호출
            if pid in learned_meta and learned_meta[pid]["title"]:
                title = learned_meta[pid]["title"]
            else:
                title = get_page_title(pid)
                print(f"      API 조회: {pid} → {title}")

            learned = pid in learned_page_ids
            team_pages.append({
                "page_id": pid,
                "title": title,
                "learned": learned,
                "source": src,
                "url": f"https://www.notion.so/{pid}",
            })

        team_results[team_name] = team_pages

    # 4. 결과 리포트
    print("\n" + "=" * 60)
    print("결과 리포트")
    print("=" * 60)

    total_pages = 0
    total_learned = 0
    total_not_learned = 0

    for team_name, pages in team_results.items():
        if not pages:
            continue

        learned_count = sum(1 for p in pages if p["learned"])
        not_learned = [p for p in pages if not p["learned"]]

        total_pages += len(pages)
        total_learned += learned_count
        total_not_learned += len(not_learned)

        print(f"\n### {team_name} ({learned_count}/{len(pages)} 학습완료)")
        print(f"{'상태':<4} {'페이지 제목':<40} {'page_id'}")
        print("-" * 80)
        for p in sorted(pages, key=lambda x: (not x["learned"], x["title"])):
            status = "✅" if p["learned"] else "❌"
            title_disp = (p["title"] or "")[:38]
            pid_short = p["page_id"]
            print(f"{status}   {title_disp:<40} {pid_short}")

    print("\n" + "=" * 60)
    print(f"전체 요약: {total_pages}개 페이지 발견")
    print(f"  ✅ 학습됨: {total_learned}개")
    print(f"  ❌ 미학습: {total_not_learned}개")
    print("=" * 60)

    # 5. 미학습 페이지 목록만 별도 출력
    print("\n[미학습 페이지 상세 목록]")
    for team_name, pages in team_results.items():
        not_learned = [p for p in pages if not p["learned"]]
        if not_learned:
            print(f"\n{team_name}:")
            for p in not_learned:
                title = p["title"] or "[제목없음]"
                print(f"  - {title}")
                print(f"    URL: {p['url']}")
                print(f"    source: {p['source']}")

    return team_results

if __name__ == "__main__":
    main()
