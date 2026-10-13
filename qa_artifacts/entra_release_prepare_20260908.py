"""Download only release baselines and aggregate metadata; never mutate servers."""
import difflib
import hashlib
import json
import os
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "qa_artifacts" / "entra_migration" / "release"
REMOTE = "/home/jeffrey/AI_Agent"
HOSTS = {"was": "10.1.150.5", "app": "10.1.150.105"}
FILES = [
    "app/api/admin_api.py", "app/api/admin_group_api.py", "app/api/auth_api.py",
    "app/api/auth_middleware.py", "app/api/entra_routes.py", "app/api/routes.py",
    "app/config.py", "app/core/briefing.py", "app/core/entra_auth.py",
    "app/core/feedback_inbox.py", "app/core/password_reset.py",
    "app/core/password_reset_google.py", "app/core/personal_briefing.py",
    "app/core/query_profile.py", "app/core/saved_questions.py", "app/core/self_check.py",
    "app/core/user_directory.py", "app/core/user_directory_migration.py",
    "app/db/mariadb.py", "app/db/models.py", "app/frontend/chat.html",
    "app/frontend/chat.js", "app/main.py", "app/reports/store.py",
    "scripts/sync_ad_users.py", "scripts/sync_entra_users.py", "deploy/crontab.app-server",
]


def credential():
    value = os.environ.get("CRAVER_SSH_PW", "")
    if not value:
        for line in (ROOT / "scripts/_run_fx_relay.cmd").read_text(encoding="ascii").splitlines():
            if line.lower().startswith("set craver_ssh_pw="):
                value = line.partition("=")[2]
                break
    if not value:
        raise RuntimeError("Missing SSH credential")
    return value


def connect(target):
    client = paramiko.SSHClient()
    client.load_host_keys(str(Path.home() / ".ssh/known_hosts"))
    if HOSTS[target] not in client.get_host_keys():
        # First contact to the configured APP VM; pin its key in this release's
        # own known_hosts so subsequent verification/deployment detects changes.
        pin = ARTIFACTS / "ssh_known_hosts"
        pin.parent.mkdir(parents=True, exist_ok=True)
        if not pin.exists():
            pin.write_text("", encoding="ascii")
        client.load_host_keys(str(pin))
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOSTS[target], username="jeffrey", password=credential(),
                   timeout=15, banner_timeout=15, auth_timeout=15,
                   allow_agent=False, look_for_keys=False)
    return client


def digest(data):
    return hashlib.sha256(data).hexdigest()


def prepare():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    metadata = {}
    for target in HOSTS:
        client = connect(target)
        try:
            sftp = client.open_sftp()
            entries, diffs = {}, []
            for relative in FILES:
                local = (ROOT / relative).read_bytes()
                try:
                    with sftp.open(REMOTE + "/" + relative, "rb") as source:
                        baseline = source.read()
                except FileNotFoundError:
                    baseline = None
                destination = ARTIFACTS / target / relative
                if baseline is not None:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if destination.exists() and destination.read_bytes() != baseline:
                        raise RuntimeError("Remote changed since original baseline: " + target + "/" + relative)
                    destination.write_bytes(baseline)
                before = (baseline or b"").decode("utf-8-sig").replace("\r\n", "\n").splitlines(keepends=True)
                after = local.decode("utf-8-sig").replace("\r\n", "\n").splitlines(keepends=True)
                delta = list(difflib.unified_diff(before, after, fromfile=target + "/" + relative,
                                                 tofile="release/" + relative, n=3))
                diffs.extend(delta)
                entries[relative] = {"remote_sha256": digest(baseline) if baseline is not None else None,
                                     "local_sha256": digest(local), "diff_lines": len(delta)}
            _, out, err = client.exec_command("crontab -l", timeout=15)
            cron = out.read()
            if out.channel.recv_exit_status() != 0 and target == "app":
                raise RuntimeError("Could not read " + target + " crontab")
            cron_path = ARTIFACTS / target / "crontab.actual"
            cron_path.parent.mkdir(parents=True, exist_ok=True)
            cron_path.write_bytes(cron)
            (ARTIFACTS / (target + ".diff")).write_text("".join(diffs), encoding="utf-8")
            metadata[target] = entries
            print(json.dumps({"target": target, "files": len(entries),
                              "changes": {k: v["diff_lines"] for k, v in entries.items() if v["diff_lines"]}}, sort_keys=True))
            sftp.close()
        finally:
            client.close()
    (ARTIFACTS / "manifest.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")


if __name__ == "__main__":
    prepare()
