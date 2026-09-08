"""Atropos RL 학습 데이터 익스포트.

message_feedback 테이블의 👍/👎 데이터를 Atropos GRPO 포맷 JSONL로 출력한다.

출력 포맷 (한 줄 = 한 trajectory):
  {"system": "...", "messages": [{"role": "user", ...}, {"role": "assistant", ...}], "reward": 1.0}

  reward: 1.0 = 👍,  0.0 = 👎

사용법:
  python -X utf8 scripts/export_atropos_trajectories.py
  python -X utf8 scripts/export_atropos_trajectories.py --output data/atropos_out.jsonl
  python -X utf8 scripts/export_atropos_trajectories.py --min-rating 1   # 👍 만
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

import pymysql
import pymysql.cursors


def get_conn():
    return pymysql.connect(
        host=os.getenv("MARIADB_HOST", "127.0.0.1"),
        port=int(os.getenv("MARIADB_PORT", "3306")),
        user=os.getenv("MARIADB_USER"),
        password=os.getenv("MARIADB_PASSWORD"),
        database=os.getenv("MARIADB_DATABASE", "skin1004_ai"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


_SYSTEM_PROMPT = (
    "당신은 Craver Corporation(SKIN1004)의 AI 어시스턴트입니다. "
    "사내 데이터(매출, 제품, 업무 문서)에 대한 질문에 정확하고 구조화된 답변을 제공하세요."
)


def fetch_trajectories(conn, min_rating: int = -1):
    """feedback 테이블 기반으로 (user_msg, assistant_msg, reward) 트리플 반환."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                mf.message_id,
                mf.conversation_id,
                mf.rating,
                m_asst.content AS asst_content,
                m_asst.created_at AS asst_created_at
            FROM message_feedback mf
            JOIN messages m_asst ON m_asst.id = mf.message_id
            WHERE mf.rating >= %s
            ORDER BY mf.message_id
            """,
            (min_rating,),
        )
        feedback_rows = cur.fetchall()

    trajectories = []
    for row in feedback_rows:
        msg_id = row["message_id"]
        convo_id = row["conversation_id"]

        # 직전 user 메시지 조회
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT content FROM messages
                WHERE conversation_id = %s AND role = 'user' AND id < %s
                ORDER BY id DESC LIMIT 1
                """,
                (convo_id, msg_id),
            )
            user_row = cur.fetchone()

        if not user_row:
            continue

        user_content = user_row["content"]
        if isinstance(user_content, list):
            user_content = " ".join(
                p.get("text", "") for p in user_content
                if isinstance(p, dict) and p.get("type") == "text"
            )

        asst_content = row["asst_content"] or ""
        reward = 1.0 if row["rating"] == 1 else 0.0

        trajectories.append({
            "system": _SYSTEM_PROMPT,
            "messages": [
                {"role": "user", "content": str(user_content)},
                {"role": "assistant", "content": asst_content},
            ],
            "reward": reward,
            "meta": {
                "message_id": msg_id,
                "conversation_id": convo_id,
                "rating": row["rating"],
            },
        })

    return trajectories


def main():
    parser = argparse.ArgumentParser(description="Export Atropos GRPO training trajectories")
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output JSONL path (default: data/atropos_trajectories_YYYYMMDD.jsonl)",
    )
    parser.add_argument(
        "--min-rating", type=int, default=-1,
        choices=[-1, 1],
        help="Minimum rating to include. 1=👍 only, -1=both (default: -1)",
    )
    args = parser.parse_args()

    out_path = args.output or (
        Path(__file__).resolve().parent.parent
        / "data"
        / f"atropos_trajectories_{datetime.now().strftime('%Y%m%d')}.jsonl"
    )
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    conn = get_conn()
    try:
        trajectories = fetch_trajectories(conn, min_rating=args.min_rating)
    finally:
        conn.close()

    if not trajectories:
        print("⚠️  피드백 데이터가 없습니다.")
        return

    with open(out_path, "w", encoding="utf-8") as f:
        for traj in trajectories:
            f.write(json.dumps(traj, ensure_ascii=False) + "\n")

    pos = sum(1 for t in trajectories if t["reward"] == 1.0)
    neg = len(trajectories) - pos
    print(f"✅ 익스포트 완료: {out_path}")
    print(f"   총 {len(trajectories)}건 (👍 {pos}건 / 👎 {neg}건)")
    print(f"   Atropos 학습: python -m atropos.train --data {out_path}")


if __name__ == "__main__":
    main()
