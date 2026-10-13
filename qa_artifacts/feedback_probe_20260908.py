"""Read the current production feedback reports and their conversation context."""
import entra_access_count_20260908 as runner

runner.REMOTE_QUERY = r'''
import json
import pymysql
from app.config import get_settings

s = get_settings()
assert s.mariadb_host == "10.1.200.5", "Expected production database"
conn = pymysql.connect(host=s.mariadb_host, port=int(s.mariadb_port),
                       user=s.mariadb_user, password=s.mariadb_password,
                       database=s.mariadb_database, charset="utf8mb4",
                       cursorclass=pymysql.cursors.DictCursor,
                       connect_timeout=10, read_timeout=15)
result = {}
try:
    with conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        conn.begin()
        cur.execute("SELECT id, rating, comment, created_at, status, handled_note, "
                    "conversation_id, message_id FROM message_feedback "
                    "WHERE rating < 0 ORDER BY created_at DESC LIMIT 8")
        result["feedback"] = cur.fetchall()
        target = [row for row in result["feedback"] if row["status"] in ("new", "ack")][:2]
        result["conversations"] = {}
        for row in target:
            cur.execute("SELECT id, role, content, model, created_at FROM messages "
                        "WHERE conversation_id=%s ORDER BY id", (row["conversation_id"],))
            result["conversations"][str(row["id"])] = cur.fetchall()
finally:
    conn.rollback()
    conn.close()
print(json.dumps(result, default=str, ensure_ascii=True))
'''

if __name__ == "__main__":
    raise SystemExit(runner.main())
