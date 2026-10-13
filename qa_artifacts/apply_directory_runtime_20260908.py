"""Scoped table-name migration; keep pre-existing worktree bytes for review."""
from pathlib import Path
import re

files = [
    "app/api/auth_api.py", "app/api/auth_middleware.py", "app/api/admin_group_api.py",
    "app/api/routes.py", "app/db/mariadb.py", "app/main.py", "app/core/self_check.py",
]
base = Path("qa_artifacts/entra_migration/baseline/root")
for name in files:
    path = Path(name)
    raw = path.read_bytes()
    backup = base / path
    backup.parent.mkdir(parents=True, exist_ok=True)
    if not backup.exists():
        backup.write_bytes(raw)
    text = raw.decode("utf-8")
    changed, count = re.subn(r"\bad_users\b", "directory_users", text)
    if count:
        path.write_bytes(changed.encode("utf-8"))
    print(name, count)
