"""Deploy the reviewed Entra release with preservation checks and rollback files."""
import ast
import io
import json
from pathlib import Path
import shlex
import sys
import time

from entra_release_prepare_20260908 import ARTIFACTS, FILES, REMOTE, ROOT, connect, credential, digest

BACKUP = REMOTE + "/.release_backups/entra_directory_20260908_r2"
ENV_PYTHON = ("cd " + REMOTE + " && set -a && . ./.env && set +a && "
              "export HTTPS_PROXY=http://10.1.50.2:3128 HTTP_PROXY=http://10.1.50.2:3128 "
              "NO_PROXY=localhost,127.0.0.1,10.1.0.0/16 && ./venv/bin/python -")


def command(client, code, *, stdin_text=None, timeout=45):
    stdin, stdout, stderr = client.exec_command(code, timeout=timeout)
    if stdin_text is not None:
        stdin.write(stdin_text)
        stdin.flush()
    stdin.channel.shutdown_write()
    output, error = stdout.read().decode("utf-8", "replace"), stderr.read().decode("utf-8", "replace")
    status = stdout.channel.recv_exit_status()
    if status:
        # Environment and credentials never appear in diagnostics.
        raise RuntimeError("Remote command failed (exit " + str(status) + "): " + error[-1200:].replace(credential(), "[redacted]"))
    return output


def python(client, source, timeout=45):
    return command(client, ENV_PYTHON, stdin_text=source, timeout=timeout)


def mkdirs(sftp, path):
    assert (path == REMOTE or path.startswith(REMOTE + "/")) and ".." not in Path(path).parts
    current = ""
    for part in path.strip("/").split("/"):
        current += "/" + part
        try:
            sftp.stat(current)
        except FileNotFoundError:
            sftp.mkdir(current, mode=0o700)


def atomic_write(sftp, path, data, mode=0o600):
    mkdirs(sftp, path.rsplit("/", 1)[0])
    temporary = path + ".entra_release_tmp"
    with sftp.open(temporary, "wb") as handle:
        handle.write(data)
    sftp.chmod(temporary, mode)
    sftp.posix_rename(temporary, path)


def read(sftp, path):
    try:
        with sftp.open(path, "rb") as handle:
            return handle.read()
    except FileNotFoundError:
        return None


def prepare():
    baselines = json.loads((ARTIFACTS / "manifest.json").read_text(encoding="utf-8"))
    selected = {}
    for target in ("was", "app"):
        stage = ARTIFACTS / ("stage-" + target)
        if target == "was":
            for relative in FILES:
                destination = stage / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes((ROOT / relative).read_bytes())
        assert stage.exists(), "APP staging is required"
        entries = {}
        for local in sorted(stage.rglob("*")):
            if not local.is_file():
                continue
            relative = local.relative_to(stage).as_posix()
            assert relative in FILES, relative
            data = local.read_bytes()
            if local.suffix == ".py":
                ast.parse(data.decode("utf-8-sig"))
            entries[relative] = {"before": baselines[target][relative]["remote_sha256"], "after": digest(data)}
        selected[target] = entries
    old_cron = (ARTIFACTS / "app/crontab.actual").read_text(encoding="utf-8")
    lines = old_cron.splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines) if not line.lstrip().startswith("#") and "scripts/sync_ad_users.py" in line]
    assert len(matches) == 1, "Expected one legacy directory cron"
    approved = [line for line in (ROOT / "deploy/crontab.app-server").read_text(encoding="utf-8").splitlines()
                if not line.lstrip().startswith("#") and "scripts/sync_entra_users.py" in line]
    assert len(approved) == 1
    lines[matches[0]] = approved[0] + "\n"
    # Preserve all unrelated jobs and their schedules.
    new_cron = "".join(lines)
    (ARTIFACTS / "crontab.next").write_text(new_cron, encoding="utf-8", newline="\n")
    (ARTIFACTS / "selected.json").write_text(json.dumps(selected, indent=2), encoding="utf-8")
    print(json.dumps({"prepared": {k: len(v) for k, v in selected.items()}, "cron_replacements": 1}))


