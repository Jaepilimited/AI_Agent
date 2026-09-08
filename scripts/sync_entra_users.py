"""Refresh Cella employee profiles from Entra; preserve Admin-managed grants."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def main() -> int:
    from app.core.user_directory_migration import ensure_directory_tables, migration_status
    from app.core.user_directory import ensure_directory_sync_table, sync_directory
    if "--dry-run" in sys.argv:
        print(json.dumps(migration_status(), default=str, ensure_ascii=True))
        return 0
    ensure_directory_tables()
    ensure_directory_sync_table()
    from app.core.self_check import track_job
    with track_job("entra_directory_sync") as job:
        result = sync_directory()
        print(json.dumps(result, ensure_ascii=True))
        if not result["ok"]:
            raise RuntimeError(result["error"])
        job.set_note(f"Entra directory users: {result['synced_users']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
