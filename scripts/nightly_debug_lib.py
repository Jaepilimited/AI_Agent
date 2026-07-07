"""Pure, side-effect-light logic for the nightly debug/auto-fix system.

Orchestration with real side effects (subprocess calls that mutate the repo
or restart prod) lives in scripts/nightly_debug.py, which imports from here.
See docs/superpowers/specs/2026-07-07-nightly-debug-system-design.md.
"""

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# --- State ---

DEFAULT_STATE = {"last_run_at": None, "last_git_commit": None, "last_log_offset": 0}


def load_state(state_path: Path) -> dict:
    """Load the nightly-run state, or return DEFAULT_STATE if it doesn't exist yet."""
    if not state_path.exists():
        return dict(DEFAULT_STATE)
    return json.loads(state_path.read_text(encoding="utf-8"))


def save_state(state_path: Path, state: dict) -> None:
    """Persist the nightly-run state as JSON."""
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# --- Log error extraction ---

@dataclass
class LogError:
    raw: str
    kind: str  # "structured" | "traceback"


_ERROR_LEVEL_RE = re.compile(r'"level"\s*:\s*"(error|critical)"')
_TRACEBACK_START = "Traceback (most recent call last):"


def extract_new_errors(log_text: str, since_offset: int) -> "tuple[list, int]":
    """Return (errors, new_offset). Offsets are character positions into log_text."""
    new_text = log_text[since_offset:]
    errors: list = []
    lines = new_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if _TRACEBACK_START in line:
            block = [line]
            i += 1
            while i < len(lines) and (lines[i].startswith(" ") or lines[i].startswith("\t")):
                block.append(lines[i])
                i += 1
            if i < len(lines):
                block.append(lines[i])  # exception summary line, e.g. "AttributeError: ..."
                i += 1
            errors.append(LogError(raw="\n".join(block), kind="traceback"))
            continue
        if _ERROR_LEVEL_RE.search(line):
            errors.append(LogError(raw=line.strip(), kind="structured"))
        i += 1
    return errors, len(log_text)


# --- Git helpers ---


def get_current_commit(repo_dir: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo_dir),
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git rev-parse HEAD failed: {result.stderr}")
    return result.stdout.strip()


def has_uncommitted_changes(repo_dir: Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(repo_dir),
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    if result.returncode != 0:
        # Fail closed: if we can't determine cleanliness, treat as dirty so
        # auto-apply is skipped rather than proceeding on a false "clean".
        return True
    return bool(result.stdout.strip())


def get_changed_files(repo_dir: Path, since_commit: Optional[str]) -> list:
    """Files changed between since_commit and HEAD. Empty list if since_commit is falsy (first run)."""
    if not since_commit:
        return []
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{since_commit}..HEAD"], cwd=str(repo_dir),
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def get_file_diff(repo_dir: Path, since_commit: str, file_path: str) -> str:
    result = subprocess.run(
        ["git", "diff", f"{since_commit}..HEAD", "--", file_path], cwd=str(repo_dir),
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    return result.stdout


def diff_check_applies(repo_dir: Path, diff_text: str) -> bool:
    """Dry-run check: would this unified diff apply cleanly to the current worktree?"""
    result = subprocess.run(
        ["git", "apply", "--check", "-"], input=diff_text.encode("utf-8"), cwd=str(repo_dir),
        capture_output=True, timeout=30,
    )
    return result.returncode == 0


def apply_diff_to_worktree(repo_dir: Path, diff_text: str) -> bool:
    """Apply a unified diff to the working tree (NOT committed yet)."""
    result = subprocess.run(
        ["git", "apply", "-"], input=diff_text.encode("utf-8"), cwd=str(repo_dir),
        capture_output=True, timeout=30,
    )
    return result.returncode == 0


def discard_worktree_changes(repo_dir: Path, file_path: str) -> None:
    """Discard uncommitted changes to a single file (restore to last commit)."""
    subprocess.run(
        ["git", "checkout", "--", file_path], cwd=str(repo_dir),
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )


def commit_worktree_changes(repo_dir: Path, file_path: str, commit_message: str) -> bool:
    add = subprocess.run(
        ["git", "add", file_path], cwd=str(repo_dir),
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    if add.returncode != 0:
        return False
    commit = subprocess.run(
        ["git", "commit", "-m", commit_message], cwd=str(repo_dir),
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    return commit.returncode == 0


def revert_last_commit(repo_dir: Path) -> bool:
    result = subprocess.run(
        ["git", "revert", "--no-edit", "HEAD"], cwd=str(repo_dir),
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    return result.returncode == 0
