"""Read production Entra login counts; print aggregates only."""

import json
import os
from pathlib import Path
import sys

import paramiko


REMOTE_QUERY = r'''
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import subprocess

import pymysql
from app.config import get_settings

settings = get_settings()
assert settings.mariadb_host == "10.1.200.5", "Expected production database"
conn = pymysql.connect(
    host=settings.mariadb_host, port=int(settings.mariadb_port),
    user=settings.mariadb_user, password=settings.mariadb_password,
    database=settings.mariadb_database, charset="utf8mb4",
    cursorclass=pymysql.cursors.DictCursor, connect_timeout=10,
    read_timeout=15, write_timeout=10,
)
result = {}
try:
    with conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        conn.begin()
        cur.execute("SELECT NOW() AS db_now, UTC_TIMESTAMP() AS db_utc, "
                    "@@session.time_zone AS time_zone, DATABASE() AS db_name")
        result["database"] = cur.fetchone()
        cur.execute("SELECT COUNT(*) AS total_users, "
                    "SUM(entra_oid IS NOT NULL AND entra_oid <> '') AS entra_linked_users "
                    "FROM users")
        result["accounts"] = cur.fetchone()
        cur.execute("SELECT COUNT(DISTINCT v.user_id) AS all_users_today, "
                    "COUNT(DISTINCT CASE WHEN u.entra_oid IS NOT NULL AND u.entra_oid <> '' "
                    "THEN v.user_id END) AS entra_linked_users_today, "
                    "COUNT(DISTINCT CASE WHEN v.last_seen_at >= NOW() - INTERVAL 15 MINUTE "
                    "THEN v.user_id END) AS all_users_page_open_15m, "
                    "COUNT(DISTINCT CASE WHEN v.last_seen_at >= NOW() - INTERVAL 15 MINUTE "
                    "AND u.entra_oid IS NOT NULL AND u.entra_oid <> '' "
                    "THEN v.user_id END) AS entra_linked_users_page_open_15m "
                    "FROM user_visits v JOIN users u ON u.id=v.user_id "
                    "WHERE v.visit_date=CURDATE()")
        result["visits"] = cur.fetchone()
        cur.execute("SELECT COUNT(*) AS linked_users, "
                    "SUM(a.id IS NOT NULL) AS with_existing_ad_record, "
                    "SUM(a.can_view_fi=1) AS explicit_fi_allowed, "
                    "SUM(a.can_view_visitor_analytics=1) AS explicit_visitors_allowed, "
                    "SUM(u.role='admin') AS admins, "
                    "SUM(EXISTS(SELECT 1 FROM user_groups ug WHERE ug.ad_user_id=u.ad_user_id)) "
                    "AS users_with_groups FROM users u "
                    "LEFT JOIN ad_users a ON u.ad_user_id=a.id "
                    "WHERE u.entra_oid IS NOT NULL AND u.entra_oid<>''")
        result["entra_permissions"] = cur.fetchone()
        cur.execute("SELECT g.name, g.brand_filter, COUNT(DISTINCT ug.ad_user_id) AS members, "
                    "COUNT(DISTINCT CASE WHEN u.entra_oid IS NOT NULL AND u.entra_oid<>'' "
                    "THEN u.id END) AS entra_linked_members "
                    "FROM access_groups g LEFT JOIN user_groups ug ON ug.group_id=g.id "
                    "LEFT JOIN users u ON u.ad_user_id=ug.ad_user_id "
                    "GROUP BY g.id, g.name, g.brand_filter ORDER BY g.id")
        result["groups"] = cur.fetchall()
        cur.execute("SELECT rc.TABLE_NAME, kcu.COLUMN_NAME, rc.REFERENCED_TABLE_NAME, "
                    "kcu.REFERENCED_COLUMN_NAME, rc.DELETE_RULE "
                    "FROM information_schema.REFERENTIAL_CONSTRAINTS rc "
                    "JOIN information_schema.KEY_COLUMN_USAGE kcu "
                    "ON kcu.CONSTRAINT_SCHEMA=rc.CONSTRAINT_SCHEMA "
                    "AND kcu.CONSTRAINT_NAME=rc.CONSTRAINT_NAME "
                    "AND kcu.TABLE_NAME=rc.TABLE_NAME "
                    "WHERE rc.CONSTRAINT_SCHEMA=DATABASE() "
                    "AND rc.REFERENCED_TABLE_NAME='ad_users'")
        result["ad_foreign_keys"] = cur.fetchall()
        cur.execute("SELECT COUNT(*) AS all_directory_users, SUM(is_active=1) AS active_users, "
                    "MAX(synced_at) AS last_sync_at FROM ad_users")
        result["directory"] = cur.fetchone()
finally:
    conn.rollback()
    conn.close()

kst = dt.timezone(dt.timedelta(hours=9))
now = dt.datetime.now(kst)
today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
journal = subprocess.run(
    ["journalctl", "-u", "ai-craver", "--since", "2026-09-02 00:00:00",
     "--until", now.strftime("%Y-%m-%d %H:%M:%S %z"),
     "--no-pager", "-o", "json", "--grep=entra_login_success"],
    capture_output=True, text=True, timeout=30,
)
if journal.returncode:
    result["login_log_error"] = {"exit_code": journal.returncode}
else:
    events = []
    unmatched = 0
    ansi = re.compile(r"\x1b\[[0-9;]*m")
    for line in journal.stdout.splitlines():
        row = json.loads(line)
        message = ansi.sub("", row.get("MESSAGE", ""))
        try:
            event = json.loads(message)
        except json.JSONDecodeError:
            event = {}
        identity = event.get("anon_id") or event.get("user_id")
        if identity is None:
            match = re.search(r"user_id\D{1,8}(\d+)", message)
            identity = match.group(1) if match else None
        if identity is None:
            unmatched += 1
            continue
        timestamp = dt.datetime.fromtimestamp(int(row["__REALTIME_TIMESTAMP"])/1_000_000, kst)
        events.append((str(identity), timestamp))
    todays = [event for event in events if event[1] >= today_start]
    result["login_logs"] = {
        "since": "2026-09-02 00:00:00 server time",
        "unique_users": len({event[0] for event in events}),
        "success_events": len(events),
        "today_unique_users": len({event[0] for event in todays}),
        "today_success_events": len(todays),
        "unparsed_events": unmatched,
        "first_success_kst": min((event[1] for event in events), default=None),
        "last_success_kst": max((event[1] for event in events), default=None),
    }
result["queried_at_kst"] = now
result["source_hashes"] = {
    name: hashlib.sha256(Path(name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    for name in [
        "app/core/entra_auth.py", "app/api/entra_routes.py", "app/api/auth_middleware.py",
        "app/api/admin_group_api.py", "app/api/auth_api.py", "scripts/sync_ad_users.py"
    ]
}
print(json.dumps(result, default=str, ensure_ascii=True))
'''


