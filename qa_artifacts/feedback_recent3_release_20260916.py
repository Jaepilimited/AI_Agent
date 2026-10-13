"""Release only the verified fixes for CS thumbs-down reports 178, 179, 180."""
from pathlib import Path
import sys

import cs_bp_dashboard_release_20260916 as backend


STAGE = Path(__file__).resolve().parent / "feedback_recent3_20260916"
backend.STAGE = STAGE
backend.release.STAGE = STAGE
backend.release.TRACKED = [
    "app/agents/orchestrator.py", "app/agents/sql_agent.py", "prompts/sql_generator.txt",
    "app/flow/spec.py",
]
backend.release.NEW = [
    "app/core/cs_order_details.py", "tests/test_cs_order_details.py",
    "tests/test_cs_order_routing.py", "tests/test_cs_order_integration.py",
]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    {"prepare": backend.prepare, "stage": backend.release.stage, "publish": backend.publish}[sys.argv[1]]()
