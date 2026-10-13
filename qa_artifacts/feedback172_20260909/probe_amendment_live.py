"""Read and preserve concurrent live source changes without changing any plan."""
from __future__ import annotations

import ast
import base64
import datetime as dt
import difflib
import hashlib
import json
from pathlib import Path
import sys

import release


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def normalize(blob):
    return blob.decode("utf-8").replace("\r\n", "\n")


def helper(text):
    return next(ast.get_source_segment(text, node) for node in ast.parse(text).body
                if isinstance(node, ast.FunctionDef) and node.name == "_outlook_result_answer")


def main():
    initial_plan_hash = sha(release.PLAN.read_bytes())
    assert initial_plan_hash == "990f3366af0b61bd262ddbd79c175a3d7be89f4f067cc2fc6b74a32f97fbd276"
    plan = release.read_json(release.PLAN)
    paths = [name for name, row in plan["files"].items() if row["deploy"]]
    snapshot = release.remote("""
import base64, datetime, hashlib, json
from pathlib import Path
root = Path.cwd().resolve()
assert str(root) == '/home/jeffrey/AI_Agent'
result = {'captured_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'files': {}}
for name in """ + repr(paths) + """:
    path = root / name
    assert not path.is_symlink() and path.resolve().is_relative_to(root)
    blob = path.read_bytes()
    result['files'][name] = {'sha256': hashlib.sha256(blob).hexdigest(),
        'modified_at_utc': datetime.datetime.fromtimestamp(path.stat().st_mtime, datetime.timezone.utc).isoformat(),
        'bytes': len(blob), 'content': base64.b64encode(blob).decode('ascii')}
print(json.dumps(result))
""")
    directory = release.ARTIFACT / ("amendment_live_" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    directory.mkdir(exist_ok=False)
    for name, row in snapshot["files"].items():
        blob = base64.b64decode(row.pop("content"))
        assert sha(blob) == row["sha256"]
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
        row["matches_phase1"] = row["sha256"] == plan["files"][name]["before"]
        row["matches_amendment"] = row["sha256"] == plan["files"][name]["after"]
    sql_name = "app/agents/sql_agent.py"
    live = normalize((directory / sql_name).read_bytes())
    phase1 = normalize((release.ARTIFACT / "phase1/release_candidate" / sql_name).read_bytes())
    amendment = normalize((release.CANDIDATE / sql_name).read_bytes())
    for label, prior in (("phase1", phase1), ("amendment", amendment)):
        diff = "".join(difflib.unified_diff(prior.splitlines(keepends=True), live.splitlines(keepends=True),
            fromfile=label + "/" + sql_name, tofile="current_live/" + sql_name))
        (directory / ("diff_vs_" + label + ".patch")).write_text(diff, encoding="utf-8")
    snapshot["outlook_helper"] = {
        "live": helper(live), "matches_phase1": helper(live) == helper(phase1),
        "matches_amendment": helper(live) == helper(amendment),
    }
    snapshot["sql_normalized_matches_amendment"] = live == amendment
    assert sha(release.PLAN.read_bytes()) == initial_plan_hash, "Plan changed during read-only probe"
    snapshot["frozen_plan_sha256"] = initial_plan_hash
    release.write_json(directory / "manifest.json", snapshot)
    print(json.dumps({"snapshot": str(directory), **snapshot}, ensure_ascii=True))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
