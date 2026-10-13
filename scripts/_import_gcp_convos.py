"""GCP 내보내기 JSON → 로컬 MariaDB 병합 임포트 (임시)
- users: INSERT IGNORE (id 보존)
- conversations: INSERT IGNORE (UUID PK, 기존 로컬 대화는 그대로)
- messages: 이번에 새로 들어간 대화의 메시지만, id는 auto-increment로 재발급 (원본 순서 유지)
"""
import sys, io, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import pymysql
from app.config import get_settings

s = get_settings()
conn = pymysql.connect(host=s.mariadb_host, port=int(s.mariadb_port), user=s.mariadb_user,
                       password=s.mariadb_password, database=s.mariadb_database,
                       charset='utf8mb4', autocommit=False)
cur = conn.cursor()

with open("data/gcp_convo_export.json", encoding="utf-8") as f:
    data = json.load(f)

# 1) users
u_cols = list(data["users"][0].keys())
u_sql = f"INSERT IGNORE INTO users ({', '.join(u_cols)}) VALUES ({', '.join(['%s']*len(u_cols))})"
u_new = 0
for u in data["users"]:
    u_new += cur.execute(u_sql, [u[c] for c in u_cols])
print(f"users: +{u_new} (총 {len(data['users'])}개 중)")

# 2) conversations
c_cols = list(data["conversations"][0].keys())
c_sql = f"INSERT IGNORE INTO conversations ({', '.join(c_cols)}) VALUES ({', '.join(['%s']*len(c_cols))})"
new_convo_ids = set()
for c in data["conversations"]:
    if cur.execute(c_sql, [c[col] for col in c_cols]) == 1:
        new_convo_ids.add(c["id"])
print(f"conversations: +{len(new_convo_ids)} (총 {len(data['conversations'])}개 중)")

# 3) messages — 새로 들어간 대화 것만, id 제외 (재발급)
m_cols = [c for c in data["messages"][0].keys() if c != "id"]
m_sql = f"INSERT INTO messages ({', '.join(m_cols)}) VALUES ({', '.join(['%s']*len(m_cols))})"
m_new = 0
for m in data["messages"]:  # 원본 id 순 정렬 상태 → auto-inc로 순서 보존
    if m["conversation_id"] in new_convo_ids:
        cur.execute(m_sql, [m[c] for c in m_cols])
        m_new += 1
print(f"messages: +{m_new} (총 {len(data['messages'])}개 중)")

conn.commit()
cur.execute("SELECT (SELECT COUNT(*) FROM conversations), (SELECT COUNT(*) FROM messages), (SELECT COUNT(*) FROM conversations WHERE user_id=1)")
print("최종(로컬): convos, msgs, jeffrey_convos =", cur.fetchone())
