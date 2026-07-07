"""Pure, side-effect-light logic for the nightly debug/auto-fix system.

Orchestration with real side effects (subprocess calls that mutate the repo
or restart prod) lives in scripts/nightly_debug.py, which imports from here.
See docs/superpowers/specs/2026-07-07-nightly-debug-system-design.md.
"""

import json
from pathlib import Path


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
