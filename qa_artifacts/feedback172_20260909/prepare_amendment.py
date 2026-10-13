"""Archive phase 1 and stage only the reviewed outlook disclosure amendment.

This command performs local file preparation only. It never invokes SSH, writes
production files, or restarts services. The existing publisher rechecks the new
plan's preimages before publishing.
"""
from __future__ import annotations

import ast
import copy
import datetime as dt
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import sys

ARTIFACT = Path(__file__).resolve().parent
ROOT = ARTIFACT.parents[1]
PHASE1 = ARTIFACT / "phase1"
FIRST_PLAN = "c361174d1aee5af4129cce8f1f4e8eb33f3b69363b6ae961eb37b783457bb49e"
SQL_PATH = "app/agents/sql_agent.py"
TEST_PATH = "tests/test_sales_outlook_integration.py"
OLD_LINES = (
    "    future_note = _future_period_note(sql)\n"
    "    if future_note:\n"
    '        answer = future_note.lstrip() + "\\n\\n" + answer\n'
)
NEW_LINES = (
    "    # The renderer discloses the validated month and future registration dates.\n"
    "    # A generic SQL upper bound can be exclusive and outside that month.\n"
)


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def helper_node(text):
    matches = [node for node in ast.parse(text).body
               if isinstance(node, ast.FunctionDef) and node.name == "_outlook_result_answer"]
    assert len(matches) == 1, "Expected one exact outlook answer helper"
    return matches[0]


