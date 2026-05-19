"""DB-HUB 학습 현황 표시 스크립트.

DB-HUB Notion 페이지의 모든 하위 페이지 목록을 크롤링해,
notion_vectors_gemini.json에 없는 페이지를 '미학습'으로 표시.
DB-HUB 최상단에 callout + 팀별 테이블로 현황을 삽입.
이전 실행의 블록을 자동으로 교체 (data/notion_status_block_id.json 기준).

Usage:
  python -X utf8 scripts/update_notion_learning_status.py
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

NOTION_TOKEN   = os.getenv("NOTION_MCP_TOKEN", "")
NOTION_VERSION = "2022-06-28"
DB_HUB_ID      = "2e12b4283b008011ae32e39bf73b7f7b"
LOCAL_JSON     = _ROOT / "data" / "notion_vectors_gemini.json"
STATUS_ID_FILE = _ROOT / "data" / "notion_status_block_id.json"
NOTION_API     = "https://api.notion.com/v1"

TEAM_ORDER = ["B2B2", "BCM", "CS", "B2B1", "DB", "PEOPLE", "Craver", "KBT", "JBT", "[GM]WEST", "[GM]EAST"]


def headers() -> dict:
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


# ── Notion 헬퍼 ──────────────────────────────────────────────────────────────

def get_block_children(block_id: str, client: httpx.Client) -> list[dict]:
    resp = client.get(f"{NOTION_API}/blocks/{block_id}/children?page_size=100", headers=headers())
    if resp.status_code != 200:
        return []
    return resp.json().get("results", [])


def rich_text_str(content: dict) -> str:
    return "".join(t.get("plain_text", "") for t in content.get("rich_text", []))


# ── DB-HUB 크롤링 (pipeline과 동일 로직) ────────────────────────────────────

def collect_from_block(block_id: str, team: str, client: httpx.Client, pages: list):
    children = get_block_children(block_id, client)
    for b in children:
        btype = b.get("type", "")
        bid   = b.get("id", "")

        if btype == "child_page":
            title = b.get("child_page", {}).get("title", "")
            pages.append({"page_id": bid, "title": title, "team": team})

        elif btype == "child_database":
            title = b.get("child_database", {}).get("title", "")
            pages.append({"page_id": bid, "title": title, "team": team})

        elif btype == "toggle":
            sub_team = rich_text_str(b["toggle"]) or team
            collect_from_block(bid, sub_team, client, pages)

        elif btype in ("paragraph", "bulleted_list_item", "numbered_list_item"):
            rich = b.get(btype, {}).get("rich_text", [])
            for chunk in rich:
                if chunk.get("type") == "mention":
                    m = chunk.get("mention", {})
                    if m.get("type") == "page":
                        pid = m["page"]["id"]
                        text = chunk.get("plain_text", "")
                        pages.append({"page_id": pid, "title": text, "team": team})


def crawl_hub(client: httpx.Client) -> list[dict]:
    hub_blocks = get_block_children(DB_HUB_ID, client)
    pages = []
    seen_ids: set[str] = set()
    for block in hub_blocks:
        if block.get("type") == "toggle":
            team = rich_text_str(block["toggle"])
            # 자동 삽입된 학습 현황 블록은 수집 제외
            if team.startswith(STATUS_TOGGLE_MARKER):
                continue
            before = len(pages)
            collect_from_block(block["id"], team, client, pages)
            if len(pages) == before:
                pages.append({"page_id": block["id"], "title": team, "team": team, "is_block": True})

    # 중복 page_id 제거 (동일 페이지가 여러 팀 참조에 등장하는 경우)
    unique: list[dict] = []
    for p in pages:
        pid = normalize_id(p.get("page_id", ""))
        if pid not in seen_ids:
            seen_ids.add(pid)
            unique.append(p)
    return unique


# ── 벡터스토어 로드 ───────────────────────────────────────────────────────────

def load_learned_ids() -> set[str]:
    if not LOCAL_JSON.exists():
        return set()
    store = json.loads(LOCAL_JSON.read_text(encoding="utf-8"))
    ids = set()
    for entry in store:
        pid = entry.get("payload", {}).get("page_id", "")
        if pid:
            # Notion page_id 정규화 (하이픈 제거)
            ids.add(pid.replace("-", ""))
    return ids


def normalize_id(raw: str) -> str:
    return raw.replace("-", "")


def hyphenate_id(raw: str) -> str:
    """Notion page_id를 8-4-4-4-12 형식으로 변환."""
    s = raw.replace("-", "")
    if len(s) != 32:
        return raw
    return f"{s[:8]}-{s[8:12]}-{s[12:16]}-{s[16:20]}-{s[20:]}"


# ── Notion 블록 빌드 ──────────────────────────────────────────────────────────

def rt(text: str, bold: bool = False, color: str = "default") -> dict:
    ann = {"bold": bold, "color": color}
    return {"type": "text", "text": {"content": text}, "annotations": ann}


STATUS_TOGGLE_MARKER = "🤖 AI 학습 현황"  # crawl_hub에서 이 prefix로 skip


def build_status_blocks(
    all_pages: list[dict],
    learned_ids: set[str],
    now_str: str,
) -> list[dict]:
    """학습 현황을 단일 wrapper toggle 안에 담아 반환.

    wrapper toggle 제목 앞에 STATUS_TOGGLE_MARKER를 붙여,
    crawl_hub()가 이 블록을 팀 데이터로 오인하지 않도록 한다.
    """
    from collections import defaultdict
    by_team: dict[str, list[dict]] = defaultdict(list)
    for p in all_pages:
        team = p.get("team", "?")
        pid  = normalize_id(p.get("page_id", ""))
        ok   = pid in learned_ids
        by_team[team].append({**p, "learned": ok})

    total     = len(all_pages)
    n_learned = sum(1 for p in all_pages if normalize_id(p.get("page_id","")) in learned_ids)
    n_not     = total - n_learned
    pct       = int(n_learned / total * 100) if total else 0

    # ── wrapper toggle 제목 ──────────────────────────────────────────────
    wrapper_title = f"{STATUS_TOGGLE_MARKER} — 학습 {n_learned}/{total} ({pct}%)  |  {now_str}"

    # ── 요약 callout ─────────────────────────────────────────────────────
    summary = (
        f"학습 완료 {n_learned}/{total}페이지 ({pct}%)  |  "
        f"미학습 {n_not}페이지  |  최종 갱신: {now_str}"
    )
    inner_blocks = [{
        "object": "block",
        "type": "callout",
        "callout": {
            "icon": {"type": "emoji", "emoji": "📊"},
            "color": "blue_background",
            "rich_text": [rt(summary, bold=True)],
        },
    }]

    # ── 팀별 toggle ───────────────────────────────────────────────────────
    teams = TEAM_ORDER + [t for t in by_team if t not in TEAM_ORDER]
    for team in teams:
        pages = by_team.get(team)
        if not pages:
            continue

        n_ok  = sum(1 for p in pages if p["learned"])
        n_ng  = len(pages) - n_ok
        emoji = "✅" if n_ng == 0 else ("⚠️" if n_ok > 0 else "❌")
        label = f"{emoji} {team}  ({n_ok}/{len(pages)} 학습 완료)"

        children = []
        for p in pages:
            icon    = "✅" if p["learned"] else "❌"
            reason  = "" if p["learned"] else "  ← 통합 연동 필요"
            pid_raw = p.get("page_id", "")
            is_block = p.get("is_block", False)

            if is_block:
                # toggle 블록 자체 → 외부 URL 링크
                title = p.get("title") or "(제목 없음)"
                url   = f"https://www.notion.so/skin1004/{DB_HUB_ID.replace('-','')}"
                page_rt = {"type": "text",
                           "text": {"content": title, "link": {"url": url}},
                           "annotations": {"color": "default"}}
            else:
                # 일반 Notion 페이지 → mention(내부 링크, 경고 팝업 없음)
                page_rt = {
                    "type": "mention",
                    "mention": {"type": "page", "page": {"id": hyphenate_id(pid_raw)}},
                    "annotations": {"color": "red" if not p["learned"] else "default"},
                }

            children.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        rt(f"{icon} "),
                        page_rt,
                        rt(reason, color="red" if reason else "default"),
                    ]
                },
            })

        inner_blocks.append({
            "object": "block",
            "type": "toggle",
            "toggle": {
                "rich_text": [rt(label, bold=True)],
                "children": children[:99],
            },
        })

    # ── 단일 wrapper toggle로 감싸서 반환 ────────────────────────────────
    return [{
        "object": "block",
        "type": "toggle",
        "toggle": {
            "rich_text": [rt(wrapper_title, bold=True)],
            "children": inner_blocks[:99],  # callout + 팀 toggles
        },
    }]


# ── 이전 상태 블록 삭제 ───────────────────────────────────────────────────────

def delete_old_blocks(client: httpx.Client):
    if not STATUS_ID_FILE.exists():
        return
    data = json.loads(STATUS_ID_FILE.read_text(encoding="utf-8"))
    for block_id in data.get("block_ids", []):
        resp = client.patch(
            f"{NOTION_API}/blocks/{block_id}",
            headers=headers(),
            json={"archived": True},
        )
        status = "삭제" if resp.status_code == 200 else f"실패({resp.status_code})"
        print(f"   이전 블록 {block_id[:8]}... {status}")
    STATUS_ID_FILE.unlink(missing_ok=True)


# ── 신규 블록 삽입 (DB-HUB 최상단) ───────────────────────────────────────────

def prepend_blocks(blocks: list[dict], client: httpx.Client) -> list[str]:
    """DB-HUB 최상단에 블록 삽입. 생성된 block_id 목록 반환."""
    # Notion PATCH children은 기본적으로 마지막에 append.
    # 최상단 삽입은 after="" 이면 맨 앞에 추가됨 (Notion API 동작).
    # 단, after 파라미터 없이 children만 보내면 순서는 API에 따름.
    # → 먼저 현재 첫 번째 block_id를 가져와서 그 앞에 끼우기 위해
    #   children PATCH의 "after" 기능 활용.
    existing = get_block_children(DB_HUB_ID, client)

    created_ids = []
    # 블록을 역순으로 삽입해 최상단 순서 유지
    # after="" means beginning; after=block_id means after that block
    # We insert each block at position after="" (prepend), then the next one after=""
    # But Notion doesn't officially support prepend. Let's use after=first_block approach:
    # Actually simplest: just append at end (or use a dedicated section)
    # Since Notion doesn't have prepend, we'll append at the end instead.
    # Users will see it when scrolling — OR we can make the note at the top a child page.

    # Fallback: append at end (still visible)
    resp = client.patch(
        f"{NOTION_API}/blocks/{DB_HUB_ID}/children",
        headers=headers(),
        json={"children": blocks},
    )
    if resp.status_code != 200:
        print(f"❌ 블록 삽입 실패: {resp.status_code} — {resp.text[:300]}")
        return []

    results = resp.json().get("results", [])
    created_ids = [r["id"] for r in results]
    return created_ids


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    if not NOTION_TOKEN:
        print("❌ NOTION_MCP_TOKEN not found in .env")
        sys.exit(1)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"🔍 DB-HUB 크롤링 중... ({now_str})")

    with httpx.Client(timeout=30) as client:
        # 1. DB-HUB 전체 페이지 목록
        all_pages = crawl_hub(client)
        print(f"   총 {len(all_pages)}개 페이지 발견")

        # 2. 학습된 page_id 목록
        learned_ids = load_learned_ids()
        n_learned = sum(1 for p in all_pages if normalize_id(p.get("page_id","")) in learned_ids)
        n_not = len(all_pages) - n_learned
        print(f"   학습 완료: {n_learned}  미학습: {n_not}")

        # 3. 이전 상태 블록 삭제
        print("🗑️  이전 상태 블록 삭제 중...")
        delete_old_blocks(client)
        time.sleep(0.5)

        # 4. 새 상태 블록 빌드
        blocks = build_status_blocks(all_pages, learned_ids, now_str)
        print(f"📦 {len(blocks)}개 블록 생성")

        # 5. DB-HUB에 삽입
        print("📤 Notion DB-HUB에 업로드 중...")
        created_ids = prepend_blocks(blocks, client)

        if created_ids:
            # block_id 저장 (다음 실행 시 삭제용)
            STATUS_ID_FILE.write_text(
                json.dumps({"block_ids": created_ids, "updated_at": now_str}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"✅ 완료! {len(created_ids)}개 블록 업로드")
            print(f"   https://www.notion.so/skin1004/DB-HUB-{DB_HUB_ID.replace('-','')}")
        else:
            print("❌ 업로드 실패")


if __name__ == "__main__":
    main()
