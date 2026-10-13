"""Publish the reviewed briefing asset changes against an unchanged WAS snapshot."""
from __future__ import annotations

import base64
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
TASK = Path(__file__).resolve().parent
STAGE = TASK / "candidate"
SOURCES = ["app/frontend/personal-briefing.js", "app/static/style.css"]
TEST = "tests/frontend/test_briefing_document.py"
sys.path.insert(0, str(ROOT / "qa_artifacts"))


def remote(code):
    from feedback_release_20260908 import remote as relay
    return relay(code)


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def prepare():
    snapshot = remote(
        "import base64,json\nfrom pathlib import Path\npaths=" + repr(SOURCES)
        + "\nprint(json.dumps({p:base64.b64encode(Path(p).read_bytes()).decode('ascii') for p in paths}))"
    )
    plan = {}
    for name in SOURCES:
        original = base64.b64decode(snapshot[name])
        candidate = original.decode("utf-8").replace("\r\n", "\n")
        before = (TASK / "baseline" / name).read_text(encoding="utf-8").splitlines(keepends=True)
        after = (ROOT / name).read_text(encoding="utf-8").splitlines(keepends=True)
        hunks = 0
        for group in difflib.SequenceMatcher(None, before, after, autojunk=False).get_grouped_opcodes(8):
            old = "".join(before[group[0][1]:group[-1][2]])
            new = "".join(after[group[0][3]:group[-1][4]])
            assert old and candidate.count(old) == 1, "Production context changed: " + name
            candidate = candidate.replace(old, new, 1)
            hunks += 1
        output = STAGE / name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(candidate, encoding="utf-8", newline="\n")
        if hunks:
            plan[name] = {"before": sha(original), "after": sha(output.read_bytes())}
    assert plan, "No reviewed source changes"
    target = STAGE / TEST
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / TEST, target)
    (TASK / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(json.dumps({"prepared": list(plan), "candidate": str(STAGE)}))


def validate():
    plan = json.loads((TASK / "plan.json").read_text(encoding="utf-8"))
    for name, hashes in plan.items():
        assert sha((STAGE / name).read_bytes()) == hashes["after"], name
    subprocess.run(["node", "--check", str(STAGE / SOURCES[0])], check=True)
    check = subprocess.run(
        [sys.executable, "-m", "pytest", TEST, "-q", "--tb=short"],
        cwd=STAGE, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    (TASK / "candidate_tests.log").write_text(check.stdout + check.stderr, encoding="utf-8")
    print((check.stdout + check.stderr)[-3000:])
    assert check.returncode == 0, "Candidate browser regressions failed"
    (TASK / "validated.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")


def publish():
    plan = json.loads((TASK / "plan.json").read_text(encoding="utf-8"))
    assert plan == json.loads((TASK / "validated.json").read_text(encoding="utf-8"))
    files = {}
    for name, hashes in plan.items():
        blob = (STAGE / name).read_bytes()
        assert sha(blob) == hashes["after"], "Candidate changed: " + name
        files[name] = base64.b64encode(blob).decode("ascii")
    encoded = base64.b64encode(json.dumps({"plan": plan, "files": files}).encode()).decode("ascii")
    result = remote("""
import base64,hashlib,json,os,shutil,time
from pathlib import Path
payload=json.loads(base64.b64decode(""" + repr(encoded) + """))
root=Path.cwd()
release=root/'.feedback-releases'/time.strftime('briefing-ui-%Y%m%d-%H%M%S')
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
for name, hashes in payload['plan'].items():
    assert digest(root/name)==hashes['before'], 'Production changed: '+name
for name, encoded in payload['files'].items():
    target=release/'candidate'/name
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(base64.b64decode(encoded))
    assert digest(target)==payload['plan'][name]['after'], name
for name, hashes in payload['plan'].items():
    assert digest(root/name)==hashes['before'], 'Production changed during staging: '+name
for name in payload['files']:
    target=root/name
    backup=release/'backup'/name
    backup.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(target,backup)
    temp=target.with_name(target.name+'.briefing-ui.tmp')
    shutil.copy2(release/'candidate'/name,temp)
    os.replace(temp,target)
print(json.dumps({'published':True,'release':str(release),
    'hashes_match':all(digest(root/n)==h['after'] for n,h in payload['plan'].items())}))
""")
    (TASK / "publish_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    assert result["published"] and result["hashes_match"]


def verify():
    plan = json.loads((TASK / "plan.json").read_text(encoding="utf-8"))
    result = remote("""
import hashlib,json,subprocess
from pathlib import Path
import requests
plan=""" + repr(plan) + """
session=requests.Session()
session.trust_env=False
results={}
for name,hashes in plan.items():
    path='/' + name.removeprefix('app/')
    response=session.get('http://127.0.0.1:3000'+path,timeout=10)
    results[name]={'status':response.status_code,
        'served_hash_matches':hashlib.sha256(response.content).hexdigest()==hashes['after'],
        'file_hash_matches':hashlib.sha256(Path(name).read_bytes()).hexdigest()==hashes['after']}
health=session.get('http://127.0.0.1:3000/health',timeout=10)
active=subprocess.run(['systemctl','is-active','ai-craver'],capture_output=True,text=True,timeout=10)
print(json.dumps({'health':health.status_code,'service':active.stdout.strip(),'assets':results}))
""")
    (TASK / "verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    assert result["health"] == 200 and result["service"] == "active"
    assert all(item["status"] == 200 and item["served_hash_matches"] and item["file_hash_matches"]
               for item in result["assets"].values())


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    {"prepare": prepare, "validate": validate, "publish": publish, "verify": verify}[sys.argv[1]]()
