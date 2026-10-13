"""로컬 conversations/messages JSON 백업 + binlog 여부 확인 (임시)"""
import sys, io, os, json, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from app.db.mariadb import _maria_fetch_all

def _ser(o):
    if isinstance(o, (datetime.datetime, datetime.date)):
        return o.isoformat()
    raise TypeError

convos = _maria_fetch_all("SELECT * FROM conversations")
msgs = _maria_fetch_all("SELECT * FROM messages")
out = {"conversations": convos, "messages": msgs}
ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
path = f"data/_local_convo_backup_{ts}.json"
with open(path, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, default=_ser)
print(f"백업 완료: {path} (convos={len(convos)}, msgs={len(msgs)})")

try:
    print("binlog:", _maria_fetch_all("SHOW VARIABLES LIKE 'log_bin'"))
except Exception as e:
    print("binlog 확인 실패:", e)
