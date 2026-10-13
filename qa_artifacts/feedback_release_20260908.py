"""Prepare/review and publish only the fixes for feedback 166 and 167.

Preserves concurrent edits by applying selected contextual changes to an exact
production snapshot, then rechecking hashes before replacing any live files.
"""
from __future__ import annotations

import base64
import contextlib
import difflib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import entra_access_count_20260908 as runner

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "qa_artifacts" / "feedback166_167_release"
TRACKED = {
    "app/agents/gws_agent.py": "answer_statistics",
    "app/core/google_workspace.py": "def list_calendar_history(",
    "app/core/chart.py": "pointHitRadius",
    "app/frontend/chat.js": "_sameYearDaily",
    "app/core/feedback_inbox.py": '"id": 166',
}
NEW = ["app/core/calendar_stats.py"]
TESTS = [
    "tests/test_gws_calendar_stats.py", "tests/test_calendar_history_api.py",
    "tests/test_gws_digest.py", "tests/test_gws_gmail.py",
    "tests/test_feedback_resolution_sync.py", "tests/test_chart_many_series.py",
    "tests/test_chart_yoy.py", "tests/test_chart_series_label.py",
    "tests/frontend/test_chart_daily_ticks.py",
]


def digest(blob):
    return hashlib.sha256(blob).hexdigest() if blob is not None else None