def main():
    plan_path = ARTIFACT / "release_plan.json"
    assert sha(plan_path.read_bytes()) == FIRST_PLAN, "Phase 1 reviewed plan changed"
    assert not PHASE1.exists(), "Phase 1 archive already exists; refusing to overwrite or re-amend"
    previous = load(plan_path)
    publication = load(ARTIFACT / "release_publish_result.json")
    verification = load(ARTIFACT / "release_verify_result.json")
    assert publication["published"] and publication["hashes_match"]
    assert publication["plan_sha256"] == FIRST_PLAN and publication.get("restart_completed_at_utc")
    assert verification["hashes_match"], "Phase 1 live hash verification is missing"
    deploy = [name for name, row in previous["files"].items() if row["deploy"]]
    assert deploy == ["app/core/sales_outlook.py", SQL_PATH,
                      "prompts/sql_generator.txt", "app/core/feedback_inbox.py"]
    for name, row in previous["files"].items():
        assert sha((ARTIFACT / "release_candidate" / name).read_bytes()) == row["after"], (
            "Phase 1 candidate changed: " + name)
        if row["deploy"]:
            assert verification["hashes"][name] == row["after"], "Live verification mismatch: " + name

    old_blob = (ARTIFACT / "release_candidate" / SQL_PATH).read_bytes()
    old_text = old_blob.decode("utf-8")
    normalized_old = old_text.replace("\r\n", "\n")
    old_node = helper_node(normalized_old)
    old_function = ast.get_source_segment(normalized_old, old_node)
    current_blob = (ROOT / SQL_PATH).read_bytes()
    current_text = current_blob.decode("utf-8").replace("\r\n", "\n")
    current_function = ast.get_source_segment(current_text, helper_node(current_text))
    assert old_function.count(OLD_LINES) == 1
    assert current_function == old_function.replace(OLD_LINES, NEW_LINES, 1), (
        "Local helper includes changes beyond the reviewed three-line removal/two-line comment")

    # Patch the already published candidate's exact helper bytes, preserving all
    # production-specific surroundings and unchanged mixed newline conventions.
    raw_lines = old_text.splitlines(keepends=True)
    raw_function = "".join(raw_lines[old_node.lineno - 1:old_node.end_lineno])
    newline = "\r\n" if "\r\n" in raw_function else "\n"
    old_chunk = OLD_LINES.replace("\n", newline)
    new_chunk = NEW_LINES.replace("\n", newline)
    assert raw_function.count(old_chunk) == 1 and old_text.count(raw_function) == 1
    amended_function = raw_function.replace(old_chunk, new_chunk, 1)
    new_blob = old_text.replace(raw_function, amended_function, 1).encode("utf-8")
    assert new_blob != old_blob
    assert new_blob.replace(new_chunk.encode(), old_chunk.encode(), 1) == old_blob, (
        "Candidate changed beyond the intended snippet")
    compile(new_blob.decode("utf-8"), SQL_PATH, "exec")
    test_blob = (ROOT / TEST_PATH).read_bytes()
    assert sha(test_blob) != previous["files"][TEST_PATH]["after"], "Expected improved integration regression"
    compile(test_blob.decode("utf-8-sig"), TEST_PATH, "exec")

    # Preserve every original candidate and all published evidence before editing
    # any currently selected plan/candidate file. Hash-check every archived copy.
    archive_names = [
        "release_plan.json", "release_review.diff", "release_publish_result.json",
        "release_verify_result.json", "smoke_evidence.json", "smoke.py", "release.py",
    ]
    for path in (ARTIFACT / "release_candidate").rglob("*"):
        if path.is_file():
            assert not path.is_symlink() and path.resolve().is_relative_to(ARTIFACT.resolve())
            archive_names.append(path.relative_to(ARTIFACT).as_posix())
    manifest = {}
    for name in archive_names:
        source, destination = ARTIFACT / name, PHASE1 / name
        blob = source.read_bytes()
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        assert destination.read_bytes() == blob
        manifest[name] = {"sha256": sha(blob), "bytes": len(blob)}
    assert sha((PHASE1 / "release_plan.json").read_bytes()) == FIRST_PLAN
    assert sha((PHASE1 / "release_candidate" / TEST_PATH).read_bytes()) == previous["files"][TEST_PATH]["after"]
    dump(PHASE1 / "archive_manifest.json", {
        "archived_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "reviewed_plan_sha256": FIRST_PLAN,
        "old_integration_test": "release_candidate/" + TEST_PATH,
        "files": manifest,
    })

    amended = copy.deepcopy(previous)
    amended["staged_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    amended["amends_plan_sha256"] = FIRST_PLAN
    amended["amendment"] = "Remove redundant generic future-date notice from deterministic outlook prose."
    amended["production_captured_at_utc"] = verification["checked_at_utc"]
    amended["production_snapshot_directory"] = "phase1/release_candidate"
    amended["preimage_basis"] = "Phase 1 published candidate hashes, confirmed by its saved live verification."
    for name in deploy:
        row = amended["files"][name]
        row["before"] = previous["files"][name]["after"]
        row["hunks"] = []
    (ARTIFACT / "release_candidate" / SQL_PATH).write_bytes(new_blob)
    amended["files"][SQL_PATH].update(after=sha(new_blob), workspace_sha256=sha(current_blob),
        hunks=[{"function": "_outlook_result_answer", "status": "exact_snippet_amendment",
                "removed_lines": 3, "added_comment_lines": 2,
                "old_sha256": sha(old_chunk.encode()), "new_sha256": sha(new_chunk.encode())}])
    (ARTIFACT / "release_candidate" / TEST_PATH).write_bytes(test_blob)
    amended["files"][TEST_PATH]["after"] = sha(test_blob)

    changed_sources = [name for name in deploy
                       if amended["files"][name]["before"] != amended["files"][name]["after"]]
    assert changed_sources == [SQL_PATH], "Unexpected deployed file change"
    for name, row in amended["files"].items():
        assert sha((ARTIFACT / "release_candidate" / name).read_bytes()) == row["after"]
    review = "".join(difflib.unified_diff(normalized_old.splitlines(keepends=True),
        new_blob.decode("utf-8").replace("\r\n", "\n").splitlines(keepends=True),
        fromfile="phase1/" + SQL_PATH, tofile="amendment/" + SQL_PATH))
    (ARTIFACT / "release_review.diff").write_text(review, encoding="utf-8")
    old_test = (PHASE1 / "release_candidate" / TEST_PATH).read_text(encoding="utf-8")
    test_review = "".join(difflib.unified_diff(old_test.splitlines(keepends=True),
        test_blob.decode("utf-8").replace("\r\n", "\n").splitlines(keepends=True),
        fromfile="phase1/" + TEST_PATH, tofile="amendment/" + TEST_PATH))
    (ARTIFACT / "amendment_test_review.diff").write_text(test_review, encoding="utf-8")
    dump(plan_path, amended)
    print(json.dumps({"staged": True, "archive": str(PHASE1), "archive_files": len(manifest),
        "changed_deploy_files": changed_sources, "validation_test": TEST_PATH,
        "plan_sha256": sha(plan_path.read_bytes()), "review_diff": str(ARTIFACT / "release_review.diff")},
        ensure_ascii=True))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
