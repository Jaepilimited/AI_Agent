"""Read-only notification evidence for the reported user's empty drawer."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import sys

TASK = Path(__file__).resolve().parent
sys.path.insert(0, str(TASK.parent))
from feedback_release_20260908 import remote


QUERY = r'''
import base64, datetime, hashlib, json
from pathlib import Path
import pymysql
from app.config import get_settings
from app.db import mariadb

s = get_settings()
assert s.mariadb_host == "10.1.200.5"
conn = pymysql.connect(host=s.mariadb_host, port=int(s.mariadb_port),
    user=s.mariadb_user, password=s.mariadb_password, database=s.mariadb_database,
    charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
    connect_timeout=10, read_timeout=15, write_timeout=10)
result = {"scope": "Read only; named user's notifications and frontend source",
          "queried_at": datetime.datetime.now().isoformat()}
try:
    with conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        conn.begin()
        cur.execute("SELECT u.id, u.display_name, u.is_active, u.announce_seen_at "
                    "FROM users u LEFT JOIN directory_users d ON d.id=u.ad_user_id "
                    "WHERE u.display_name=%s OR d.display_name=%s",
                    ("이형섭", "이형섭"))
        users = cur.fetchall()
    def read_all(sql, params=()):
        assert sql.lstrip().upper().startswith("SELECT"), "Only SELECT is allowed"
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())
    def read_one(sql, params=()):
        return next(iter(read_all(sql, params)), None)
    def no_write(*args, **kwargs):
        raise AssertionError("Writes are disabled in incident probe")
    mariadb.fetch_all = read_all
    mariadb.fetch_one = read_one
    mariadb.execute = no_write
    mariadb.execute_lastid = no_write
    from app.core import announcements, briefing, feedback_inbox
    from app.reports import store
    result["users"] = users
    result["notifications"] = {}
    for user in users:
        uid = user["id"]
        sources = {}
        for key, getter in [("shares", store.list_notifications),
                            ("feedback", feedback_inbox.my_feedback),
                            ("announcements", announcements.for_user),
                            ("briefings", briefing.for_user)]:
            try:
                sources[key] = getter(uid)
            except Exception as exc:
                sources[key] = {"error": str(exc), "type": type(exc).__name__}
        result["notifications"][str(uid)] = sources
finally:
    conn.rollback()
    conn.close()
paths = ["app/frontend/chat.js", "app/frontend/chat.html", "app/static/style.css",
         "app/api/notifications_api.py"]
result["sources"] = {name: {"sha256": hashlib.sha256(Path(name).read_bytes()).hexdigest(),
    "base64": base64.b64encode(Path(name).read_bytes()).decode("ascii")} for name in paths}
print(json.dumps(result, default=str, ensure_ascii=True))
'''


NETWORK_QUERY = r'''
import collections, datetime, json, re, subprocess
from pathlib import Path
import requests
result = {"queried_at": datetime.datetime.now().isoformat()}
proc = subprocess.run(["journalctl", "-u", "ai-craver", "--since", "2026-09-09 00:00:00",
    "--no-pager", "-o", "json", "--grep=/api/notifications|notifications_.*failed|Illegal mix of collations"],
    capture_output=True, text=True, timeout=25)
result["journal_exit_code"] = proc.returncode
result["counts"] = {}
events = []
ansi = re.compile(r"\x1b\[[0-9;]*m")
for line in proc.stdout.splitlines():
    row = json.loads(line)
    message = ansi.sub("", row.get("MESSAGE", ""))
    if "/api/notifications" not in message and "Illegal mix" not in message:
        continue
    match = re.search(r'(GET|POST) (/api/notifications[^ ?"]*)[^"\n]*" (\d{3})', message)
    key = " ".join(match.groups()) if match else "other"
    result["counts"][key] = result["counts"].get(key, 0) + 1
    events.append({"at": row.get("__REALTIME_TIMESTAMP"), "message": message[:400]})
result["recent_events"] = events[-50:]
proc = subprocess.run(["journalctl", "-u", "ai-craver", "--since", "2026-09-09 00:00:00",
    "--no-pager", "-o", "json", "--grep=entra_login_success"],
    capture_output=True, text=True, timeout=20)
result["named_user_login_times"] = []
for line in proc.stdout.splitlines():
    row = json.loads(line)
    message = ansi.sub("", row.get("MESSAGE", ""))
    if re.search(r'\buser_id[=\s":]+34\b', message):
        result["named_user_login_times"].append(row.get("__REALTIME_TIMESTAMP"))
result["source_modified_at"] = {p: datetime.datetime.fromtimestamp(Path(p).stat().st_mtime).isoformat()
    for p in ["app/core/session_auth.py", "app/api/auth_middleware.py", "app/frontend/chat.js"]}
import pymysql
from app.config import get_settings
s = get_settings()
conn = pymysql.connect(host=s.mariadb_host, port=int(s.mariadb_port),
    user=s.mariadb_user, password=s.mariadb_password, database=s.mariadb_database,
    charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
    connect_timeout=10, read_timeout=10)
try:
    with conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        conn.begin()
        cur.execute("SELECT visit_date, first_seen_at, last_seen_at, visit_count FROM user_visits "
                    "WHERE user_id=%s AND visit_date >= %s ORDER BY visit_date DESC LIMIT 2", (34, "2026-09-08"))
        result["named_user_visits"] = cur.fetchall()
finally:
    conn.rollback()
    conn.close()
session = requests.Session()
session.trust_env = False
result["http"] = {}
for path in ["/health", "/frontend/chat.js", "/static/style.css"]:
    response = session.get("http://127.0.0.1:3000" + path, timeout=10)
    result["http"][path] = {"status": response.status_code, "bytes": len(response.content)}
print(json.dumps(result, default=str, ensure_ascii=True))
'''


def main():
    if "--network" in sys.argv:
        data = remote(NETWORK_QUERY)
        (TASK / "network_evidence.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(data, ensure_ascii=True))
        return
    data = remote(QUERY)
    for name, entry in data.pop("sources").items():
        destination = TASK / "baseline" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        blob = base64.b64decode(entry["base64"])
        if destination.exists():
            assert destination.read_bytes() == blob, "Baseline changed: " + name
        else:
            destination.write_bytes(blob)
        data.setdefault("source_hashes", {})[name] = entry["sha256"]
    (TASK / "production_evidence.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(data, ensure_ascii=True))


if __name__ == "__main__":
    main()
