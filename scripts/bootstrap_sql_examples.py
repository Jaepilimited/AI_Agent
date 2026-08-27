# -*- coding: utf-8 -*-
"""지난 대화에서 (질문 ↔ 실행된 SQL) 쌍을 캐내 예시 자산을 채운다 (1회성).

⛔ 왜 `sql_cache` 에서 캐지 않는가 — 거기 `hit_count` 는 **골든봇·카나리가 부풀린다**
   (실측 2026-08-27: 재사용 205건 중 137건이 골든 문항). 사람이 실제로 묻고 답을 받은
   흔적은 `messages` 에 남는다.

⛔ 무엇을 자산으로 인정하는가:
     · 답변에 `실행된 쿼리` 가 실려 있고 (= 실행까지 갔다)
     · 답변이 "결과 없음" 이 아니고 (= 행이 나왔다)
     · 그 답변에 👎 가 없고
     · 같은 질문이 **두 번 이상** 나온 것
   하나라도 빠지면 예시가 되지 못한다 — 틀린 것을 학습하면 더 자신 있게 틀린다.

⚠️ 이 스크립트는 **다시 돌려도 안전하다**. 같은 질문은 시그니처가 같아 합쳐지고,
   이미 막힌(`blocked`) 것은 되살아나지 않는다.

사용법:  python scripts/bootstrap_sql_examples.py [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict

sys.path.insert(0, ".")

from dotenv import load_dotenv

load_dotenv(".env")

from app.db.mariadb import fetch_all          # noqa: E402

_SQL_BLOCK = re.compile(r"```sql\s*\n([\s\S]*?)\n```")
#: 답이 비었다는 표시. 이런 답의 SQL 을 예시로 쓰면 다음 사람도 0건을 낸다.
_EMPTY_MARKS = ("결과가 없", "결과 없음", "데이터가 없", "조회된 데이터가 없",
                "해당하는 데이터", "찾지 못했습니다")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from app.core.query_profile import signature
    from app.core.sql_examples import ensure_tables, record

    ensure_tables()

    # 👎 가 달린 메시지 — 이 대화에서 나온 것은 자산으로 쓰지 않는다
    disliked = {
        int(r["message_id"]) for r in
        (fetch_all("SELECT message_id FROM message_feedback WHERE rating = -1") or [])
        if r.get("message_id")
    }
    print(f"[i] 👎 달린 답변 {len(disliked)}건 제외")

    rows = fetch_all(
        "SELECT id, conversation_id, content FROM messages "
        "WHERE role = 'assistant' AND content LIKE %s ORDER BY id",
        ("%```sql%",),
    ) or []
    print(f"[i] SQL 이 실린 답변 {len(rows)}건")

    # ⛔ **대화의 첫 질문만** 자산이 된다. "시각화해줘" · "여기서 인도네시아만" 같은
    #    후속 질문은 앞 대화가 진짜 의도라, 예시로 쓰면 **왜 그 SQL 인지 설명되지
    #    않는다.** 실행 경로(`execute_sql`)는 `conversation_context` 로 이미 거르는데,
    #    여기만 안 거르면 자산의 절반이 맥락 없는 조각이 된다.
    first_user = {
        str(r["conversation_id"]): int(r["mid"]) for r in
        (fetch_all("SELECT conversation_id, MIN(id) mid FROM messages "
                   "WHERE role = 'user' GROUP BY conversation_id") or [])
    }

    pairs = defaultdict(lambda: {"n": 0, "question": "", "sql": ""})
    skipped_empty = skipped_disliked = skipped_noq = skipped_followup = 0

    for row in rows:
        if int(row["id"]) in disliked:
            skipped_disliked += 1
            continue
        content = str(row.get("content") or "")
        if any(mark in content for mark in _EMPTY_MARKS):
            skipped_empty += 1
            continue
        match = _SQL_BLOCK.search(content)
        if not match:
            continue
        sql = match.group(1).strip()

        user = fetch_all(
            "SELECT id, content FROM messages WHERE conversation_id = %s AND role = 'user' "
            "AND id < %s ORDER BY id DESC LIMIT 1",
            (row["conversation_id"], row["id"]),
        ) or []
        if not user:
            skipped_noq += 1
            continue
        if int(user[0]["id"]) != first_user.get(str(row["conversation_id"]), -1):
            skipped_followup += 1
            continue
        question = str(user[0].get("content") or "").strip()
        if not question or len(question) > 400 or len(sql) > 6000:
            skipped_noq += 1
            continue

        sig = signature(question)
        if not sig:
            continue
        item = pairs[sig]
        item["n"] += 1
        item["question"] = question       # 가장 최근 표현을 쓴다
        item["sql"] = sql

    repeated = {k: v for k, v in pairs.items() if v["n"] >= 2}
    print(f"[i] 서로 다른 질문 {len(pairs)}개 · 그중 두 번 이상 {len(repeated)}개")
    print(f"[i] 건너뜀 — 👎 {skipped_disliked} · 결과없음 {skipped_empty} · 후속질문 {skipped_followup} · 질문없음 {skipped_noq}")

    if args.dry_run:
        for sig, item in sorted(repeated.items(), key=lambda kv: -kv[1]["n"])[:12]:
            print(f"    {item['n']:2d}회  {item['question'][:56]}")
        return 0

    saved = 0
    for item in repeated.values():
        # ⚠️ `record` 는 한 번에 1회씩 센다 — 실제 횟수만큼 불러 `n_seen` 을 맞춘다
        for _ in range(item["n"]):
            record(item["question"], item["sql"], row_count=1)
        saved += 1
    print(f"[o] 예시 {saved}건 적재")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