def remote(code):
    runner.REMOTE_QUERY = code
    capture = io.StringIO()
    with contextlib.redirect_stdout(capture):
        status = runner.main()
    output = capture.getvalue()
    if status:
        raise RuntimeError(output[-4000:])
    for line in reversed(output.splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    raise RuntimeError("No structured remote result")


def prepare():
    paths = list(TRACKED) + NEW + TESTS
    snapshot = remote("""
import base64, json
from pathlib import Path
paths = """ + repr(paths) + """
print(json.dumps({'files': {name: base64.b64encode(Path(name).read_bytes()).decode('ascii')
                          if Path(name).exists() else None for name in paths}}))
""")
    plan = {}
    for name in paths:
        raw = snapshot["files"][name]
        original = base64.b64decode(raw) if raw is not None else None
        current = (ROOT / name).read_text(encoding="utf-8")
        if name in TRACKED:
            assert original is not None, f"Missing production source: {name}"
            candidate = original.decode("utf-8").replace("\r\n", "\n")
            baseline = subprocess.check_output(["git", "show", f"HEAD:{name}"], cwd=ROOT).decode("utf-8")
            before, after = baseline.splitlines(keepends=True), current.splitlines(keepends=True)
            hunks = 0
            for group in difflib.SequenceMatcher(None, before, after, autojunk=False).get_grouped_opcodes(10):
                old = "".join(before[group[0][1]:group[-1][2]])
                new = "".join(after[group[0][3]:group[-1][4]])
                if TRACKED[name] not in new:
                    continue
                assert old and candidate.count(old) == 1, f"Production hunk conflict: {name}"
                candidate = candidate.replace(old, new, 1)
                hunks += 1
            assert hunks, f"No owned changes selected: {name}"
        else:
            candidate = current
            if name in NEW:
                assert original is None or original.decode("utf-8").replace("\r\n", "\n") == candidate, f"New source already exists: {name}"
        blob = candidate.encode("utf-8")
        output = STAGE / name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(blob)
        plan[name] = {"before": digest(original), "after": digest(blob)}
    (STAGE / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(json.dumps({"prepared": list(plan), "stage": str(STAGE)}, ensure_ascii=True))


def publish():
    plan = json.loads((STAGE / "plan.json").read_text(encoding="utf-8"))
    previous_path = STAGE / "publish_result.json"
    previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path.exists() else {}
    if previous.get("published") and previous.get("restart_exit"):
        actual = remote("import hashlib,json\nfrom pathlib import Path\npaths=" + repr(list(plan)) +
                        "\nprint(json.dumps({p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths}))")
        assert all(actual[name] == hashes["after"] for name, hashes in plan.items()), "Published source changed before restart"
        restart_service()
        previous["restart_exit"] = 0
        previous["restart_error"] = ""
        previous_path.write_text(json.dumps(previous, indent=2), encoding="utf-8")
        print(json.dumps(previous, ensure_ascii=True))
        return
    files = {}
    for name, hashes in plan.items():
        blob = (STAGE / name).read_bytes()
        assert digest(blob) == hashes["after"], f"Candidate changed: {name}"
        files[name] = base64.b64encode(blob).decode("ascii")
    support = base64.b64encode((ROOT / "qa_artifacts/feedback166_167_test_support.zip").read_bytes()).decode("ascii")
    payload = base64.b64encode(json.dumps({"plan": plan, "files": files, "test_support": support}).encode()).decode("ascii")
    result = remote("""
import base64, hashlib, io, json, os, shutil, subprocess, time, zipfile
from pathlib import Path
payload = json.loads(base64.b64decode(""" + repr(payload) + """))
root = Path.cwd()
release = root / '.feedback-releases' / time.strftime('166-167-%Y%m%d-%H%M%S')
stage, backup = release / 'candidate', release / 'backup'
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
for name, hashes in payload['plan'].items():
    assert sha(root / name) == hashes['before'], 'Production changed before staging: ' + name
stage.mkdir(parents=True)
for path in (root / 'app').rglob('*.py'):
    target = stage / path.relative_to(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
if (root / 'prompts').exists():
    shutil.copytree(root / 'prompts', stage / 'prompts')
for name, encoded in payload['files'].items():
    path = stage / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(encoded))
    if name.endswith('.py'):
        compile(path.read_text(encoding='utf-8'), name, 'exec')
support = release / 'test-support'
with zipfile.ZipFile(io.BytesIO(base64.b64decode(payload['test_support']))) as archive:
    assert all(not Path(name).is_absolute() and '..' not in Path(name).parts for name in archive.namelist())
    archive.extractall(support)
env = dict(os.environ, PYTHONPATH=str(support) + ':' + str(stage), PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
check = subprocess.run([str(root / 'venv/bin/python'), '-m', 'pytest', '-p', 'pytest_asyncio.plugin',
    'tests/test_gws_calendar_stats.py', 'tests/test_calendar_history_api.py',
    'tests/test_gws_digest.py', 'tests/test_gws_gmail.py', 'tests/test_feedback_resolution_sync.py',
    'tests/test_chart_many_series.py', 'tests/test_chart_yoy.py', 'tests/test_chart_series_label.py',
    '-q', '--tb=short'], cwd=stage, env=env, capture_output=True, text=True, timeout=40)
if check.returncode:
    print(json.dumps({'published': False, 'stage': str(stage), 'test_output': (check.stdout + check.stderr)[-7000:]}))
else:
    for name, hashes in payload['plan'].items():
        assert sha(root / name) == hashes['before'], 'Production changed during validation: ' + name
    for name in payload['files']:
        source, target = stage / name, root / name
        saved = backup / name
        saved.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.copy2(target, saved)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + '.feedback-166-167.tmp')
        shutil.copy2(source, temp)
        os.replace(temp, target)
    restart = subprocess.run(['sudo', '-n', 'systemctl', 'restart', 'ai-craver'], capture_output=True, text=True, timeout=25)
    result = {'published': True, 'release': str(release), 'test_output': check.stdout[-2000:],
              'restart_exit': restart.returncode, 'restart_error': restart.stderr[-500:],
              'hashes_match': all(sha(root / name) == hashes['after'] for name, hashes in payload['plan'].items())}
    print(json.dumps(result))
""")
    (STAGE / "publish_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    if result.get("published") and result.get("restart_exit") and result.get("hashes_match"):
        restart_service()
        result["restart_exit"] = 0
        result["restart_error"] = ""
        (STAGE / "publish_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True))
    if not result.get("published") or result.get("restart_exit") or not result.get("hashes_match"):
        raise SystemExit(1)


def restart_service():
    password = os.environ.get("CRAVER_SSH_PW", "")
    if not password:
        for line in (ROOT / "scripts/_run_fx_relay.cmd").read_text(encoding="ascii").splitlines():
            if line.lower().startswith("set craver_ssh_pw="):
                password = line.partition("=")[2]
                break
    assert password, "SSH credential unavailable"
    client = runner.paramiko.SSHClient()
    client.load_host_keys(str(Path.home() / ".ssh/known_hosts"))
    try:
        client.connect("10.1.150.5", username="jeffrey", password=password,
                       allow_agent=False, look_for_keys=False, timeout=10, auth_timeout=15)
        stdin, stdout, stderr = client.exec_command("sudo -S -p '' -- systemctl restart ai-craver", timeout=45)
        stdin.write(password + "\n")
        stdin.flush()
        stdin.channel.shutdown_write()
        error = stderr.read().decode("utf-8", "replace")
        status = stdout.channel.recv_exit_status()
        if status:
            raise RuntimeError(error.replace(password, "[redacted]")[-500:])
    finally:
        client.close()


def refresh():
    """Prepare a follow-up for our new module after an observed live formatting issue."""
    previous = json.loads((STAGE / "plan.json").read_text(encoding="utf-8"))
    names = list(previous)
    actual = remote("import hashlib,json\nfrom pathlib import Path\npaths=" + repr(names) +
                    "\nprint(json.dumps({p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths}))")
    for name, hashes in previous.items():
        assert actual[name] == hashes["after"], "Published files changed: " + name
        hashes["before"] = hashes["after"]
    for name in ("app/core/calendar_stats.py", "tests/test_gws_calendar_stats.py"):
        content = (ROOT / name).read_text(encoding="utf-8").encode("utf-8")
        (STAGE / name).write_bytes(content)
        previous[name]["after"] = digest(content)
    (STAGE / "plan.json").write_text(json.dumps(previous, indent=2), encoding="utf-8")
    print(json.dumps({"refreshed": ["app/core/calendar_stats.py", "tests/test_gws_calendar_stats.py"]}))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    {"prepare": prepare, "publish": publish, "refresh": refresh}[sys.argv[1]]()
