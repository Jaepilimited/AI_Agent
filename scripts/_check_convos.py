"""로컬 MariaDB 대화 이력 상태 진단 (임시)"""
import sys, io, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from app.db.mariadb import _maria_fetch_all

print("== users ==")
for r in _maria_fetch_all("SELECT id, email, display_name, ad_user_id, role, last_login FROM users ORDER BY id"):
    print(r)

print("\n== conversations per user ==")
for r in _maria_fetch_all("SELECT user_id, COUNT(*) cnt, MIN(created_at) oldest, MAX(updated_at) newest FROM conversations GROUP BY user_id ORDER BY cnt DESC"):
    print(r)

print("\n== totals ==")
print(_maria_fetch_all("SELECT (SELECT COUNT(*) FROM conversations) convos, (SELECT COUNT(*) FROM messages) msgs"))

print("\n== recent 10 conversations ==")
for r in _maria_fetch_all("SELECT id, user_id, title, updated_at FROM conversations ORDER BY updated_at DESC LIMIT 10"):
    print(str(r)[:160])