SNAPSHOT = '''
import json
from pathlib import Path
from app.db.mariadb import get_maria_conn
conn = get_maria_conn()
try:
    with conn.cursor() as cur:
        cur.execute("SELECT DATABASE() AS name")
        assert cur.fetchone()["name"] == "ai"
        snapshot = {}
        for table in ("ad_users", "users", "access_groups", "user_groups"):
            cur.execute("SELECT * FROM " + table + " ORDER BY id")
            snapshot[table] = cur.fetchall()
        backup = Path(BACKUP) / "identity_before.json"
        assert not backup.exists(), "A rollout backup already exists; inspect before retry"
        backup.write_text(json.dumps(snapshot, default=str), encoding="utf-8")
        backup.chmod(0o600)
        print(json.dumps({"snapshot": {table: len(rows) for table, rows in snapshot.items()}}))
finally:
    conn.close()
'''

VERIFY = '''
import json
from pathlib import Path
from app.db.mariadb import get_maria_conn
from app.core.user_directory_migration import migration_status, ensure_directory_tables
before = json.loads((Path(BACKUP) / "identity_before.json").read_text())
conn = get_maria_conn()
try:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM directory_users ORDER BY id")
        people = {r["id"]: r for r in cur.fetchall()}
        for person in before["ad_users"]:
            current = people[person["id"]]
            for field in ("id", "can_view_fi", "can_view_visitor_analytics", "deal_id_prefix"):
                assert current[field] == person[field], "Directory permission changed"
        cur.execute("SELECT * FROM users ORDER BY id")
        accounts = {r["id"]: r for r in cur.fetchall()}
        for account in before["users"]:
            current = accounts[account["id"]]
            for field in ("id", "ad_user_id", "role", "allowed_models", "password_hash", "email"):
                assert current[field] == account[field], "Existing account changed"
        cur.execute("SELECT * FROM user_groups ORDER BY id")
        memberships = cur.fetchall()
        assert json.loads(json.dumps(memberships, default=str)) == before["user_groups"], "Membership changed"
        cur.execute("SELECT * FROM access_groups ORDER BY id")
        assert json.loads(json.dumps(cur.fetchall(), default=str)) == before["access_groups"], "Group changed"
        cur.execute("SELECT COUNT(*) AS n FROM information_schema.REFERENTIAL_CONSTRAINTS "
                    "WHERE CONSTRAINT_SCHEMA=DATABASE() AND REFERENCED_TABLE_NAME='ad_users'")
        assert cur.fetchone()["n"] == 0, "Legacy references remain"
        cur.execute("SELECT COUNT(*) AS accounts, SUM(entra_oid IS NOT NULL) AS entra_linked FROM users")
        counts = cur.fetchone()
        print(json.dumps({"preserved_directory_people":len(before["ad_users"]),
                          "preserved_accounts":len(before["users"]),
                          "preserved_memberships":len(memberships), "counts":counts}, default=str))
finally:
    conn.close()
print(json.dumps({"migration":migration_status(), "repeat_startup_migrated":ensure_directory_tables()["migrated"]},default=str))
'''


def migrate_env(was, app):
    # Copy only Entra app settings within the same authorized application.
    settings_query = ("import json\nfrom app.config import get_settings\ns=get_settings()\n"
                      "print(json.dumps({key.upper():getattr(s,key,'') for key in "
                      "('entra_tenant_id','entra_client_id','entra_client_secret','entra_redirect_uri')}))\n")
    source = json.loads(python(was, settings_query))
    current = json.loads(python(app, settings_query))
    was_sftp, app_sftp = was.open_sftp(), app.open_sftp()
    try:
        original = read(app_sftp, REMOTE + "/.env")
        assert original is not None
        keys = ("ENTRA_TENANT_ID", "ENTRA_CLIENT_ID", "ENTRA_CLIENT_SECRET", "ENTRA_REDIRECT_URI")
        assert all(source.get(key) for key in keys), "WAS Entra setup missing"
        # No local environment files or credentials are written.
        text = original.decode("utf-8-sig").rstrip("\r\n") + "\n"
        for key in keys:
            if current.get(key) and current[key] != source[key]:
                raise RuntimeError("APP already has a different Entra configuration")
            if not current.get(key):
                value = source[key]
                assert "\n" not in value and "\r" not in value
                text += key + "=" + shlex.quote(value) + "\n"
        atomic_write(app_sftp, BACKUP + "/app.env.before", original)
        atomic_write(app_sftp, REMOTE + "/.env", text.encode("utf-8"))
        assert json.loads(python(app, settings_query)) == source, "APP Entra settings did not round-trip"
    finally:
        was_sftp.close()
        app_sftp.close()


