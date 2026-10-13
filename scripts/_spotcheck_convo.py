"""복구된 대화 1건 메시지 순서 스팟체크 (임시)"""
import sys, io, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from app.db.mariadb import _maria_fetch_all

convo = _maria_fetch_all("""SELECT id, title FROM conversations
WHERE user_id=1 AND created_at < '2026-06-01' ORDER BY updated_at DESC LIMIT 1""")[0]
print("대화:", convo["title"][:50])
for m in _maria_fetch_all("SELECT id, role, LEFT(content, 60) c, created_at FROM messages WHERE conversation_id=%s ORDER BY id LIMIT 8", (convo["id"],)):
    print(f"  [{m['role']:9s}] {m['created_at']} | {' '.join(m['c'].split())}")
