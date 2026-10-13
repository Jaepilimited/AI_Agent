"""Review, validate, and publish only the notification drawer JavaScript fix."""
from __future__ import annotations

import base64
import contextlib
import difflib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[2]
TASK = Path(__file__).resolve().parent
SOURCE = "app/frontend/chat.js"
TESTS = ["tests/frontend/test_notifications_drawer.py",
         "tests/frontend/test_entra_directory_management.py"]


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def normalized(path):
    return path.read_bytes().replace(b"\r\n", b"\n")


def remote(code):
    sys.path.insert(0, str(TASK.parent))
    from feedback_release_20260908 import remote as relay
    return relay(code)


def snapshot():
    result = remote("import base64,hashlib,json\nfrom pathlib import Path\np=Path('app/frontend/chat.js')\n"
                    "b=p.read_bytes()\nprint(json.dumps({'sha256':hashlib.sha256(b).hexdigest(),"
                    "'sha256_lf':hashlib.sha256(b.replace(bytes([13,10]),bytes([10]))).hexdigest(),"
                    "'file':base64.b64encode(b).decode('ascii')}))")
    blob = base64.b64decode(result.pop("file"))
    target = TASK / "snapshots" / result["sha256"] / SOURCE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(blob)
    result["path"] = str(target)
    (TASK / "latest_snapshot.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


def verify_web(expected_raw, validated):
    sys.path.insert(0, str(ROOT / "qa_artifacts" / "feedback172_20260909"))
    import web_health_probe
    web_health_probe.ARTIFACT = TASK
    web_health_probe.REMOTE = r'''
import hashlib, http.client, json, subprocess
expected = ''' + repr(expected_raw) + r'''
validated = ''' + repr(validated) + r'''
def get(path):
    conn = http.client.HTTPConnection("127.0.0.1", 80, timeout=10)
    try:
        conn.request("GET", path, headers={"Host": "10.1.100.5", "Connection": "close"})
        response = conn.getresponse()
        return response.status, response.read(), response.getheader("X-Cella-Auth")
    finally:
        conn.close()
asset_status, asset, _ = get("/frontend/chat.js")
health, _, _ = get("/health")
unauth, _, header = get("/api/notifications")
nginx = subprocess.run(["systemctl", "is-active", "nginx"], capture_output=True, text=True, timeout=5)
print(json.dumps({"asset_status": asset_status, "served_hash_matches": hashlib.sha256(asset).hexdigest() == expected,
    "validated_contents_match": hashlib.sha256(asset.replace(b"\r\n", b"\n")).hexdigest() == validated,
    "health": health, "unauth_status": unauth, "auth_header": header,
    "nginx": nginx.stdout.strip(), "checked_via": "Web host loopback through nginx with production Host header"}))
'''
    with contextlib.redirect_stdout(io.StringIO()):
        web_health_probe.main()
    evidence = json.loads((TASK / "web_health_evidence.json").read_text(encoding="utf-8"))
    assert evidence.get("remote_exit_code") == 0, evidence
    return evidence["web_host_observations"]


def prepare():
    before = (TASK / "baseline" / SOURCE).read_bytes()
    old = before.decode("utf-8").replace("\r\n", "\n")
    new = normalized(ROOT / SOURCE).decode("utf-8")
    start, end = "  var _notifTimer = null;", "  function openStatusDrawer()"
    assert old.count(start) == new.count(start) == 1
    assert old.count(end) == new.count(end) == 1
    assert old[:old.index(start)] == new[:new.index(start)], "Changes outside notification scope"
    assert old[old.index(end):] == new[new.index(end):], "Changes outside notification scope"
    assert 4000 < len(old[old.index(start):old.index(end)]) < 12000
    assert old != new, "No notification changes"
    candidate = TASK / "candidate" / SOURCE
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_bytes(new.encode("utf-8"))
    plan = {"source": SOURCE, "before": sha(before), "after": sha(candidate.read_bytes()),
            "tests": TESTS, "scope": "Notification drawer only; no service restart or database writes"}
    (TASK / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    (TASK / "release.diff").write_text("".join(difflib.unified_diff(
        old.splitlines(True), new.splitlines(True), fromfile="production/" + SOURCE,
        tofile="candidate/" + SOURCE)), encoding="utf-8")
    print(json.dumps(plan))


def validate():
    plan = json.loads((TASK / "plan.json").read_text(encoding="utf-8"))
    candidate = TASK / "candidate" / SOURCE
    assert sha(candidate.read_bytes()) == plan["after"]
    assert sha(normalized(ROOT / SOURCE)) == plan["after"], "Local test source differs"
    subprocess.run(["node", "--check", str(candidate)], check=True)
    result = subprocess.run(["python", "-m", "pytest", *TESTS, "-q", "--tb=short"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    (TASK / "candidate_tests.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    print((result.stdout + result.stderr)[-2500:])
    assert result.returncode == 0, "Browser regression checks failed"
    assert sha(normalized(ROOT / SOURCE)) == plan["after"], "Source changed during validation"
    checked = {"plan": plan, "test_hashes": {p: sha((ROOT / p).read_bytes()) for p in TESTS}}
    (TASK / "validated.json").write_text(json.dumps(checked, indent=2), encoding="utf-8")


def publish():
    plan = json.loads((TASK / "plan.json").read_text(encoding="utf-8"))
    checked = json.loads((TASK / "validated.json").read_text(encoding="utf-8"))
    assert checked["plan"] == plan
    for name, digest in checked["test_hashes"].items():
        assert sha((ROOT / name).read_bytes()) == digest, "Tests changed after validation"
    blob = (TASK / "candidate" / SOURCE).read_bytes()
    assert sha(blob) == plan["after"]
    payload = {"plan": plan, "file": base64.b64encode(blob).decode("ascii")}
    encoded = base64.b64encode(json.dumps(payload).encode()).decode("ascii")
    result = remote(r'''
import base64, datetime, hashlib, json, os, shutil
from pathlib import Path
from uuid import uuid4
payload = json.loads(base64.b64decode(''' + repr(encoded) + r'''))
plan = payload["plan"]
assert plan["source"] == "app/frontend/chat.js"
root = Path.cwd()
assert str(root) == "/home/jeffrey/AI_Agent"
target = root / plan["source"]
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
assert digest(target) == plan["before"], "Production changed since the reviewed snapshot"
blob = base64.b64decode(payload["file"])
assert hashlib.sha256(blob).hexdigest() == plan["after"]
release = root / ".release_backups" / ("notifications_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid4().hex[:8])
release.mkdir(parents=True)
backup = release / "chat.js"
shutil.copy2(target, backup)
assert digest(backup) == plan["before"]
temp = target.with_name("chat.js.notification-" + uuid4().hex + ".tmp")
temp.write_bytes(blob)
assert digest(temp) == plan["after"]
assert digest(target) == plan["before"], "Production changed during staging"
os.replace(temp, target)
assert digest(target) == plan["after"]
print(json.dumps({"published": True, "source": plan["source"], "sha256": digest(target),
    "backup": str(backup), "published_at": datetime.datetime.now().isoformat()}))
''')
    (TASK / "published.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


def verify():
    plan = json.loads((TASK / "plan.json").read_text(encoding="utf-8"))
    expected_raw = plan["after"]
    latest = TASK / "latest_snapshot.json"
    if latest.exists():
        snapshot = json.loads(latest.read_text(encoding="utf-8"))
        assert snapshot["sha256_lf"] == plan["after"], "Live source differs from validated content"
        expected_raw = snapshot["sha256"]
    result = remote(r'''
import hashlib, json, subprocess
from pathlib import Path
import requests
expected = ''' + repr(expected_raw) + r'''
validated = ''' + repr(plan["after"]) + r'''
session = requests.Session()
session.trust_env = False
results = {}
for origin in ["http://127.0.0.1:3000"]:
    asset = session.get(origin + "/frontend/chat.js", timeout=10)
    health = session.get(origin + "/health", timeout=10)
    unauth = session.get(origin + "/api/notifications", timeout=10)
    results[origin] = {"asset_status": asset.status_code,
        "served_hash_matches": hashlib.sha256(asset.content).hexdigest() == expected,
        "validated_contents_match": hashlib.sha256(asset.content.replace(b"\r\n", b"\n")).hexdigest() == validated,
        "health": health.status_code, "unauth_status": unauth.status_code,
        "auth_header": unauth.headers.get("X-Cella-Auth")}
active = subprocess.run(["systemctl", "is-active", "ai-craver"], capture_output=True, text=True, timeout=10)
print(json.dumps({"service": active.stdout.strip(), "origins": results,
    "file_hash_matches": hashlib.sha256(Path("app/frontend/chat.js").read_bytes()).hexdigest() == expected,
    "live_sha256": hashlib.sha256(Path("app/frontend/chat.js").read_bytes()).hexdigest()}))
''')
    # The Web VM accepts the desktop network; WAS-to-Web traffic is restricted.
    opener = build_opener(ProxyHandler({}))
    def get(path):
        try:
            response = opener.open("http://10.1.100.5" + path, timeout=10)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, response.read(), response.headers
    try:
        asset_status, asset, _ = get("/frontend/chat.js")
        health_status, _, _ = get("/health")
        auth_status, _, auth_headers = get("/api/notifications")
        result["origins"]["http://10.1.100.5"] = {
            "asset_status": asset_status, "served_hash_matches": sha(asset) == expected_raw,
            "validated_contents_match": sha(asset.replace(b"\r\n", b"\n")) == plan["after"],
            "health": health_status, "unauth_status": auth_status,
            "auth_header": auth_headers.get("X-Cella-Auth"),
        }
    except OSError as exc:
        result["desktop_web_error"] = {"type": type(exc).__name__, "error": str(exc)}
        result["origins"]["http://10.1.100.5"] = verify_web(expected_raw, plan["after"])
    (TASK / "verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    assert result["service"] == "active" and result["file_hash_matches"]
    assert all(row["asset_status"] == row["health"] == 200 and row["served_hash_matches"]
               and row["validated_contents_match"]
               and row["unauth_status"] == 401 and row["auth_header"] == "login-required"
               for row in result["origins"].values())


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    {"snapshot": snapshot, "prepare": prepare, "validate": validate, "publish": publish, "verify": verify}[sys.argv[1]]()
