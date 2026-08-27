# -*- coding: utf-8 -*-
"""고친 실패를 회귀로 굳힌다 — 붐따 → 골든 문항 후보.

⛔ **왜 필요한가** (2026-08-27 실측). 👎 74건 중 골든셋에 반영된 것이 **0건**이었다.
   처리 완료(`done`)로 닫혔는데 골든에 없는 것이 **40건**이다. 즉 **고친 40가지가
   재발해도 아무도 모른다** — 다음 배포에서 조용히 되돌아가도 통과다.

   CLAUDE.md 는 "데이터 규칙을 바꿨으면 골든 문항도 같이 추가한다" 고 적어 두었지만,
   손으로 하는 일은 결국 안 된다. 그래서 **빠진 것을 매일 보이게** 만든다.

⛔ **자동으로 골든에 넣지 않는다.** 골든 문항은 기대 문구(`contains`)가 생명이고,
   그건 사람이 정해야 한다 — "기대 키워드가 실패 답변에도 들어가면 그 문항은
   아무것도 검증하지 못한다" (실제로 거짓 통과가 났던 규칙이다). 여기서는 **후보와
   초안**까지만 만든다.

⚠️ 오래된 것까지 매일 세면 첫날부터 40건이 떠서 곧 무시당한다. 감시는 **최근에
   처리한 것**만 본다 — 지금 손이 닿는 크기여야 사람이 실제로 움직인다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import structlog

from app.db.mariadb import fetch_all

logger = structlog.get_logger(__name__)

#: 감시할 창. 이보다 오래된 것은 "밀린 숙제" 라 매일 알릴 값이 없다 (목록으로는 준다).
RECENT_DAYS = 14
#: 이만큼 쌓이면 사람이 봐야 한다.
ALERT_AT = 3

_GOLDEN_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "golden_set.json"


#: 골든 문항이 될 값이 있는 경로. `direct`(잡담·기능 질문)는 뺀다.
_WORK_ROUTES = ("bigquery", "notion", "cs", "gws", "inventory", "model_rights", "multi")


def _route_of(answer: str) -> str:
    """답변에서 라우트를 읽는다 — 판정은 `skill_memory` 것을 그대로 쓴다."""
    try:
        from app.agents.skill_memory import _infer_route

        return _infer_route(answer or "")
    except Exception:
        return "direct"

def _golden_signatures() -> set:
    """골든셋에 이미 있는 질문의 시그니처.

    ⚠️ 질문 원문이 아니라 시그니처로 본다 — "이번 달" 과 "이번달" 이 다른 문항으로
       보이면 있는데도 없다고 센다 (`sql_cache` 가 문자열 일치라 안 걸리던 것과 같다).
    """
    from app.core.query_profile import signature

    try:
        data = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("golden_set_unreadable", error=str(e)[:160])
        return set()
    return {signature(item.get("question", "")) for item in data.get("items", [])}


def golden_candidates(recent_days: int | None = None) -> List[Dict[str, Any]]:
    """고쳤다고 닫힌 붐따 중 **골든셋이 지키지 않는** 것.

    ⚠️ 코멘트가 없는 👎 는 뺀다. 무엇이 틀렸는지 모르면 기대 문구를 쓸 수 없어
       문항이 될 수 없다 (진단할 수 없는 건을 대기열에 섞지 않는 것과 같은 규칙).
    """
    from app.core.query_profile import signature

    covered = _golden_signatures()
    where = "AND f.handled_at >= DATE_SUB(NOW(), INTERVAL %s DAY)" if recent_days else ""
    params = (recent_days,) if recent_days else ()
    rows = fetch_all(
        "SELECT f.id, f.comment, f.handled_at, f.handled_note, u.content AS question, "
        "       m.content AS answer "
        "FROM message_feedback f "
        "JOIN messages m ON m.id = f.message_id "
        # ⛔ `u.id < m.id` 로 조인하고 `GROUP BY` 로 묶으면 **직전 질문이 아니라
        #    아무 질문이나** 딸려 온다 (MySQL 이 조용히 한 행을 고른다). 실측:
        #    #152 의 이유는 "동남아시아2팀" 인데 질문은 "NAD cream" 으로 붙었다 —
        #    목록은 그럴듯한데 짝이 틀린, 이 프로젝트가 가장 싫어하는 실패다.
        "JOIN messages u ON u.id = ("
        "    SELECT MAX(id) FROM messages "
        "     WHERE conversation_id = m.conversation_id AND role = 'user' AND id < m.id) "
        "WHERE f.rating = -1 AND f.status = 'done' "
        "  AND f.comment IS NOT NULL AND TRIM(f.comment) <> '' " + where +
        " GROUP BY f.id ORDER BY f.id DESC",
        params,
    ) or []

    out: List[Dict[str, Any]] = []
    seen = set()
    for row in rows:
        question = str(row.get("question") or "").strip()
        sig = signature(question)
        if not sig or sig in covered or sig in seen:
            continue
        # ⛔ 잡담에 달린 👎 는 골든 문항이 될 값이 없다 ("웃으라 하지 말아요" 가
        #    후보로 올라왔다). 업무 경로만 남긴다 — 라우트 판정은 `skill_memory` 가
        #    이미 하는 것을 **그대로** 쓴다 (사본을 만들지 않는다).
        if _route_of(str(row.get("answer") or "")) not in _WORK_ROUTES:
            continue
        seen.add(sig)
        out.append({
            "feedback_id": row["id"],
            "question": question[:300],
            "why": str(row.get("comment") or "")[:200],
            "fixed_at": row.get("handled_at"),
            "note": str(row.get("handled_note") or "")[:200],
        })
    return out


def draft_items(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """골든 문항 **초안**. 기대 문구는 비워 둔다 — 사람이 채워야 한다.

    ⛔ 기대 문구를 자동으로 만들지 않는다. 지금 답변에서 낱말을 뽑아 넣으면
       **지금 답이 맞다는 전제**가 되는데, 그 전제가 틀렸을 때 문항이 오답을 굳힌다.
    ⚠️ `contains` 는 그 답변에서만 나올 값(숫자·고유명사)이어야 한다. 일반어를 넣으면
       되묻기 답변에도 들어 있어 **거짓 통과**가 난다 (실제로 났던 사고다).
    """
    drafts = []
    for c in candidates:
        drafts.append({
            "id": f"regress_fb{c['feedback_id']}",
            "question": c["question"],
            "scope": "weekly",
            "contains": [],          # ← 사람이 채운다 (그 답변에서만 나올 값)
            "not_contains": [],
            "why": c["why"],
        })
    return drafts


def status() -> Dict[str, Any]:
    """자가 점검·다이제스트가 쓰는 한 줄 요약."""
    recent = golden_candidates(RECENT_DAYS)
    everything = golden_candidates(None)
    return {
        "recent": len(recent),
        "total": len(everything),
        "window_days": RECENT_DAYS,
        "questions": [c["question"][:60] for c in recent[:5]],
    }
