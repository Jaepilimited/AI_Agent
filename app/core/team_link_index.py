# -*- coding: utf-8 -*-
"""팀 자료 **링크 카드** — `team_resources` 의 시트·드라이브 링크를 벡터 색인에 태운다.

⛔ **왜 필요한가.** DB-HUB 한 페이지를 두 파이프라인이 각자 긁는다:
   `sync_team_resources.py`(01:00) → MariaDB 906건, `notion_qdrant_pipeline.py`(05:00)
   → 벡터 407조각. 그런데 벡터 쪽은 `child_page`·`child_database`·페이지 멘션만
   수집한다 — **구글 시트·드라이브 링크는 노션 페이지가 아니라서 통째로 빠진다.**
   실측(2026-08-25): 시트·드라이브 자료 64건 중 44건(69%)이 색인에 이름조차 없었다.
   그래서 "그 시트 어디 있어" 류가 어느 경로로도 답이 안 나왔다.

⛔ **왜 여기서 다시 크롤링하지 않는가.** 이미 01:00 에 긁어 둔 표가 있다. 노션을
   또 긁으면 같은 원본을 세 번 읽는 셈이고, 두 사본이 갈리는 이 프로젝트의 단골
   사고를 하나 더 만든다. 크롤은 한 번, 색인은 그 결과에서 만든다.

⚠️ **이름이 없는 행은 태우지 않는다.** `Untitled`·URL 뿐인 이름·`/2a32b428…` 같은
   조각은 검색에 걸려봐야 잡음이고, **잡음은 답처럼 보인다** (0건일 때 검색어를
   한 낱말까지 풀지 않기로 한 것과 같은 판단). 상위 경로로 이름을 살릴 수 있으면
   살리고, 그래도 사람이 읽을 이름이 안 나오면 버린다.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from typing import Dict, Iterable, List, Optional, Set

import structlog

logger = structlog.get_logger(__name__)

_DASHED = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)
_ID32 = re.compile(r"([0-9a-f]{32})", re.I)

# 사람이 읽을 이름이 아닌 것들 — 이대로 색인하면 검색 결과가 지저분해진다
_JUNK_NAME = re.compile(r"^(untitled|무제|new database|제목 ?없음)\s*$", re.I)

_SOURCE = "team_resources"        # payload.source — 링크 카드임을 나중에 가려내는 표식
MAX_TEXT = 900


def notion_page_id(url: str) -> str:
    """노션 URL → dash 없는 32자리 page id (노션 링크가 아니면 빈 문자열)."""
    lowered = (url or "").lower()
    if "notion." not in lowered:
        return ""
    match = _DASHED.search(lowered) or _ID32.search(lowered)
    return match.group(1).replace("-", "") if match else ""


def _breadcrumb(row: dict, by_id: Dict[int, dict]) -> str:
    """상위 폴더 경로 — 이름이 부실한 자료의 뜻을 살리는 유일한 단서다."""
    parts: List[str] = []
    parent_id, depth = row.get("parent_id"), 0
    while parent_id and parent_id in by_id and depth < 8:
        parent = by_id[parent_id]
        if parent.get("node_type") != "team":
            name = (parent.get("name") or "").strip()
            if name and not name.startswith("http"):
                parts.append(name)
        parent_id = parent.get("parent_id")
        depth += 1
    return " > ".join(reversed(parts))


def _label(row: dict, crumb: str) -> str:
    """색인에 쓸 제목. 살릴 수 없으면 빈 문자열(=버린다)."""
    name = (row.get("name") or "").strip()
    if (
        name
        and len(name) >= 4
        and not name.startswith("http")
        and not name.startswith("/")
        and not _JUNK_NAME.match(name)
    ):
        return name
    tail = crumb.split(" > ")[-1].strip() if crumb else ""
    if tail and len(tail) >= 4 and not tail.startswith("http") and not _JUNK_NAME.match(tail):
        return tail
    return ""


_TYPE_LABEL = {
    "google_sheet": "구글 시트",
    "google_drive": "구글 드라이브",
    "notion": "노션 문서",
    "other": "링크",
}


def _card_text(team: str, label: str, crumb: str, row: dict) -> str:
    """검색에 걸릴 본문. 링크 카드는 짧아서 조각내지 않는다 (1행 = 1조각)."""
    kind = _TYPE_LABEL.get(row.get("resource_type") or "other", "링크")
    lines = [f"{team} 팀 자료 · {kind}", label]
    if crumb:
        lines.append(f"경로: {crumb}")
    description = (row.get("description") or "").strip()
    if description and description != label:
        lines.append(description[:400])
    lines.append(f"링크: {row.get('url') or ''}")
    return "\n".join(lines)[:MAX_TEXT]


def build_link_cards(
    rows: Iterable[dict],
    indexed_page_ids: Optional[Set[str]] = None,
    resolve_team=None,
) -> List[dict]:
    """`team_resources` 행 → 벡터 포인트 payload (임베딩 **전** 단계).

    `indexed_page_ids` 에 이미 색인된 노션 page id 를 주면 그 링크는 건너뛴다 —
    본문이 이미 색인돼 있는 문서를 링크 카드로 한 번 더 넣으면 같은 문서가 두 번
    잡힌다.

    `resolve_team` 은 `qdrant_agent.resolve_team_filter` 를 받는다. ⛔ 팀 값은
    반드시 이걸 거쳐야 한다 — 색인은 `[GM]EAST`, `team_resources` 는 `GM EAST` 라
    그냥 넣으면 `@@GM EAST` 필터에 이 카드들만 안 걸린다 (에러 없이 빠진다).
    """
    rows = list(rows)
    by_id = {r["id"]: r for r in rows}
    indexed = indexed_page_ids or set()
    if resolve_team is None:
        from app.agents.qdrant_agent import resolve_team_filter as resolve_team

    cards: List[dict] = []
    skipped_indexed = skipped_nameless = 0
    for row in rows:
        if row.get("node_type") in ("team", "folder"):
            continue
        url = (row.get("url") or "").strip()
        if not url:
            continue
        page_id = notion_page_id(url)
        if page_id and page_id in indexed:
            skipped_indexed += 1
            continue
        crumb = _breadcrumb(row, by_id)
        label = _label(row, crumb)
        if not label:
            skipped_nameless += 1
            continue

        team = resolve_team(row.get("team") or "") or (row.get("team") or "")
        text = _card_text(team, label, crumb, row)
        synced = str(row.get("synced_at") or "")
        cards.append({
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"teamres:{row['id']}")),
            "payload": {
                "source": _SOURCE,
                "hub_id": "hub_main",
                "team": team,
                "status": "active",
                "page_id": f"teamres-{row['id']}",
                "page_url": url,
                "page_title": label,
                "breadcrumb": f"{team} > {crumb}" if crumb else f"{team} > {label}",
                "section_path": crumb,
                "chunk_index": 0,
                "last_edited_time": synced,
                "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "text": text,
                "source_collection": "team_resources",
            },
        })

    logger.info("team_link_cards_built", cards=len(cards),
                skipped_indexed=skipped_indexed, skipped_nameless=skipped_nameless)
    return cards


def load_rows() -> List[dict]:
    """MariaDB 에서 팀 자료 행 전체 (트리 계산에 폴더·팀 노드도 필요하다)."""
    from app.db.mariadb import fetch_all

    return fetch_all(
        "SELECT id, parent_id, team, node_type, name, resource_type, url, "
        "       COALESCE(description,'') AS description, synced_at "
        "FROM team_resources ORDER BY depth, sort_order"
    )
