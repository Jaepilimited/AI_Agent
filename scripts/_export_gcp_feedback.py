"""GCP message_feedback 내보내기 — 메시지 매칭 키 포함 (임시, 서버에서 실행)"""
import json, datetime
from dotenv import dotenv_values
import pymysql

def _ser(o):
    if isinstance(o, (datetime.datetime, datetime.date)):
        return o.isoformat(sep=" ")
    raise TypeError

cfg = dotenv_values('.env')
conn = pymysql.connect(host='127.0.0.1', user=cfg['MARIADB_USER'], password=cfg['MARIADB_PASSWORD'],
                       database='skin1004_ai', charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)
cur = conn.cursor()
cur.execute("""
SELECT f.message_id, f.conversation_id, f.user_id, f.anon_id, f.rating, f.comment, f.created_at,
       m.role m_role, m.created_at m_created, LEFT(m.content, 200) m_head
FROM message_feedback f JOIN messages m ON m.id = f.message_id
""")
rows = cur.fetchall()
with open("/tmp/gcp_feedback_export.json", "w", encoding="utf-8") as f:
    json.dump(rows, f, ensure_ascii=False, default=_ser)
print(f"feedback export: {len(rows)}건")
