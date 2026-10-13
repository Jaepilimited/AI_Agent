"""fid 21,22,23 원문 답변 조회 (임시, 서버에서 실행)"""
from dotenv import dotenv_values
import pymysql

cfg = dotenv_values('.env')
conn = pymysql.connect(host='127.0.0.1', user=cfg['MARIADB_USER'], password=cfg['MARIADB_PASSWORD'],
                       database='skin1004_ai', charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)
cur = conn.cursor()
cur.execute('''
SELECT f.id fid, m.content
FROM message_feedback f JOIN messages m ON m.id = f.message_id
WHERE f.id IN (21, 22, 23)
''')
for r in cur.fetchall():
    print('=' * 70)
    print('fid:', r['fid'])
    print(repr(r['content'][:1500]))
