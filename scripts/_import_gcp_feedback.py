"""GCP 피드백 → 로컬 임포트: (conversation_id, role, created_at, content 앞부분)으로 메시지 매칭 (임시)"""
import sys, io, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import pymysql
from app.config import get_settings

s = get_settings()
conn = pymysql.connect(host=s.mariadb_host, port=int(s.mariadb_port), user=s.mariadb_user,
                       password=s.mariadb_password, database=s.mariadb_database,
                       charset='utf8mb4', autocommit=False, cursorclass=pymysql.cursors.DictCursor)
cur = conn.cursor()

with open("data/gcp_feedback_export.json", encoding="utf-8") as f:
    rows = json.load(f)

ok, miss, dup = 0, 0, 0
for r in rows:
    cur.execute(
        """SELECT id FROM messages
           WHERE conversation_id=%s AND role=%s AND created_at=%s AND LEFT(content,200)=%s LIMIT 2""",
        (r["conversation_id"], r["m_role"], r["m_created"], r["m_head"]),
    )
    found = cur.fetchall()
    if len(found) != 1:
        # 내용 매칭 실패 시 시각+역할만으로 재시도
        cur.execute(
            "SELECT id FROM messages WHERE conversation_id=%s AND role=%s AND created_at=%s LIMIT 2",
            (r["conversation_id"], r["m_role"], r["m_created"]),
        )
        found = cur.fetchall()
    if len(found) != 1:
        miss += 1
        continue
    n = cur.execute(
        """INSERT IGNORE INTO message_feedback (message_id, conversation_id, user_id, anon_id, rating, comment, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (found[0]["id"], r["conversation_id"], r["user_id"], r["anon_id"], r["rating"], r["comment"], r["created_at"]),
    )
    if n == 1:
        ok += 1
    else:
        dup += 1

conn.commit()
print(f"피드백 이관: 성공={ok}, 중복스킵={dup}, 매칭실패={miss} (총 {len(rows)}건)")
