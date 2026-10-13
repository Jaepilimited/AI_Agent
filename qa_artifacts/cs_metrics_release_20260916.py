"""Stage and publish only CS-source changes against an exact production snapshot."""
from __future__ import annotations

import base64
import difflib
import hashlib
import json
from pathlib import Path
import sys

import feedback_release_20260908 as access

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "qa_artifacts/cs_metrics_release"
TRACKED = [
    "app/agents/orchestrator.py", "app/agents/sql_agent.py", "app/agents/sql_tool_agent.py",
    "app/config.py", "app/core/bigquery.py", "app/core/safety.py", "app/core/schema_watch.py",
    "app/core/security.py", "app/core/value_lists.py", "app/frontend/chat.js", "prompts/sql_generator.txt",
]
NEW = ["app/core/cs_metrics.py", "docs/CS_METRICS.md", "tests/test_cs_metrics_contract.py",
       "tests/test_cs_metrics_routing.py", "tests/test_cs_metrics_sources.py"]


def sha(blob):
    return hashlib.sha256(blob).hexdigest() if blob is not None else None


def prepare():
    paths = TRACKED + NEW
    snapshot = access.remote("import base64,json\nfrom pathlib import Path\npaths=" + repr(paths) +
        "\nprint(json.dumps({'files':{p:base64.b64encode(Path(p).read_bytes()).decode() if Path(p).exists() else None for p in paths}}))")
    plan = {}
    for name in paths:
        raw = snapshot["files"][name]
        original = base64.b64decode(raw) if raw is not None else None
        current = (ROOT / name).read_text(encoding="utf-8")
        if name in TRACKED:
            assert original is not None, name
            baseline = (STAGE / "baseline" / name).read_text(encoding="utf-8")
            candidate = original.decode("utf-8").replace("\r\n", "\n")
            before, after = baseline.splitlines(keepends=True), current.splitlines(keepends=True)
            groups = list(difflib.SequenceMatcher(None, before, after, autojunk=False).get_grouped_opcodes(3))
            assert groups, "No owned changes: " + name
            for group in groups:
                old = "".join(before[group[0][1]:group[-1][2]])
                new = "".join(after[group[0][3]:group[-1][4]])
                assert old and candidate.count(old) == 1, "Production hunk conflict: " + name + " at " + str(group[0][1])
                candidate = candidate.replace(old, new, 1)
        else:
            assert original is None or original.decode("utf-8").replace("\r\n", "\n") == current, "Existing source: " + name
            candidate = current
        dest = STAGE / "candidate" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        blob = candidate.encode("utf-8")
        dest.write_bytes(blob)
        if original is not None:
            saved = STAGE / "production_before" / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(original)
        plan[name] = {"before": sha(original), "after": sha(blob)}
    (STAGE / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(json.dumps({"prepared": list(plan)}, ensure_ascii=True))


def stage():
    plan = json.loads((STAGE / "plan.json").read_text())
    files = {}
    for name, hashes in plan.items():
        blob = (STAGE / "candidate" / name).read_bytes()
        assert sha(blob) == hashes["after"], name
        files[name] = base64.b64encode(blob).decode()
    payload = base64.b64encode(json.dumps({"plan": plan, "files": files}).encode()).decode()
    result = access.remote("""
import base64,hashlib,json,shutil,time
from pathlib import Path
p=json.loads(base64.b64decode(""" + repr(payload) + """))
root=Path.cwd()
release=root/'.cs-releases'/time.strftime('%Y%m%d-%H%M%S')
stage=release/'candidate'
for name,h in p['plan'].items():
 path=root/name
 digest=hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
 assert digest==h['before'], 'Production changed: '+name
stage.mkdir(parents=True)
for name in ('app','prompts'):
 shutil.copytree(root/name,stage/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
for name,encoded in p['files'].items():
 path=stage/name
 path.parent.mkdir(parents=True,exist_ok=True)
 path.write_bytes(base64.b64decode(encoded))
 if name.endswith('.py'):
  compile(path.read_text(encoding='utf-8'),name,'exec')
(release/'plan.json').write_text(json.dumps(p['plan']))
print(json.dumps({'stage':str(stage),'release':str(release),'files':len(p['files'])}))
""")
    (STAGE / "remote_stage.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


def publish():
    test_log = (STAGE / "final_pytest.txt").read_text(encoding="utf-8")
    assert " passed" in test_log and " failed" not in test_log and "ERRORS" not in test_log, "Final test run did not pass"
    info = json.loads((STAGE / "remote_stage.json").read_text())
    result = access.remote("""
import hashlib,json,os,shutil
from pathlib import Path
root=Path.cwd()
release=Path(""" + repr(info["release"]) + """)
stage,backup=release/'candidate',release/'backup'
plan=json.loads((release/'plan.json').read_text())
def sha(path):
 return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
for name,h in plan.items():
 assert sha(root/name)==h['before'],'Production changed: '+name
 assert sha(stage/name)==h['after'],'Candidate changed: '+name
for name in plan:
 source,target=stage/name,root/name
 saved=backup/name
 saved.parent.mkdir(parents=True,exist_ok=True)
 if target.exists():
  shutil.copy2(target,saved)
 target.parent.mkdir(parents=True,exist_ok=True)
 temp=target.with_name(target.name+'.cs-release.tmp')
 shutil.copy2(source,temp)
 os.replace(temp,target)
print(json.dumps({'published':True,'release':str(release),'hashes_match':all(sha(root/n)==h['after'] for n,h in plan.items())}))
""")
    assert result["published"] and result["hashes_match"], result
    (STAGE / "publish_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    result["value_cache"] = access.remote("""
import json
from app.config import get_settings
from app.core import value_lists
s=get_settings()
assert s.mariadb_host=='10.1.200.5' and s.mariadb_database=='ai'
names=[n for n in value_lists.REGISTRY if n.startswith(('DomesticCS','OverseasCS'))]
out={}
for name in names:
 out.update(value_lists.refresh(name))
assert set(out)==set(names),'Incomplete CS value cache refresh'
print(json.dumps(out))
""")
    access.restart_service()
    result["restarted"] = True
    (STAGE / "publish_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    {"prepare": prepare, "stage": stage, "publish": publish}[sys.argv[1]]()
