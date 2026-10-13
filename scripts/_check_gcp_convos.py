"""GCP MariaDB 대화 이력 인벤토리 (임시, 서버에서 실행)"""
from dotenv import dotenv_values
import pymysql

cfg = dotenv_values('.env')
conn = pymysql.connect(host='127.0.0.1', user=cfg['MARIADB_USER'], password=cfg['MARIADB_PASSWORD'],
                       database='skin1004_ai', charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)
cur = conn.cursor()

cur.execute("SELECT (SELECT COUNT(*) FROM conversations) convos, (SELECT COUNT(*) FROM messages) msgs")
print("totals:", cur.fetchall())

cur.execute("SELECT MIN(created_at) oldest, MAX(updated_at) newest FROM conversations")
print("range:", cur.fetchall())

cur.execute("""SELECT c.user_id, u.email, u.display_name, COUNT(*) cnt, MAX(c.updated_at) newest
FROM conversations c LEFT JOIN users u ON u.id = c.user_id
GROUP BY c.user_id, u.email, u.display_name ORDER BY cnt DESC LIMIT 15""")
for r in cur.fetchall():
    print(r)

cur.execute("SELECT id, email, display_name FROM users WHERE email LIKE 'jeffrey%' OR display_name LIKE '%임재필%'")
print("jeffrey:", cur.fetchall())
