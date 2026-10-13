"""Preserve the stopped amendment and review exact current live bytes unchanged.

All four deployment preimages equal their candidate hashes. Publishing this plan
runs the isolated regression suite and restarts the worker without introducing a
source difference. Concurrent production FX changes remain byte-for-byte intact.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import shutil
import sys

ARTIFACT = Path(__file__).resolve().parent
ARCHIVE = ARTIFACT / "phase2"
SNAPSHOT = ARTIFACT / "amendment_live_20260909T003629199083Z"
PREVIOUS_PLAN = "990f3366af0b61bd262ddbd79c175a3d7be89f4f067cc2fc6b74a32f97fbd276"
LIVE_SQL = "7ecaef837b14b09e4c41fc00519c065dfb0dd3cfb89b876d14fdff70df343b88"


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    plan_path = ARTIFACT / "release_plan.json"
    assert sha(plan_path.read_bytes()) == PREVIOUS_PLAN, "Stopped amendment plan changed"
    assert not ARCHIVE.exists(), "Phase 2 archive already exists"
    previous = load(plan_path)
    snapshot = load(SNAPSHOT / "manifest.json")
    assert snapshot["frozen_plan_sha256"] == PREVIOUS_PLAN
    assert snapshot["outlook_helper"]["matches_amendment"] is True
    assert snapshot["files"]["app/agents/sql_agent.py"]["sha256"] == LIVE_SQL
    deploy = [name for name, row in previous["files"].items() if row["deploy"]]
    assert set(deploy) == set(snapshot["files"])
    for name, row in previous["files"].items():
        assert sha((ARTIFACT / "release_candidate" / name).read_bytes()) == row["after"]
    for name in deploy:
        blob = (SNAPSHOT / name).read_bytes()
        assert sha(blob) == snapshot["files"][name]["sha256"]
        if name != "app/agents/sql_agent.py":
            assert sha(blob) == previous["files"][name]["after"]
        if name.endswith(".py"):
            compile(blob.decode("utf-8-sig"), name, "exec")

    names = ["release_plan.json", "release_review.diff", "amendment_test_review.diff",
             "release_publish_result.json", "release_verify_result.json", "smoke_evidence.json",
             "smoke.py", "release.py"]
    names += [path.relative_to(ARTIFACT).as_posix()
              for path in (ARTIFACT / "release_candidate").rglob("*") if path.is_file()]
    archive_records = {}
    for name in names:
        source, destination = ARTIFACT / name, ARCHIVE / name
        assert not source.is_symlink() and source.resolve().is_relative_to(ARTIFACT.resolve())
        blob = source.read_bytes()
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        assert destination.read_bytes() == blob
        archive_records[name] = {"sha256": sha(blob), "bytes": len(blob)}
    dump(ARCHIVE / "archive_manifest.json", {
        "archived_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "stopped_plan_sha256": PREVIOUS_PLAN,
        "publication_status": "Stopped by production preimage guard before writes; saved publish/verify evidence still belongs to phase 1.",
        "files": archive_records,
    })

    plan = copy.deepcopy(previous)
    plan["staged_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    plan["amends_plan_sha256"] = PREVIOUS_PLAN
    plan["amendment"] = "Adopt exact current live sources; validate and reload the already amended outlook helper."
    plan["production_captured_at_utc"] = snapshot["captured_at_utc"]
    plan["production_snapshot_directory"] = SNAPSHOT.name
    plan["preimage_basis"] = "Read-only live snapshot; each source candidate is byte-identical to its preimage."
    plan["reviewed_concurrent_changes"] = "Five logistics FX hunks already present in production are retained without modification."
    for name in deploy:
        blob = (SNAPSHOT / name).read_bytes()
        (ARTIFACT / "release_candidate" / name).write_bytes(blob)
        row = plan["files"][name]
        row.update(before=sha(blob), after=sha(blob), hunks=[])
        row.pop("workspace_sha256", None)
    for name, row in plan["files"].items():
        assert sha((ARTIFACT / "release_candidate" / name).read_bytes()) == row["after"]
        if row["deploy"]:
            assert row["before"] == row["after"]
    (ARTIFACT / "release_review.diff").write_text("", encoding="utf-8")
    dump(ARTIFACT / "live_adoption_review.json", {
        "source_changes_to_introduce": 0,
        "outlook_helper_matches_reviewed_amendment": True,
        "concurrent_delta_preserved_from": str(SNAPSHOT / "diff_vs_amendment.patch"),
        "validation_tests": plan["tests"],
        "intent": "Run existing isolated tests and restart after preimage verification, preserving every live source byte.",
    })
    dump(plan_path, plan)
    assert sha((ARTIFACT / "release.py").read_bytes()) == archive_records["release.py"]["sha256"]
    print(json.dumps({"staged": True, "archive": str(ARCHIVE),
        "plan_sha256": sha(plan_path.read_bytes()), "source_changes": 0,
        "deploy_preimages_equal_candidates": True, "tests": plan["tests"]}, ensure_ascii=True))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
