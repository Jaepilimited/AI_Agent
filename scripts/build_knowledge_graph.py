"""CLI entrypoint for Knowledge Map build.

Usage:
    python scripts/build_knowledge_graph.py              # incremental
    python scripts/build_knowledge_graph.py --force      # rebuild all
    python scripts/build_knowledge_graph.py --dry-run    # show plan only
    python scripts/build_knowledge_graph.py --bootstrap  # alias for --force
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.knowledge_map.builder import build  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Build SKIN1004 Knowledge Map")
    parser.add_argument("--force", action="store_true", help="Rebuild everything, ignore cache")
    parser.add_argument("--bootstrap", action="store_true", help="Alias for --force (first run)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be built, no Flash calls")
    args = parser.parse_args()

    force = args.force or args.bootstrap
    stats = asyncio.run(build(force=force, dry_run=args.dry_run))

    print("=" * 60)
    print("Knowledge Map build complete")
    print("=" * 60)
    for k, v in stats.items():
        print(f"  {k:20s}: {v}")
    print("=" * 60)
    return 0


def _tracked_main() -> int:
    """크론 실행을 job_runs 에 기록한다 — 죽어도 자가 점검이 알아채도록.

    (2026-08-05: app/knowledge_map 이 배포에서 누락돼 이 잡이 매일
     ModuleNotFoundError 로 죽고 있었는데 아무도 몰랐다.)
    """
    try:
        from app.core.self_check import track_job
    except Exception:
        return main()  # 기록 불가여도 본 작업은 수행한다
    with track_job("knowledge_map_build") as jr:
        rc = main()
        jr.set_note(f"exit={rc}")
    return rc


if __name__ == "__main__":
    raise SystemExit(_tracked_main())
