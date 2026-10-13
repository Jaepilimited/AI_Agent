"""Deploy the reviewed three COA files only; abort on unreviewed production drift."""
from pathlib import Path
import sys

ART = Path(__file__).resolve().parent
ROOT = ART.parents[1]
sys.path.insert(0, str(ART.parent))

import ast
import difflib
import hashlib
import json

FILES = ["app/core/coa_finder.py", "app/api/coa_finder_api.py", "app/static/coa_finder.html"]
REMOTE = "/home/jeffrey/AI_Agent"
BACKUP = REMOTE + "/.release_backups/coa_latest_20260909"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def prepare():
    selected, diff = {}, []
    for relative in FILES:
        before = (ART / "baseline-local" / relative).read_bytes()
        after = (ROOT / relative).read_bytes()
        assert before != after, "Selected file has no change: " + relative
        if relative.endswith(".py"):
            ast.parse(after.decode("utf-8-sig"))
        stage = ART / "stage" / relative
        stage.parent.mkdir(parents=True, exist_ok=True)
        stage.write_bytes(after)
        selected[relative] = {"before": digest(before), "after": digest(after)}
        diff.extend(difflib.unified_diff(
            before.decode("utf-8-sig").replace("\r\n", "\n").splitlines(True),
            after.decode("utf-8-sig").replace("\r\n", "\n").splitlines(True),
            fromfile="before/" + relative, tofile="release/" + relative,
        ))
    (ART / "selected.json").write_text(json.dumps(selected, indent=2), encoding="utf-8")
    (ART / "release.diff").write_text("".join(diff), encoding="utf-8")
    print(json.dumps({"prepared_files": len(selected), "backup": BACKUP}))


HEALTH = r'''
import time, requests
from app.config import get_settings
session = requests.Session()
session.trust_env = False
base = "http://127.0.0.1:" + str(get_settings().port)
deadline = time.monotonic() + 42
healthy = False
while time.monotonic() < deadline:
    try:
        healthy = session.get(base + "/health", timeout=2).status_code == 200
    except requests.RequestException:
        pass
    if healthy:
        break
    time.sleep(2)
assert healthy, "Health check failed"
response = session.get(base + "/api/auth/methods", timeout=5)
assert response.status_code == 200
assert response.json()["password"] is False and response.json()["entra"] is True
print("COA_RESULT=" + '{"health":200,"password":false,"entra":true}')
'''

PROBE = r'''
import json
from app.core import coa_finder as cf
from pathlib import Path
old = cf.DriveFile("old", "COA_FE103C_old.pdf", 10, "", "2026-09-01T00:00:00Z")
new = cf.DriveFile("new", "COA_FE103C_new.pdf", 20, "", "2026-09-08T03:30:00Z")
verdict = cf.classify_lot("FE103C", [old, new])
assert verdict.status == cf.FOUND and [f.id for f in verdict.files] == ["new"]
assert "최신" in verdict.note and "2026-09-08" in verdict.note
tie = cf.DriveFile("tie", "COA_FE103C_tie.pdf", 30, "", new.modified)
assert cf.classify_lot("FE103C", [new, tie]).status == cf.MANY
unknown = cf.DriveFile("unknown", "COA_FE103C_unknown.pdf", 40, "")
assert cf.classify_lot("FE103C", [new, unknown]).status == cf.MANY
assert cf.classify_lot("FE103", [new]).status != cf.FOUND
def fake_search(creds, query, **kwargs):
    if "MSDS" not in query:
        return []
    return [{"id": file.id, "name": "MSDS Toning Toner 210ml_" + file.id + ".pdf",
             "size": file.size, "modifiedTime": file.modified} for file in [old, new]]
result = list(cf.find_all(None, [cf.Row("A", "Toning Toner 210ml", "", 1)], search=fake_search))[0]
assert result.msds.status == cf.FOUND and [f.id for f in result.msds.files] == ["new"]
html = Path("app/static/coa_finder.html").read_text(encoding="utf-8")
assert "최신" in html and "수정일" in html
print("COA_RESULT=" + json.dumps({"coa_latest": True, "msds_latest": True,
    "ties_and_unknown_dates_manual": True, "wrong_lot_rejected": True, "page_updated": True}))
'''


