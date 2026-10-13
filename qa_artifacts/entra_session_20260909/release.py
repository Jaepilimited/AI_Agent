"""Apply only the reviewed Entra session files with source CAS and rollback copies."""

from pathlib import Path
import sys

ART = Path(__file__).resolve().parent
ROOT = ART.parents[1]
sys.path = [p for p in sys.path if Path(p or ".").resolve() != ART]
sys.path.insert(0, str(ART.parent))

import ast
import difflib
import hashlib
import json

FILES = [
    "app/core/session_auth.py", "app/api/auth_api.py", "app/api/auth_middleware.py",
    "app/api/middleware.py", "app/api/entra_routes.py", "app/api/coa_finder_api.py",
    "app/main.py", "app/core/golden_runner.py", "app/core/self_check.py",
    "app/frontend/chat.js", "app/frontend/chat.html",
]
REMOTE = "/home/jeffrey/AI_Agent"
BACKUP = REMOTE + "/.release_backups/entra_session_20260909"


def digest(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def prepare():
    baseline = json.loads((ART / "inspection.json").read_text())
    selected = {}
    changes = []
    for relative in FILES:
        source = ROOT / relative
        data = source.read_bytes()
        before = (ART / "was" / relative).read_bytes() if baseline["files"][relative]["exists"] else None
        assert digest(before) == baseline["files"][relative].get("sha256")
        if before is not None and not relative.startswith("app/frontend/"):
            local_before = (ART / "baseline-local" / relative).read_text(encoding="utf-8-sig")
            assert local_before == before.decode("utf-8-sig").replace("\r\n", "\n"), relative
        if source.suffix == ".py":
            ast.parse(data.decode("utf-8-sig"))
        stage = ART / "stage" / relative
        stage.parent.mkdir(parents=True, exist_ok=True)
        stage.write_bytes(data)
        selected[relative] = {"before": digest(before), "after": digest(data)}
        changes.extend(difflib.unified_diff(
            (before or b"").decode("utf-8-sig").replace("\r\n", "\n").splitlines(True),
            data.decode("utf-8-sig").replace("\r\n", "\n").splitlines(True),
            fromfile="was/" + relative, tofile="release/" + relative,
        ))
    (ART / "selected.json").write_text(json.dumps(selected, indent=2), encoding="utf-8")
    (ART / "release.diff").write_text("".join(changes), encoding="utf-8")
    print(json.dumps({"prepared_files": len(selected), "backup": BACKUP}))


PERMISSIONS = r'''
import hashlib, json
from app.db.mariadb import get_maria_conn
conn = get_maria_conn()
try:
    result = {}
    queries = {
        "users": "SELECT id,ad_user_id,role,allowed_models,is_active FROM users ORDER BY id",
        "directory_permissions": "SELECT id,can_view_fi,can_view_visitor_analytics,deal_id_prefix "
                                 "FROM directory_users ORDER BY id",
        "groups": "SELECT * FROM access_groups ORDER BY id",
        "memberships": "SELECT * FROM user_groups ORDER BY id",
    }
    with conn.cursor() as cur:
        for name, query in queries.items():
            cur.execute(query)
            rows = cur.fetchall()
            encoded = json.dumps(rows, sort_keys=True, default=str).encode()
            result[name] = {"count": len(rows), "sha256": hashlib.sha256(encoded).hexdigest()}
    print("SESSION_RESULT=" + json.dumps(result))
finally:
    conn.rollback()
    conn.close()
'''

HEALTH = r'''
import time, requests
from app.config import get_settings
session = requests.Session()
session.trust_env = False
url = "http://127.0.0.1:" + str(get_settings().port) + "/health"
healthy = False
deadline = time.monotonic() + 42
while time.monotonic() < deadline:
    try:
        healthy = session.get(url, timeout=2).status_code == 200
    except requests.RequestException:
        pass
    if healthy:
        break
    time.sleep(2)
assert healthy, "Health check failed"
print("SESSION_RESULT=" + '{"health":200}')
'''

PROBES = r'''
import json, time, jwt, requests
from app.config import get_settings
from app.db.mariadb import fetch_one
settings = get_settings()
session = requests.Session()
session.trust_env = False
base = "http://127.0.0.1:" + str(settings.port)
result = {}
for path in ("/health", "/login", "/auth/entra/status", "/api/auth/methods", "/frontend/chat.js"):
    response = session.get(base + path, timeout=10)
    assert response.status_code == 200, path
    result[path] = response.status_code
    if path == "/api/auth/methods":
        methods = response.json()
        assert methods["password"] is False and methods["entra"] is True
        result["auth_methods"] = {"password": False, "entra": True}
    if path == "/frontend/chat.js":
        assert "Entra 로그인 완료".encode() in response.content and "Entra 로그인 필요".encode() in response.content
        assert "셀라 계정 있음".encode() not in response.content
        assert "X-Cella-Auth" in response.text
row = fetch_one("SELECT id FROM users WHERE entra_oid IS NOT NULL AND is_active=1 ORDER BY id LIMIT 1")
assert row, "Expected an existing linked account for legacy-cookie rejection check"
# An unproven old cookie is rejected even when that user's directory is Entra-linked.
# No Entra user session is fabricated and token values never leave this process.
old = jwt.encode({"user_id": row["id"], "exp": int(time.time()) + 300},
                 settings.jwt_secret_key, algorithm="HS256")
for method, path, expected in (
    ("GET", "/api/auth/me", 401), ("GET", "/api/admin/directory/users", 401),
    ("POST", "/v1/chat/completions", 401), ("GET", "/face-search/stats", 401),
    ("GET", "/face-search/thumb/entra-session-probe", 401),
    ("GET", "/api/harness/tree", 401), ("GET", "/", 302),
    ("GET", "/coa-finder", 302), ("GET", "/harness", 302),
    ("GET", "/frontend/chat.html", 302), ("GET", "/auth/google/login", 302),
):
    response = session.request(method, base + path, cookies={"token": old},
                               allow_redirects=False, timeout=10)
    assert response.status_code == expected, path
    assert response.headers.get("X-Cella-Auth") == "login-required", path
    if expected == 302:
        assert response.headers["location"] == "/login"
    result["legacy " + method + " " + path] = response.status_code
for path in ("/auth/entra/callback", "/users/auth/openid_connect/callback"):
    response = session.get(base + path, params={"error": "access_denied"}, allow_redirects=False, timeout=10)
    assert response.status_code == 400 and "X-Cella-Auth" not in response.headers
    result["public callback " + path] = response.status_code
print("SESSION_RESULT=" + json.dumps(result))
'''


def result_of(client, script):
    from entra_release_deploy_20260908 import python
    output = python(client, script, timeout=65)
    lines = [s[len("SESSION_RESULT="):] for s in output.splitlines() if s.startswith("SESSION_RESULT=")]
    assert len(lines) == 1, "Missing verification result"
    return json.loads(lines[0])


def apply():
    from entra_release_prepare_20260908 import connect, credential
    from entra_release_deploy_20260908 import read, atomic_write, mkdirs, command, python
    selected = json.loads((ART / "selected.json").read_text())
    assert list(selected) == FILES
    client = connect("was")
    originals, modes = {}, {}
    changed = False
    try:
        sftp = client.open_sftp()
        try:
            for relative, hashes in selected.items():
                assert digest((ART / "stage" / relative).read_bytes()) == hashes["after"]
                assert digest((ROOT / relative).read_bytes()) == hashes["after"], "Local source changed"
                current = read(sftp, REMOTE + "/" + relative)
                assert digest(current) == hashes["before"], "WAS source changed: " + relative
                originals[relative] = current
                modes[relative] = sftp.stat(REMOTE + "/" + relative).st_mode & 0o777 if current is not None else 0o644
            try:
                sftp.stat(BACKUP)
            except FileNotFoundError:
                pass
            else:
                raise RuntimeError("Release backup already exists; inspect before retry")
            before = result_of(client, PERMISSIONS)
            mkdirs(sftp, BACKUP)
            for relative, data in originals.items():
                if data is not None:
                    atomic_write(sftp, BACKUP + "/" + relative, data, mode=0o600)
            atomic_write(sftp, BACKUP + "/selected.json", json.dumps(selected).encode())
            atomic_write(sftp, BACKUP + "/permissions-before.json", json.dumps(before).encode())
            changed = True
            for relative in FILES:
                atomic_write(sftp, REMOTE + "/" + relative, (ART / "stage" / relative).read_bytes(), mode=modes[relative])
            loaded = python(client, "import app.main\nprint('SESSION_IMPORT_OK')\n", timeout=45)
            assert "SESSION_IMPORT_OK" in loaded
            command(client, "sudo -S -p '' systemctl restart ai-craver", stdin_text=credential() + "\n")
            health = result_of(client, HEALTH)
            active = command(client, "systemctl is-active ai-craver").strip()
            assert active == "active"
            probes = result_of(client, PROBES)
            after = result_of(client, PERMISSIONS)
            for relative, hashes in selected.items():
                assert digest(read(sftp, REMOTE + "/" + relative)) == hashes["after"]
            report = {"deployed_files": len(selected), "systemd": active, **health, "probes": probes,
                      "permissions_unchanged": before == after, "before": before, "after": after,
                      "backup": BACKUP}
            (ART / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            assert report["permissions_unchanged"], "Permission snapshot changed during deploy"
            print(json.dumps(report))
        except Exception as exc:
            if changed:
                for relative, data in originals.items():
                    if data is not None:
                        atomic_write(sftp, REMOTE + "/" + relative, data, mode=modes[relative])
                command(client, "sudo -S -p '' systemctl restart ai-craver", stdin_text=credential() + "\n")
                health = result_of(client, HEALTH)
                (ART / "rollback.json").write_text(json.dumps({"error_type": type(exc).__name__,
                    "restored_existing_files": True, **health}), encoding="utf-8")
                print(json.dumps({"rolled_back": True, "error_type": type(exc).__name__, **health}))
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
