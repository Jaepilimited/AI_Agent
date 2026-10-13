"""프로덕션 message_feedback에서 👎 건 조회 (임시, 서버에서 실행)"""
import json, re
from dotenv import dotenv_values
import pymysql

cfg = dotenv_values('.env')
conn = pymysql.connect(host='127.0.0.1', user=cfg['MARIADB_USER'], password=cfg['MARIADB_PASSWORD'],
                       database='skin1004_ai', charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)
cur = conn.cursor()
cur.execute('''
SELECT f.id fid, f.message_id, f.comment, f.created_at,
       m.content answer, m.model,
       (SELECT content FROM messages m2 WHERE m2.conversation_id = m.conversation_id
          AND m2.id < m.id AND m2.role = 'user' ORDER BY m2.id DESC LIMIT 1) question
FROM message_feedback f
JOIN messages m ON m.id = f.message_id
WHERE f.rating = -1
ORDER BY f.created_at DESC
''')
fence = chr(96) * 3
for r in cur.fetchall():
    ans = r['answer'] or ''
    ans = re.sub(fence + r'[\s\S]*?' + fence, '[code/chart]', ans)
    ans = re.sub(r'<details[\s\S]*?</details>', '[sql]', ans)
    print('=' * 70)
    print('fid:', r['fid'], '| date:', str(r['created_at']), '| model:', r['model'])
    print('COMMENT:', r['comment'])
    print('Q:', (r['question'] or '')[:300].replace('\n', ' '))
    print('A:', ans[:600].replace('\n', ' '))
