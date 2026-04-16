"""SHA256 + mtime file fingerprint cache for incremental builds."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def file_fingerprint(path: Path) -> dict[str, Any]:
    """Return {sha256, mtime, size} for a file. Reads the whole file once."""
    data = path.read_bytes()
    return {
        "sha256": hashlib.sha256(data).hexdigest(),
        "mtime": path.stat().st_mtime,
        "size": len(data),
    }


class FileCache:
    """Persistent cache of file fingerprints for incremental rebuilds."""

    def __init__(self, cache_file: Path) -> None:
        self.cache_file = cache_file

    def load(self) -> dict[str, dict[str, Any]]:
        if not self.cache_file.exists():
            return {}
        try:
            return json.loads(self.cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def save(self, data: dict[str, dict[str, Any]]) -> None:
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self.cache_file.write_text(
            json.dumps(data, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def is_changed(self, path: Path, previous: dict[str, dict[str, Any]]) -> bool:
        key = str(path)
        if key not in previous:
            return True
        prev = previous[key]
        try:
            st = path.stat()
        except FileNotFoundError:
            return True
        # Fast path: mtime + size match → assume unchanged
        if prev.get("mtime") == st.st_mtime and prev.get("size") == st.st_size:
            return False
        # Slow path: hash compare
        current_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        return prev.get("sha256") != current_hash