def main():
    password = os.environ.get("CRAVER_SSH_PW", "")
    if not password:
        runner = Path(__file__).resolve().parents[1] / "scripts" / "_run_fx_relay.cmd"
        for line in runner.read_text(encoding="ascii").splitlines():
            if line.lower().startswith("set craver_ssh_pw="):
                password = line.partition("=")[2]
                break
    if not password:
        raise RuntimeError("Production SSH credential is unavailable")

    client = paramiko.SSHClient()
    known_hosts = Path.home() / ".ssh" / "known_hosts"
    client.load_host_keys(str(known_hosts))
    try:
        client.connect("10.1.150.5", username="jeffrey", password=password,
                       allow_agent=False, look_for_keys=False,
                       timeout=10, banner_timeout=15, auth_timeout=15)
        command = (
            "cd /home/jeffrey/AI_Agent && "
            "set -a && . ./.env && set +a && "
            "export HTTPS_PROXY=http://10.1.50.2:3128 "
            "HTTP_PROXY=http://10.1.50.2:3128 "
            "NO_PROXY=localhost,127.0.0.1,10.1.0.0/16 && "
            "./venv/bin/python -"
        )
        stdin, stdout, stderr = client.exec_command(command, timeout=60)
        stdin.write(REMOTE_QUERY)
        stdin.flush()
        stdin.channel.shutdown_write()
        output = stdout.read().decode("utf-8", "replace")
        error = stderr.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
        print(output.replace(password, "[redacted]"))
        if code:
            print(json.dumps({"remote_exit_code": code,
                              "remote_error": error.replace(password, "[redacted]")[-2000:]},
                             ensure_ascii=True))
        return code
    finally:
        client.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(json.dumps({"error_type": type(exc).__name__, "error": str(exc)}, ensure_ascii=True))
        sys.exit(1)
