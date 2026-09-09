# -*- coding: utf-8 -*-
"""그룹 배정 대기 알림 — 막혀 있는 사람은 아는데 **관리자만 모르는** 상태를 없앤다.

⛔ **왜 있나** (2026-09-09 사용자 제보). Entra 로 처음 로그인하면 계정은 생기지만
   데이터 조회 그룹이 없어 질문을 하나도 못 한다. 그 사람 화면은 이렇게 말한다:

       데이터 조회 그룹 배정 대기
       관리자가 데이터 조회 그룹을 배정하면 질문을 이용할 수 있습니다.

   **그런데 그 관리자에게 가는 신호가 없었다.** 본인은 막혀 있고 관리자는 모른다 —
   조용한 실패의 사람 버전이다. 사용자 지시: *"이런거 나오면 나한테 알림 뜨게 해야지"*.

⚠️ **스스로 꺼진다.** 그룹이 배정되면 조회에서 빠지고 알림도 함께 사라진다.
   상태를 따로 저장하지 않고 **매번 실제 조건을 조회**하기 때문이다 — 물류 수량
   이상치 공시가 "원본을 고치면 조회가 비고 공시도 사라진다" 로 꺼지는 것과 같은 모양.
   저장하는 것은 "읽었다" 표시뿐이다.

⛔ **판정 조건은 로그인 관문과 같아야 한다.** 다르면 화면은 막혔다고 하는데 알림은
   안 오거나(또는 그 반대) 하는 조용한 어긋남이 난다. `auth_middleware` 가 쓰는
   조건을 그대로 옮겨 왔고, 회귀가 두 문자열을 대조한다.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)

#: 알림을 받을 사람. ⚠️ 사용자 지시로 **한 사람에게만** 간다 (2026-09-09).
#:   전체 관리자에게 뿌리면 잔디 규칙("받는 사람이 특정되는 것만")과 부딪히고,
#:   전원 방송은 알림함 배지가 맡는 자리다.
#: ⚠️ `main.py` 의 관리자 자동 승격에도 같은 주소가 적혀 있다. 한 곳으로 합치려면
#:    그 파일을 만져야 하는데 지금 다른 세션이 편집 중이라 미뤘다 — 합칠 때 함께 볼 것.
OWNER_EMAIL = os.getenv("CELLA_ALERT_OWNER_EMAIL", "jeffrey@skin1004korea.com")

#: 알림 종류 키. `jandi_briefing.KIND_META` 와 `jandi_briefing_relay` 양쪽에 라벨이 있다.
KIND = "group_assign"

#: ⛔ **로그인 관문(`auth_middleware`)과 같은 조건이다.** 손으로 다시 적지 마라 —
#:    갈리면 "화면은 막혔는데 알림은 안 온다" 가 된다. 회귀가 대조한다.
WAITING_CONDITION = (
    "u.requires_group_assignment AND NOT EXISTS "
    "(SELECT 1 FROM user_groups ug JOIN access_groups g ON g.id=ug.group_id "
    "WHERE ug.ad_user_id=u.ad_user_id AND g.brand_filter IS NOT NULL "
    "AND g.brand_filter<>'')"
)

_DDL = """
CREATE TABLE IF NOT EXISTS group_alert_seen (
    waiting_user_id INT PRIMARY KEY,
    seen_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def ensure_table() -> None:
    """읽음 표시 테이블 (idempotent — 앱 기동 시 호출).

    ⚠️ 대기 목록은 저장하지 않는다. 저장하면 배정된 뒤에도 남아 손으로 지워야 하고,
       지우는 것을 잊으면 **이미 해결된 일로 매번 알린다.**
    """
    from app.db.mariadb import execute

    try:
        execute(_DDL)
    except Exception as e:                                  # noqa: BLE001
        logger.warning("group_alert_table_failed", error=str(e)[:160])


def is_recipient(user_id: int) -> bool:
    """이 사람이 알림을 받기로 한 그 사람인가."""
    from app.db.mariadb import fetch_one

    if not user_id:
        return False
    try:
        row = fetch_one("SELECT email FROM users WHERE id=%s", (int(user_id),)) or {}
    except Exception as e:                                  # noqa: BLE001
        logger.warning("group_alert_recipient_failed", error=str(e)[:160])
        return False
    return str(row.get("email") or "").strip().lower() == OWNER_EMAIL.strip().lower()


def waiting() -> List[Dict[str, Any]]:
    """지금 그룹 배정을 기다리는 계정들. **조회할 때마다 실제 조건을 본다.**

    ⚠️ 관리자는 빼지 않는다 — 관문(`auth_middleware`)이 admin 을 통과시키므로
       애초에 대기 상태가 아니고, 조건에도 걸리지 않는다.
    """
    from app.db.mariadb import fetch_all

    try:
        return fetch_all(
            "SELECT u.id, u.display_name, u.email, u.created_at, "
            "a.department AS department "
            "FROM users u LEFT JOIN directory_users a ON u.ad_user_id = a.id "
            f"WHERE u.is_active = 1 AND ({WAITING_CONDITION}) "
            "ORDER BY u.created_at DESC"
        ) or []
    except Exception as e:                                  # noqa: BLE001
        # ⛔ 조용히 빈손을 주면 "대기자가 없다" 로 읽힌다 — 흔적을 남긴다
        logger.warning("group_alert_waiting_failed", error=str(e)[:200])
        return []


def _seen_map() -> Dict[int, Any]:
    from app.db.mariadb import fetch_all

    try:
        rows = fetch_all("SELECT waiting_user_id, seen_at FROM group_alert_seen") or []
    except Exception:                                       # noqa: BLE001
        return {}
    return {int(r["waiting_user_id"]): r.get("seen_at") for r in rows}


def for_user(user_id: int) -> List[Dict[str, Any]]:
    """알림함·잔디가 함께 쓰는 목록. **받기로 한 사람에게만** 준다.

    ⚠️ 대상이 아니면 빈 목록이다 — 남의 계정 이름·부서가 새면 안 된다.
    """
    if not is_recipient(user_id):
        return []
    ensure_table()
    seen = _seen_map()
    out: List[Dict[str, Any]] = []
    for row in waiting():
        wid = int(row["id"])
        name = str(row.get("display_name") or row.get("email") or "").strip()
        dept = str(row.get("department") or "").strip()
        out.append({
            "kind": KIND,
            # ⛔ 한 계정당 한 번만 울린다. 해소될 때까지 참인 조건이라, 매번 밀면
            #    곧 아무도 안 읽는다 (매일 같은 알림은 무시당한다는 그 규칙).
            "dedup_key": f"{KIND}:{wid}",
            "waiting_user_id": wid,
            "title": (f"{name} ({dept})" if dept else name) or f"사용자 {wid}",
            "note": "데이터 조회 그룹을 배정해야 질문을 이용할 수 있습니다.",
            "at": row.get("created_at"),
            "seen": wid in seen,
        })
    return out


def mark_seen(user_id: int, waiting_user_id: Optional[int] = None) -> int:
    """읽음 표시. `waiting_user_id` 를 안 주면 지금 대기 중인 것 전부.

    ⚠️ 대상이 아닌 사람은 아무것도 못 바꾼다 — 판정은 서버가 한다.
    """
    from app.db.mariadb import execute

    if not is_recipient(user_id):
        return 0
    ensure_table()
    ids = ([int(waiting_user_id)] if waiting_user_id
           else [int(r["id"]) for r in waiting()])
    now = datetime.now().replace(microsecond=0)
    done = 0
    for wid in ids:
        try:
            execute("INSERT INTO group_alert_seen (waiting_user_id, seen_at) "
                    "VALUES (%s, %s) ON DUPLICATE KEY UPDATE seen_at=VALUES(seen_at)",
                    (wid, now))
            done += 1
        except Exception as e:                              # noqa: BLE001
            logger.warning("group_alert_seen_failed", error=str(e)[:160])
    return done