def evaluate(client, source):
    from entra_release_deploy_20260908 import python
    raw = python(client, source, timeout=60)
    lines = [line[len("COA_RESULT="):] for line in raw.splitlines() if line.startswith("COA_RESULT=")]
    assert len(lines) == 1, "Verification result missing"
    return json.loads(lines[0])


def apply():
    from entra_release_prepare_20260908 import connect, credential
    from entra_release_deploy_20260908 import read, atomic_write, mkdirs, command
    selected = json.loads((ART / "selected.json").read_text())
    assert list(selected) == FILES
    client = connect("was")
    changed = False
    originals, modes, drift, staged = {}, {}, [], {}
    try:
        sftp = client.open_sftp()
        try:
            for relative, hashes in selected.items():
                assert digest((ROOT / relative).read_bytes()) == hashes["after"], "Local file changed"
                staged[relative] = (ART / "stage" / relative).read_bytes()
                assert digest(staged[relative]) == hashes["after"]
                original = read(sftp, REMOTE + "/" + relative)
                assert original is not None, "Missing production file: " + relative
                snapshot = ART / "was" / relative
                snapshot.parent.mkdir(parents=True, exist_ok=True)
                snapshot.write_bytes(original)
                originals[relative] = original
                modes[relative] = sftp.stat(REMOTE + "/" + relative).st_mode & 0o777
                if digest(original) != hashes["before"]:
                    drift.append(relative)
            if drift:
                print(json.dumps({"production_drift": drift, "mutated": False}))
                raise RuntimeError("Review downloaded production versions before applying")
            try:
                sftp.stat(BACKUP)
            except FileNotFoundError:
                pass
            else:
                raise RuntimeError("Backup already exists; inspect before retry")
            mkdirs(sftp, BACKUP)
            for relative, original in originals.items():
                atomic_write(sftp, BACKUP + "/" + relative, original, mode=0o600)
            atomic_write(sftp, BACKUP + "/selected.json", json.dumps(selected).encode())
            for relative, hashes in selected.items():
                assert digest(read(sftp, REMOTE + "/" + relative)) == hashes["before"], "WAS changed during backup"
            changed = True
            for relative in FILES:
                atomic_write(sftp, REMOTE + "/" + relative, staged[relative], mode=modes[relative])
            behavior = evaluate(client, PROBE)
            command(client, "sudo -S -p '' systemctl restart ai-craver", stdin_text=credential() + "\n")
            health = evaluate(client, HEALTH)
            active = command(client, "systemctl is-active ai-craver").strip()
            assert active == "active"
            for relative, hashes in selected.items():
                assert digest(read(sftp, REMOTE + "/" + relative)) == hashes["after"]
            report = {"deployed_files": len(selected), "systemd": active, **behavior, **health, "backup": BACKUP}
            (ART / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report))
        except Exception:
            if changed:
                for relative, original in originals.items():
                    atomic_write(sftp, REMOTE + "/" + relative, original, mode=modes[relative])
                command(client, "sudo -S -p '' systemctl restart ai-craver", stdin_text=credential() + "\n")
                health = evaluate(client, HEALTH)
                assert command(client, "systemctl is-active ai-craver").strip() == "active"
                for relative, original in originals.items():
                    assert digest(read(sftp, REMOTE + "/" + relative)) == digest(original), "Rollback hash mismatch"
                (ART / "rollback.json").write_text(json.dumps({"restored": True, **health}), encoding="utf-8")
                print(json.dumps({"rolled_back": True, **health}))
            raise
        finally:
            sftp.close()
    finally:
        client.close()


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["prepare"]:
            prepare()
        elif sys.argv[1:] == ["apply"]:
            apply()
        else:
            raise ValueError("Expected prepare or apply")
    except Exception as exc:
        print(json.dumps({"ok": False, "error_type": type(exc).__name__}))
        sys.exit(1)
