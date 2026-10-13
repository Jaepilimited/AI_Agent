"""Publish only this turn's BP consolidation and dashboard integration changes."""
from __future__ import annotations

import base64
import difflib
import json
from pathlib import Path
import sys

import cs_metrics_release_20260916 as release
import feedback_release_20260908 as access


ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "qa_artifacts/cs_bp_dashboard_release"
release.STAGE = STAGE
release.TRACKED = [
    p.relative_to(STAGE / "baseline").as_posix()
    for p in sorted((STAGE / "baseline").rglob("*"))
    if p.is_file() and p.read_text(encoding="utf-8") !=
    (ROOT / p.relative_to(STAGE / "baseline")).read_text(encoding="utf-8")
]
release.NEW = [
    "app/core/cs_dashboard.py", "tests/test_cs_dashboard.py",
    "tests/test_cs_dashboard_integration.py", "tests/test_cs_bp_source_ui.py",
    "tests/test_bp_consolidation.py",
]


def prepare():
    paths = release.TRACKED + release.NEW
    snapshot = access.remote("import base64,json\nfrom pathlib import Path\npaths=" + repr(paths) +
        "\nprint(json.dumps({'files':{p:base64.b64encode(Path(p).read_bytes()).decode() if Path(p).exists() else None for p in paths}}))")
    plan = {}
    for name in paths:
        raw = snapshot["files"][name]
        original = base64.b64decode(raw) if raw is not None else None
        current = (ROOT / name).read_text(encoding="utf-8")
        if name in release.TRACKED and original is not None:
            baseline = (STAGE / "baseline" / name).read_text(encoding="utf-8")
            before, after = baseline.splitlines(keepends=True), current.splitlines(keepends=True)
            groups = list(difflib.SequenceMatcher(None, before, after, autojunk=False).get_grouped_opcodes(3))
            assert groups, "No owned changes: " + name
            candidate = original.decode("utf-8").replace("\r\n", "\n")
            for group in groups:
                old = "".join(before[group[0][1]:group[-1][2]])
                new = "".join(after[group[0][3]:group[-1][4]])
                assert old and candidate.count(old) == 1, "Production hunk conflict: " + name
                candidate = candidate.replace(old, new, 1)
        else:
            assert name in release.NEW or name.startswith("tests/"), "Missing production source: " + name
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
        plan[name] = {"before": release.sha(original), "after": release.sha(blob)}
    (STAGE / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(json.dumps({"prepared": list(plan)}))


def publish():
    log = (STAGE / "final_pytest.txt").read_text(encoding="utf-8")
    assert " passed" in log and " failed" not in log and "ERRORS" not in log
    info = json.loads((STAGE / "remote_stage.json").read_text())
    result = access.remote("""
import hashlib,json,os,shutil
from pathlib import Path
root=Path.cwd()
release=Path(""" + repr(info["release"]) + """)
candidate,backup=release/'candidate',release/'backup'
plan=json.loads((release/'plan.json').read_text())
def sha(path):
 return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
for name,hashes in plan.items():
 assert sha(root/name)==hashes['before'],'Production changed: '+name
 assert sha(candidate/name)==hashes['after'],'Candidate changed: '+name
for name in plan:
 target=root/name
 saved=backup/name
 saved.parent.mkdir(parents=True,exist_ok=True)
 if target.exists():shutil.copy2(target,saved)
 target.parent.mkdir(parents=True,exist_ok=True)
 temp=target.with_name(target.name+'.cs-bp-release.tmp')
 shutil.copy2(candidate/name,temp)
 os.replace(temp,target)
print(json.dumps({'published':True,'release':str(release),'files':len(plan),'hashes_match':all(sha(root/n)==h['after'] for n,h in plan.items())}))
""")
    assert result["published"] and result["hashes_match"], result
    (STAGE / "publish_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    access.restart_service()
    result["restarted"] = True
    (STAGE / "publish_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    {"prepare": prepare, "stage": release.stage, "publish": publish}[sys.argv[1]]()
