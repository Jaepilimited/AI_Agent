"""Read-only WAS source/aggregate snapshot; never inspect session token values."""

from pathlib import Path
import sys

# This artifact is named inspect.py; do not shadow Python's inspect module when
# Paramiko/Pydantic import their normal dependencies.
ARTIFACTS = Path(__file__).resolve().parent
sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != ARTIFACTS]
sys.path.insert(0, str(ARTIFACTS.parent))

from datetime import datetime, timezone
import hashlib
import json

import entra_release_prepare_20260908 as transport


FILES = [
    "app/api/auth_api.py", "app/api/auth_middleware.py", "app/api/middleware.py",
    "app/api/entra_routes.py", "app/api/face_search_api.py", "app/api/face_search_routes.py",
    "app/api/coa_finder_api.py", "app/api/harness_api.py",
    "app/core/session_auth.py", "app/core/entra_auth.py", "app/core/self_check.py",
    "app/core/golden_runner.py", "app/frontend/chat.js", "app/frontend/chat.html",
    "app/main.py", "app/config.py",
]

REMOTE_QUERY = r'''
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import json
import pymysql
import requests
from app.config import get_settings

settings = get_settings()
today = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
result = {"observed_at_utc": datetime.now(timezone.utc).isoformat(), "visit_date_kst": today}
connection = None
try:
    connection = pymysql.connect(
        host=settings.mariadb_host, port=int(settings.mariadb_port),
        user=settings.mariadb_user, password=settings.mariadb_password,
        database=settings.mariadb_database, cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10, read_timeout=15,
    )
    with connection.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        connection.begin()
        cur.execute("SELECT NOW() AS database_now, CURDATE() AS database_date")
        result["database_clock"] = cur.fetchone()
        cur.execute("SELECT COUNT(*) AS total, "
                    "COALESCE(SUM(entra_oid IS NOT NULL AND entra_oid<>''),0) AS entra_linked, "
                    "COALESCE(SUM(entra_oid IS NULL OR entra_oid=''),0) AS entra_unlinked, "
                    "COALESCE(SUM(is_active=1),0) AS active FROM users")
        result["users"] = cur.fetchone()
        cur.execute("SELECT COUNT(DISTINCT v.user_id) AS distinct_users, "
                    "COUNT(DISTINCT CASE WHEN u.entra_oid IS NOT NULL AND u.entra_oid<>'' "
                    "THEN v.user_id END) AS entra_linked_users, "
                    "COUNT(DISTINCT CASE WHEN u.entra_oid IS NULL OR u.entra_oid='' "
                    "THEN v.user_id END) AS entra_unlinked_users "
                    "FROM user_visits v JOIN users u ON u.id=v.user_id WHERE v.visit_date=%s", (today,))
        result["today_visitors"] = cur.fetchone()
except Exception as exc:
    result["database_error_type"] = type(exc).__name__
finally:
    if connection is not None:
        connection.rollback()
        connection.close()
try:
    response = requests.get(f"http://127.0.0.1:{int(settings.port)}/api/auth/methods", timeout=10)
    result["auth_methods_http_status"] = response.status_code
    payload = response.json()
    result["auth_methods"] = {key: payload[key] for key in ("password", "entra")
                              if isinstance(payload, dict) and isinstance(payload.get(key), bool)}
except Exception as exc:
    result["auth_methods_error_type"] = type(exc).__name__
print("SESSION_INSPECTION_JSON=" + json.dumps(result, default=str, ensure_ascii=True))
'''


def main() -> int:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    client = transport.connect("was")
    try:
        sftp = client.open_sftp()
        try:
            inventory = sorted(name for name in sftp.listdir(transport.REMOTE + "/app/api")
                               if name.endswith(".py") and "/" not in name and "\\" not in name
                               and ("harness" in name or "face_search" in name))
            files = list(dict.fromkeys(FILES + ["app/api/" + name for name in inventory]))
            manifest = {}
            for relative in files:
                try:
                    with sftp.open(transport.REMOTE + "/" + relative, "rb") as remote:
                        data = remote.read()
                except FileNotFoundError:
                    manifest[relative] = {"exists": False}
                    continue
                target = ARTIFACTS / "was" / relative
                if not target.resolve().is_relative_to(ARTIFACTS.resolve()):
                    raise RuntimeError("Snapshot target escapes artifact directory")
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists() and target.read_bytes() != data:
                    raise RuntimeError("WAS source changed since this inspection baseline")
                target.write_bytes(data)
                manifest[relative] = {"exists": True, "bytes": len(data),
                                      "sha256": hashlib.sha256(data).hexdigest()}
        finally:
            sftp.close()

        command = (
            "cd /home/jeffrey/AI_Agent && set -a && . /home/jeffrey/AI_Agent/.env && set +a && "
            "HTTPS_PROXY=http://10.1.50.2:3128 HTTP_PROXY=http://10.1.50.2:3128 "
            "NO_PROXY=localhost,127.0.0.1,10.1.0.0/16 ./venv/bin/python -"
        )
        stdin, stdout, stderr = client.exec_command(command, timeout=45)
        stdin.write(REMOTE_QUERY)
        stdin.channel.shutdown_write()
        raw = stdout.read().decode("utf-8", errors="replace")
        error_present = bool(stderr.read())
        exit_code = stdout.channel.recv_exit_status()
        marker = "SESSION_INSPECTION_JSON="
        documents = [line[len(marker):] for line in raw.splitlines() if line.startswith(marker)]
        aggregate = json.loads(documents[-1]) if documents else {
            "remote_process_status": exit_code, "remote_stderr_present": error_present,
            "aggregate_unavailable": True,
        }
        report = {"observed_at_utc": datetime.now(timezone.utc).isoformat(),
                  "target": "was", "api_inventory_matches": inventory,
                  "files": manifest, "aggregates": aggregate}
        (ARTIFACTS / "inspection.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=True))
        return 0 if documents and exit_code == 0 else 1
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # SSH/SQL exceptions can contain connection details; report only type.
        print(json.dumps({"ok": False, "error_type": type(error).__name__}))
        raise SystemExit(1)