def apply():
    selected = json.loads((ARTIFACTS / "selected.json").read_text(encoding="utf-8"))
    clients = {target: connect(target) for target in selected}
    service_stopped = False
    changed = False
    migration_started = False
    try:
        for target, entries in selected.items():
            sftp = clients[target].open_sftp()
            try:
                for relative, hashes in entries.items():
                    stage = ARTIFACTS / ("stage-" + target) / relative
                    assert digest(stage.read_bytes()) == hashes["after"], "Staged file changed"
                    current = read(sftp, REMOTE + "/" + relative)
                    current_hash = digest(current) if current is not None else None
                    inert_release_file = hashes["before"] is None and current_hash == hashes["after"]
                    assert current_hash == hashes["before"] or inert_release_file, "Production changed: " + target + "/" + relative
                    if current is not None:
                        atomic_write(sftp, BACKUP + "/files/" + relative, current)
                atomic_write(sftp, BACKUP + "/selected.json", json.dumps(entries).encode())
            finally:
                sftp.close()
        app = clients["app"]
        was = clients["was"]
        old_cron = command(app, "crontab -l")
        assert old_cron.replace("\r\n", "\n") == (ARTIFACTS / "app/crontab.actual").read_text(encoding="utf-8")
        running = command(app, "ps -eo comm,args")
        assert not any("python" in line.split(" ", 1)[0] and "scripts/sync_ad_users.py" in line for line in running.splitlines()), "Legacy sync is running"
        sftp = app.open_sftp()
        atomic_write(sftp, BACKUP + "/crontab.before", old_cron.encode())
        paused = "".join("# Entra rollout paused: " + line if "scripts/sync_ad_users.py" in line and not line.lstrip().startswith("#") else line
                         for line in old_cron.splitlines(keepends=True))
        atomic_write(sftp, BACKUP + "/crontab.paused", paused.encode())
        sftp.close()
        command(app, "crontab " + BACKUP + "/crontab.paused")
        changed = True
        command(was, "sudo -S -p '' systemctl stop ai-craver", stdin_text=credential() + "\n")
        service_stopped = True
        print(command(was, ENV_PYTHON, stdin_text="BACKUP=" + repr(BACKUP) + "\n" + SNAPSHOT).strip())
        for target, entries in selected.items():
            sftp = clients[target].open_sftp()
            try:
                for relative in entries:
                    atomic_write(sftp, REMOTE + "/" + relative, (ARTIFACTS / ("stage-" + target) / relative).read_bytes(), mode=0o644)
            finally:
                sftp.close()
        migrate_env(was, app)
        migration_started = True
        print(python(was, "import json\nfrom app.core.user_directory_migration import ensure_directory_tables\nfrom app.core.user_directory import ensure_directory_sync_table\nprint(json.dumps(ensure_directory_tables(),default=str))\nensure_directory_sync_table()\n").strip())
        print(python(was, "BACKUP=" + repr(BACKUP) + "\n" + VERIFY).strip())
        # APP imports its minimal release with the new configuration before cron activation.
        print(python(app, "import json\nfrom app.core.user_directory_migration import migration_status\nfrom app.core.user_directory import directory_sync_status\nfrom app.core.self_check import track_job\nprint(json.dumps({'app_directory_ready':migration_status()['completed']},default=str))\n").strip())
        command(was, "sudo -S -p '' systemctl start ai-craver", stdin_text=credential() + "\n")
        service_stopped = False
        healthy = False
        for _ in range(15):
            try:
                health = command(was, "curl --fail --silent --max-time 3 http://127.0.0.1:3000/health", timeout=5)
                if health:
                    healthy = True
                    break
            except RuntimeError:
                pass
            time.sleep(2)
        assert healthy, "WAS health did not recover"
        command(was, "systemctl is-active ai-craver")
        sftp = app.open_sftp()
        atomic_write(sftp, BACKUP + "/crontab.next", (ARTIFACTS / "crontab.next").read_bytes())
        sftp.close()
        command(app, "crontab " + BACKUP + "/crontab.next")
        print(json.dumps({"deployed":{target:len(entries) for target, entries in selected.items()}, "service_active":True, "cron":"Entra 22:00", "rollback_directory":BACKUP}))
        (ARTIFACTS / "applied.json").write_text(json.dumps({"deployed":True,"time":time.time(),"backup":BACKUP}),encoding="utf-8")
    except Exception:
        if migration_started:
            # MariaDB DDL commits implicitly. Restoring the old AD writer after
            # any FK change would create two divergent permission authorities.
            # Keep the new code and paused legacy cron for a verified forward fix.
            try:
                command(clients["was"], "sudo -S -p '' systemctl start ai-craver", stdin_text=credential() + "\n")
            except Exception:
                pass
            print("FORWARD_REPAIR_REQUIRED: migration started; Entra code retained and legacy AD writer stays disabled; original files and identity snapshot preserved")
        elif changed:
            # Restore only the explicitly reviewed files. Extra new modules are inert.
            for target, entries in selected.items():
                sftp = clients[target].open_sftp()
                try:
                    for relative, hashes in entries.items():
                        if hashes["before"] is not None:
                            original = read(sftp, BACKUP + "/files/" + relative)
                            if original is not None:
                                atomic_write(sftp, REMOTE + "/" + relative, original, mode=0o644)
                    if target == "app":
                        original = read(sftp, BACKUP + "/app.env.before")
                        if original is not None:
                            atomic_write(sftp, REMOTE + "/.env", original)
                finally:
                    sftp.close()
            command(clients["app"], "crontab " + BACKUP + "/crontab.before")
            command(clients["was"], "sudo -S -p '' systemctl restart ai-craver", stdin_text=credential() + "\n")
            print("ROLLBACK: migration had not started; original code and cron restored")
        raise
    finally:
        for client in clients.values():
            client.close()


