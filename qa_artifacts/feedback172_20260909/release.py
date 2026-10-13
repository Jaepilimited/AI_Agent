"""Snapshot, review, publish, and verify only the accepted feedback 172 fix.

prepare is read-only on production. stage is entirely local. publish requires the
reviewed plan digest, validates in an isolated server directory, and checks every
production preimage again immediately before replacing the four application files.
Existing unrelated tests are validation inputs only and are never deployed.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import datetime as dt
import difflib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys

ARTIFACT = Path(__file__).resolve().parent
ROOT = ARTIFACT.parents[1]
BASE = ARTIFACT / "release_base"
CANDIDATE = ARTIFACT / "release_candidate"
PLAN = ARTIFACT / "release_plan.json"
sys.path.insert(0, str(ARTIFACT.parent))
import entra_access_count_20260908 as runner

EXISTING = (
    "app/agents/sql_agent.py", "prompts/sql_generator.txt", "app/core/feedback_inbox.py",
)
NEW = ("app/core/sales_outlook.py",)
NEW_TESTS = ("tests/test_sales_outlook.py", "tests/test_sales_outlook_integration.py")
EXISTING_TESTS = ("tests/test_named_period.py", "tests/test_feedback_resolution_sync.py")
DEPLOY = NEW + EXISTING  # The module must exist before any importing source is replaced.
ALL_TESTS = NEW_TESTS + EXISTING_TESTS
SNAPSHOT = DEPLOY + ALL_TESTS + ("tests/conftest.py", "requirements.txt")


def digest(blob):
    return hashlib.sha256(blob).hexdigest() if blob is not None else None


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def remote(code):
    runner.REMOTE_QUERY = code
    capture = io.StringIO()
    with contextlib.redirect_stdout(capture):
        status = runner.main()
    output = capture.getvalue()
    if status:
        raise RuntimeError(output[-5000:])
    for line in reversed(output.splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    raise RuntimeError("No structured remote result")


def manifest_files(manifest):
    return {row["path"]: row for row in manifest["files"]}


def production_dir():
    pointer = BASE / "production_current.json"
    name = read_json(pointer)["directory"] if pointer.exists() else "production"
    path = BASE / name
    assert path.resolve().is_relative_to(BASE.resolve()), "Production snapshot escaped artifact root"
    return path


def prepare(args):
    """Freeze both baselines; never overwrite a previous capture."""
    local_manifest_path = BASE / "local/manifest.json"
    if not local_manifest_path.exists():
        records = []
        for name in DEPLOY + NEW_TESTS:
            source = ROOT / name
            blob = source.read_bytes() if source.exists() else None
            if blob is not None:
                output = BASE / "local" / name
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(blob)
            records.append({"path": name, "exists": blob is not None,
                            "sha256": digest(blob), "bytes": len(blob) if blob is not None else None})
        write_json(local_manifest_path, {"captured_at_utc": utc_now(), "files": records})
    local_manifest = manifest_files(read_json(local_manifest_path))
    for name in EXISTING:
        assert local_manifest[name]["exists"], "Missing local baseline: " + name
        assert digest((BASE / "local" / name).read_bytes()) == local_manifest[name]["sha256"]
    for name in NEW + NEW_TESTS:
        assert not local_manifest[name]["exists"], "New local path existed at baseline: " + name

    production_root = production_dir()
    if args.refresh_production:
        production_root = BASE / ("production_refresh_" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    production_manifest = production_root / "manifest.json"
    if not production_manifest.exists():
        snapshot = remote("""
