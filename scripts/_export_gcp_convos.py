"""GCP MariaDB → JSON 내보내기: users, conversations, messages (임시, 서버에서 실행)"""
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

cur.execute("SELECT * FROM users")
users = cur.fetchall()
cur.execute("SELECT * FROM conversations")
convos = cur.fetchall()
cur.execute("SELECT * FROM messages ORDER BY id")
msgs = cur.fetchall()

with open("/tmp/gcp_convo_export.json", "w", encoding="utf-8") as f:
    json.dump({"users": users, "conversations": convos, "messages": msgs}, f, ensure_ascii=False, default=_ser)
print(f"export done: users={len(users)} convos={len(convos)} msgs={len(msgs)}")