def refresh_chat_permissions():
    relative = "app/api/routes.py"
    selected_path = ARTIFACTS / "selected.json"
    selected = json.loads(selected_path.read_text(encoding="utf-8"))
    new = (ROOT / relative).read_bytes()
    ast.parse(new.decode("utf-8-sig"))
    client = connect("was")
    try:
        sftp = client.open_sftp()
        current = read(sftp, REMOTE + "/" + relative)
        assert digest(current) == selected["was"][relative]["after"], "WAS changed since verification"
        atomic_write(sftp, BACKUP + "/before_fresh_group_check.py", current)
        atomic_write(sftp, REMOTE + "/" + relative, new, mode=0o644)
        sftp.close()
        command(client, "sudo -S -p '' systemctl restart ai-craver", stdin_text=credential() + "\n")
        healthy = False
        for _ in range(15):
            try:
                command(client, "curl --fail --silent --max-time 3 http://127.0.0.1:3000/health", timeout=5)
                healthy = True
                break
            except RuntimeError:
                time.sleep(2)
        assert healthy, "WAS health did not recover"
        (ARTIFACTS / "stage-was" / relative).write_bytes(new)
        selected["was"][relative]["after"] = digest(new)
        selected_path.write_text(json.dumps(selected, indent=2),encoding="utf-8")
        print("Fresh group authorization check deployed; service healthy")
    finally:
        client.close()


def recover_before_migration():
    was, app = connect("was"), connect("app")
    try:
        guard = python(was, "from app.db.mariadb import fetch_one\nassert fetch_one(\"SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='directory_migrations'\")['n']==0, 'Migration exists; do not restore AD writer'\n")
        sftp = app.open_sftp()
        original = read(sftp, BACKUP + "/app.env.before")
        if original is not None:
            atomic_write(sftp, REMOTE + "/.env", original)
        sftp.close()
        command(app, "crontab " + BACKUP + "/crontab.before")
        command(was, "sudo -S -p '' systemctl start ai-craver", stdin_text=credential() + "\n")
        print("Original code, environment and cron restored; migration never started; service start requested")
    finally:
        was.close()
        app.close()


if __name__ == "__main__":
    if sys.argv[1:] == ["prepare"]:
        prepare()
    elif sys.argv[1:] == ["apply"]:
        apply()
    elif sys.argv[1:] == ["apply", "--recover-before-migration"]:
        recover_before_migration()
    elif sys.argv[1:] == ["apply", "--refresh-chat-permissions"]:
        refresh_chat_permissions()
    else:
        raise SystemExit("Use prepare or apply")