import base64, datetime, hashlib, importlib.util, json
from pathlib import Path
root = Path.cwd().resolve()
assert str(root) == '/home/jeffrey/AI_Agent'
paths = """ + repr(SNAPSHOT) + """
result = {'captured_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'files': [], 'content': {}, 'pytest_available': importlib.util.find_spec('pytest') is not None}
for name in paths:
    path = root / name
    assert not path.is_symlink(), 'Unexpected symlink: ' + name
    assert path.resolve().is_relative_to(root), 'Path escaped root: ' + name
    blob = path.read_bytes() if path.exists() else None
    result['files'].append({'path': name, 'exists': blob is not None,
        'sha256': hashlib.sha256(blob).hexdigest() if blob is not None else None,
        'bytes': len(blob) if blob is not None else None})
    result['content'][name] = base64.b64encode(blob).decode('ascii') if blob is not None else None
print(json.dumps(result))
""")
        records = manifest_files(snapshot)
        for name in EXISTING:
            assert records[name]["exists"], "Missing production source: " + name
        for name in NEW + NEW_TESTS:
            assert not records[name]["exists"], "New file already exists in production: " + name
        for name, encoded in snapshot.pop("content").items():
            if encoded is None:
                continue
            blob = base64.b64decode(encoded)
            assert digest(blob) == records[name]["sha256"]
            output = production_root / name
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(blob)
        write_json(production_manifest, snapshot)
        write_json(BASE / "production_current.json", {"directory": production_root.name,
                   "note": "Original production snapshot remains preserved in production/."})
    production = read_json(production_manifest)
    if args.audit_production:
        audit = remote("""
import datetime, hashlib, json
from pathlib import Path
names = """ + repr(DEPLOY + NEW_TESTS) + """
result = {'checked_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'files': {}}
for name in names:
    p = Path(name)
    result['files'][name] = {'exists': p.exists(),
        'sha256': hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None,
        'modified_at_utc': datetime.datetime.fromtimestamp(p.stat().st_mtime, datetime.timezone.utc).isoformat() if p.exists() else None}
print(json.dumps(result))
""")
        write_json(BASE / "production_audit.json", audit)
        print(json.dumps({"production_audit": audit}))
    # Some tests are deliberately not deployed. Freeze local copies as validation
    # inputs when no production copy exists, without adding them to DEPLOY.
    production_records = manifest_files(production)
    validation_manifest_path = BASE / "validation/manifest.json"
    if not validation_manifest_path.exists():
        validation_records = []
        for name in EXISTING_TESTS + ("tests/conftest.py",):
            if production_records[name]["exists"]:
                continue
            blob = (ROOT / name).read_bytes()
            output = BASE / "validation" / name
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(blob)
            validation_records.append({"path": name, "sha256": digest(blob), "source": "local test input only"})
        write_json(validation_manifest_path, {"captured_at_utc": utc_now(), "files": validation_records})
    if not production["pytest_available"]:
        support = BASE / "test_support.zip"
        if not support.exists():
            prior = ROOT / "qa_artifacts/feedback166_167_test_support.zip"
            assert prior.exists(), "Production has no pytest and test support is unavailable"
            shutil.copyfile(prior, support)
        write_json(BASE / "test_support_manifest.json", {"sha256": digest(support.read_bytes()),
                   "purpose": "Temporary isolated test imports only; never installed or deployed"})
    print(json.dumps({"prepared": True, "local_baseline": str(local_manifest_path),
                      "production_baseline": str(production_manifest),
                      "production_captured_at_utc": production["captured_at_utc"],
                      "pytest_available": production["pytest_available"]}))


def normalized(blob):
    return blob.decode("utf-8").replace("\r\n", "\n")


def owned_hunk(name, new):
    if name.endswith("feedback_inbox.py"):
        return '"id": 172' in new or "'id': 172" in new
    return "outlook" in new.lower() or "_future_period_note" in new or "_is_month_close_query" in new


def apply_owned_changes(name, baseline, current, production):
    before = normalized(baseline).splitlines(keepends=True)
    after = normalized(current).splitlines(keepends=True)
    candidate = normalized(production)
    hunks = []
    groups = difflib.SequenceMatcher(None, before, after, autojunk=False).get_grouped_opcodes(4)
    for group in groups:
        old = "".join(before[group[0][1]:group[-1][2]])
        new = "".join(after[group[0][3]:group[-1][4]])
        assert owned_hunk(name, new), "Unrecognized concurrent change; inspect hunk: " + name
        status = "applied"
        if old and candidate.count(old) == 1:
            candidate = candidate.replace(old, new, 1)
        elif candidate.count(new) == 1:
            status = "already_present_exact"
        elif (len([op for op in group if op[0] != "equal"]) == 1
              and next(op for op in group if op[0] != "equal")[0] == "insert"
              and "explicit_company = re.search(" not in new):
            # A local-only neighboring feature (the FX prompt loader) is absent
            # on production. Insert only our new lines at the exact, unique
            # unchanged right context; never transfer that neighboring feature.
            _, i1, i2, j1, j2 = next(op for op in group if op[0] != "equal")
            inserted = "".join(after[j1:j2])
            following = "".join(before[i2:group[-1][2]])
            assert owned_hunk(name, inserted), "Insertion is not release-owned"
            assert len(following.splitlines()) >= 2 and candidate.count(following) == 1, (
                "Unique insertion context unavailable: " + name + " line " + str(i1 + 1))
            if candidate.count(inserted + following) == 1:
                status = "already_present_exact_insertion"
            else:
                assert candidate.count(inserted) == 0, "Insertion exists at an unexpected location"
                candidate = candidate.replace(following, inserted + following, 1)
                status = "inserted_at_unique_right_context"
        elif name == "app/agents/sql_agent.py" and "explicit_company = re.search(" in new:
            # Reviewed intermediate version observed in the live snapshot: only
            # upgrade the precise assignment to the explicit company scope gate.
            previous = "        outlook_sql = _build_company_outlook_sql(query, brand_filter=brand_filter)\n"
            assert new.count("    try:\n") == new.count("    except ValueError:\n") == 1
            replacement = new.split("    try:\n", 1)[1].split("    except ValueError:\n", 1)[0]
            assert candidate.count(previous) == 1, "Unexpected intermediate outlook hook"
            updated = candidate.replace(previous, replacement, 1)
            assert updated.count(new) == 1, "Intermediate hook surrounding context changed"
            candidate = updated
            status = "upgraded_reviewed_intermediate_hook"
        else:
            raise AssertionError("Production context conflict: " + name + " baseline line " + str(group[0][1] + 1))
        hunks.append({"baseline_start": group[0][1] + 1, "baseline_end": group[-1][2],
                      "old_sha256": digest(old.encode()), "new_sha256": digest(new.encode()), "status": status})
    assert hunks, "No owned changes detected: " + name
    # Preserve unchanged live bytes even when previous uploads mixed CRLF and LF.
    raw_lines = production.decode("utf-8").splitlines(keepends=True)
    production_lines = normalized(production).splitlines(keepends=True)
    candidate_lines = candidate.splitlines(keepends=True)
    newline = "\r\n" if production.count(b"\r\n") > production.count(b"\n") / 2 else "\n"
    rebuilt = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, production_lines, candidate_lines, autojunk=False).get_opcodes():
        if tag == "equal":
            rebuilt.extend(raw_lines[i1:i2])
        else:
            rebuilt.extend(line.replace("\n", newline) for line in candidate_lines[j1:j2])
    return "".join(rebuilt).encode("utf-8"), hunks


def stage(args):
    local = manifest_files(read_json(BASE / "local/manifest.json"))
    prod_root = production_dir()
    prod_manifest = read_json(prod_root / "manifest.json")
    production = manifest_files(prod_manifest)
    selected_tests = tuple(args.test or ALL_TESTS)
    assert selected_tests and all(name in ALL_TESTS for name in selected_tests), "Unapproved test path"
    CANDIDATE.mkdir(parents=True, exist_ok=True)
    records, review = {}, []
    for name in DEPLOY:
        current = (ROOT / name).read_bytes()
        prod_path = prod_root / name
        original = prod_path.read_bytes() if prod_path.exists() else None
        assert digest(original) == production[name].get("sha256"), "Production snapshot changed: " + name
        if name in EXISTING:
            baseline = (BASE / "local" / name).read_bytes()
            assert digest(baseline) == local[name]["sha256"], "Local snapshot changed: " + name
            candidate, hunks = apply_owned_changes(name, baseline, current, original)
        else:
            assert original is None and not local[name]["exists"], "New source conflict: " + name
            candidate, hunks = current, []
        if name.endswith(".py"):
            compile(candidate.decode("utf-8-sig"), name, "exec")
        output = CANDIDATE / name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(candidate)
        records[name] = {"before": digest(original), "after": digest(candidate),
                         "workspace_sha256": digest(current), "deploy": True, "hunks": hunks}
        review.extend(difflib.unified_diff(normalized(original or b"").splitlines(keepends=True),
                      normalized(candidate).splitlines(keepends=True),
                      fromfile="production/" + name, tofile="candidate/" + name))
    for name in selected_tests + ("tests/conftest.py",):
        source = (ROOT / name) if name in NEW_TESTS else (prod_root / name)
        if not source.exists() and name not in NEW_TESTS:
            source = BASE / "validation" / name
        assert source.exists(), "Missing isolated test input: " + name
        blob = source.read_bytes()
        if name not in NEW_TESTS:
            expected = production[name].get("sha256") if production[name]["exists"] else next(
                row["sha256"] for row in read_json(BASE / "validation/manifest.json")["files"]
                if row["path"] == name)
            assert digest(blob) == expected, "Validation snapshot changed: " + name
        compile(blob.decode("utf-8-sig"), name, "exec")
        output = CANDIDATE / name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(blob)
        records[name] = {"after": digest(blob), "deploy": False}
    plan = {"feedback_id": 172, "feedback_created_on": "2026-09-08", "staged_at_utc": utc_now(),
            "production_captured_at_utc": prod_manifest["captured_at_utc"],
            "production_snapshot_directory": prod_root.name,
            "files": records, "tests": list(selected_tests),
            "test_support_sha256": digest((BASE / "test_support.zip").read_bytes())
                if not prod_manifest["pytest_available"] else None}
    (ARTIFACT / "release_review.diff").write_text("".join(review), encoding="utf-8")
    write_json(PLAN, plan)
    print(json.dumps({"staged": True, "plan": str(PLAN), "plan_sha256": digest(PLAN.read_bytes()),
                      "review_diff": str(ARTIFACT / "release_review.diff"),
                      "deploy_files": list(DEPLOY), "tests": list(selected_tests)}))


def verified_payload(plan_sha256):
    assert digest(PLAN.read_bytes()) == plan_sha256, "Reviewed plan digest mismatch"
    plan = read_json(PLAN)
    assert {name for name, row in plan["files"].items() if row["deploy"]} == set(DEPLOY)
    assert plan["tests"] and all(name in ALL_TESTS for name in plan["tests"])
    files = {}
    for name, row in plan["files"].items():
        blob = (CANDIDATE / name).read_bytes()
        assert digest(blob) == row["after"], "Candidate changed after review: " + name
        files[name] = base64.b64encode(blob).decode("ascii")
    support = None
    if plan["test_support_sha256"]:
        blob = (BASE / "test_support.zip").read_bytes()
        assert digest(blob) == plan["test_support_sha256"], "Test support changed"
        support = base64.b64encode(blob).decode("ascii")
    return {"plan": plan, "files": files, "test_support": support, "plan_sha256": plan_sha256}


def publish(args):
    assert args.plan_sha256, "publish requires --plan-sha256 from the reviewed stage result"
    payload = verified_payload(args.plan_sha256)
    result_path = ARTIFACT / "release_publish_result.json"
    if result_path.exists():
        previous = read_json(result_path)
        if previous.get("published") and previous.get("plan_sha256") == args.plan_sha256:
            expected = {name: row["after"] for name, row in payload["plan"]["files"].items()
                        if row["deploy"]}
            actual = remote("import hashlib,json\nfrom pathlib import Path\npaths=" + repr(list(expected))
                            + "\nprint(json.dumps({p:hashlib.sha256(Path(p).read_bytes()).hexdigest() "
                            "if Path(p).exists() else None for p in paths}))")
            assert actual == expected, "Published source changed before restart recovery"
            if not previous.get("restart_completed_at_utc"):
                restart_service()
                previous["restart_completed_at_utc"] = utc_now()
                write_json(result_path, previous)
            print(json.dumps(previous, ensure_ascii=True))
            return
    encoded = base64.b64encode(json.dumps(payload).encode()).decode("ascii")
    result = remote(PUBLISH_REMOTE.replace("__PAYLOAD__", repr(encoded)))
    write_json(result_path, result)
    if not result.get("published") or not result.get("hashes_match"):
        print(json.dumps(result, ensure_ascii=True))
        raise SystemExit(1)
    restart_service()
    result["restart_completed_at_utc"] = utc_now()
    write_json(result_path, result)
    print(json.dumps(result, ensure_ascii=True))


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


def verify(args):
    plan = read_json(PLAN)
    expected = {name: row["after"] for name, row in plan["files"].items() if row["deploy"]}
    code = VERIFY_REMOTE.replace("__EXPECTED__", repr(expected))
    result = remote(code)
    write_json(ARTIFACT / "release_verify_result.json", result)
    print(json.dumps(result, ensure_ascii=True))
    if args.require_published:
        assert result["hashes_match"], "Published hashes do not match"
        assert result["service"]["active"] == "active", "Service is not active"
        assert result["health"]["status"] == 200, "WAS health is not ready"
        assert result["feedback"] and result["feedback"]["status"] == "done", "Feedback remains open"


PUBLISH_REMOTE = r'''
import base64, datetime, hashlib, io, json, os, shutil, subprocess, time, zipfile
from pathlib import Path
payload = json.loads(base64.b64decode(__PAYLOAD__))
root = Path.cwd().resolve()
assert str(root) == '/home/jeffrey/AI_Agent'
plan = payload['plan']
deploy = {name: row for name, row in plan['files'].items() if row['deploy']}
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
def check_preimages():
    for name, row in deploy.items():
        path = root / name
        assert not path.is_symlink() and path.resolve().is_relative_to(root), 'Unsafe target: ' + name
        assert sha(path) == row['before'], 'Production preimage changed: ' + name
check_preimages()
release = root / '.feedback-releases' / ('172-' + time.strftime('%Y%m%d-%H%M%S') + '-' + plan['staged_at_utc'][-7:-1].replace(':', ''))
assert release.resolve().is_relative_to(root), 'Release staging directory escaped root'
stage, backup = release / 'candidate', release / 'backup'
stage.mkdir(parents=True, exist_ok=False)
for path in (root / 'app').rglob('*.py'):
    assert not path.is_symlink() and path.resolve().is_relative_to(root), 'Unsafe support source'
    target = stage / path.relative_to(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
shutil.copytree(root / 'prompts', stage / 'prompts')
for name, encoded in payload['files'].items():
    path = stage / name
    assert path.resolve().is_relative_to(stage)
    blob = base64.b64decode(encoded)
    assert hashlib.sha256(blob).hexdigest() == plan['files'][name]['after']
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)
    if name.endswith('.py'):
        compile(blob.decode('utf-8-sig'), name, 'exec')
support = release / 'test-support'
if payload['test_support']:
    raw = base64.b64decode(payload['test_support'])
    assert hashlib.sha256(raw).hexdigest() == plan['test_support_sha256']
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for name in archive.namelist():
            assert not Path(name).is_absolute() and '..' not in Path(name).parts
        archive.extractall(support)
env = dict(os.environ)
for key in list(env):
    if any(word in key.upper() for word in ('PASSWORD', 'SECRET', 'TOKEN', 'API_KEY', 'SSH_PW')):
        env.pop(key)
env.update(PYTHONPATH=str(support) + ':' + str(stage), PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
           PYTHONDONTWRITEBYTECODE='1', MARIADB_HOST='127.0.0.1', MARIADB_PORT='1',
           MARIADB_USER='isolated_release_test', MARIADB_PASSWORD='unused',
           JWT_SECRET_KEY='isolated-release-test-' + 'x' * 48)
bootstrap = "import socket,sys\ndef blocked(*a,**k): raise RuntimeError('Live network access is disabled during release tests')\nsocket.socket.connect=blocked\nsocket.socket.connect_ex=blocked\nsocket.create_connection=blocked\nimport pytest\nsys.exit(pytest.main(sys.argv[1:]))"
command = [str(root / 'venv/bin/python'), '-c', bootstrap, '-p', 'pytest_asyncio.plugin',
           *plan['tests'], '-q', '--tb=short', '-p', 'no:cacheprovider']
check = subprocess.run(command, cwd=stage, env=env, capture_output=True, text=True, timeout=40)
result = {'published': False, 'release': str(release), 'plan_sha256': payload['plan_sha256'],
          'test_exit': check.returncode, 'test_output': (check.stdout + check.stderr)[-10000:]}
if not check.returncode:
    check_preimages()
    backup.mkdir(parents=True)
    for name, row in deploy.items():
        path = root / name
        if path.exists():
            saved = backup / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, saved)
    (release / 'plan.json').write_text(json.dumps(plan, indent=2), encoding='utf-8')
    applied = []
    try:
        for name, row in deploy.items():
            path = root / name
            assert sha(path) == row['before'], 'Production changed while writing: ' + name
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + '.feedback172.tmp')
            assert not temporary.exists(), 'Stale atomic-write file: ' + name
            shutil.copy2(stage / name, temporary)
            if path.exists():
                os.chmod(temporary, path.stat().st_mode & 0o777)
            with temporary.open('rb') as stream:
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            applied.append(name)
        result['hashes_match'] = all(sha(root / name) == row['after'] for name, row in deploy.items())
        assert result['hashes_match'], 'Post-write hash mismatch'
        result['published'] = True
        result['published_at_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    except Exception:
        for name in reversed(applied):
            path, saved = root / name, backup / name
            assert path.resolve().is_relative_to(root)
            if saved.exists():
                temporary = path.with_name(path.name + '.feedback172.rollback.tmp')
                shutil.copy2(saved, temporary)
                os.replace(temporary, path)
            else:
                path.unlink()
        raise
(release / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result))
'''


VERIFY_REMOTE = r'''
import datetime, hashlib, json, re, subprocess, urllib.request
from pathlib import Path
import pymysql
from app.config import get_settings
expected = __EXPECTED__
result = {'checked_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
result['hashes'] = {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() if Path(name).exists() else None
                    for name in expected}
result['hashes_match'] = result['hashes'] == expected
active = subprocess.run(['systemctl', 'is-active', 'ai-craver'], capture_output=True, text=True, timeout=10)
details = subprocess.run(['systemctl', 'show', 'ai-craver', '--property=MainPID,ActiveEnterTimestamp,NRestarts'],
                         capture_output=True, text=True, timeout=10)
result['service'] = {'active': active.stdout.strip(), 'details': details.stdout.strip()}
service_exec = subprocess.run(['systemctl', 'show', 'ai-craver', '--property=ExecStart', '--value'],
                             capture_output=True, text=True, timeout=10).stdout
ports = re.findall(r'--port(?:\s+|=)(\d+)', service_exec)
ports += re.findall(r'(?:--bind|-b)(?:\s+|=)(?:[0-9.]+|\[::\]):(\d+)', service_exec)
ports = list(dict.fromkeys([int(port) for port in ports] + [8000, 3000]))
result['service']['candidate_ports'] = ports
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
def check_health(url, timeout):
    try:
        with opener.open(url, timeout=timeout) as response:
            return {'url': url, 'status': response.status,
                    'body': response.read(1500).decode('utf-8', 'replace')}
    except Exception as exc:
        return {'url': url, 'status': None, 'error_type': type(exc).__name__, 'error': str(exc)[:500]}
result['was_health_attempts'] = []
for port in ports[:4]:
    health = check_health('http://127.0.0.1:' + str(port) + '/health', 3)
    result['was_health_attempts'].append(health)
    if health['status'] == 200:
        break
result['health'] = result['was_health_attempts'][-1]
result['web_health'] = check_health('http://10.1.100.5/health', 5)
s = get_settings()
assert s.mariadb_host == '10.1.200.5', 'Expected production database'
conn = pymysql.connect(host=s.mariadb_host, port=int(s.mariadb_port), user=s.mariadb_user,
    password=s.mariadb_password, database=s.mariadb_database, charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor, connect_timeout=10, read_timeout=10)
try:
    with conn.cursor() as cur:
        cur.execute('SET TRANSACTION READ ONLY')
        conn.begin()
        cur.execute('SELECT id, created_at, status, handled_note FROM message_feedback '
                    'WHERE id=%s AND DATE(created_at)=%s', (172, '2026-09-08'))
        result['feedback'] = cur.fetchone()
finally:
    conn.rollback()
    conn.close()
print(json.dumps(result, default=str, ensure_ascii=True))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--audit-production", action="store_true")
    prepare_parser.add_argument("--refresh-production", action="store_true")
    stage_parser = sub.add_parser("stage")
    stage_parser.add_argument("--test", action="append", choices=ALL_TESTS)
    publish_parser = sub.add_parser("publish")
    publish_parser.add_argument("--plan-sha256", required=True)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--require-published", action="store_true")
    args = parser.parse_args()
    {"prepare": prepare, "stage": stage, "publish": publish, "verify": verify}[args.command](args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
