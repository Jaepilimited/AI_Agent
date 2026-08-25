"""MariaDB-backed, user-scoped snapshots for the login personal briefing.

The store is deliberately a small boundary: collectors and the briefing
aggregator hand it already-shaped, display-safe data, and this module owns
only persistence concerns.  In particular, transient Gmail content and
credentials are scrubbed before any JSON is serialized.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from app.db.mariadb import execute, fetch_one


_DDL = """
CREATE TABLE IF NOT EXISTS personal_briefing_snapshots (
    user_id INT NOT NULL PRIMARY KEY,
    for_date DATE NOT NULL,
    google_account_hash CHAR(64) NOT NULL DEFAULT '',
    calendar_json LONGTEXT NOT NULL,
    mail_json LONGTEXT NOT NULL,
    priorities_json LONGTEXT NOT NULL,
    generated_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_personal_briefing_date (for_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


# These values must never cross the persistence boundary.  Matching is case
# insensitive so a collector/API naming change cannot accidentally persist a
# credential or raw message content.
_TRANSIENT_KEYS = {
    "snippet",
    "body",
    "payload",
    "attachment",
    "attachments",
    "access_token",
    "refresh_token",
    "oauth_token",
    "token",
}


def ensure_tables() -> None:
    """Create the snapshot table if it is not already present."""

    execute(_DDL)


def _strip_transient(value: Any) -> Any:
    """Return a JSON-shaped copy without content or credential fields."""

    if isinstance(value, dict):
        return {
            key: _strip_transient(item)
            for key, item in value.items()
            if str(key).lower() not in _TRANSIENT_KEYS
        }
    if isinstance(value, list):
        return [_strip_transient(item) for item in value]
    if isinstance(value, tuple):
        return [_strip_transient(item) for item in value]
    return value


def _clean_mail(mail: dict[str, Any]) -> dict[str, Any]:
    """Copy a mail digest while excluding raw Gmail content and credentials."""

    cleaned = _strip_transient(mail)
    return cleaned if isinstance(cleaned, dict) else {}


def _json_dumps(value: Any) -> str:
    """Serialize snapshots consistently for stable writes and testability."""

    return json.dumps(
        _strip_transient(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def get_snapshot(user_id: int, for_date: date) -> dict[str, Any] | None:
    """Read exactly the requested user's snapshot for exactly one date."""

    row = fetch_one(
        "SELECT google_account_hash,calendar_json,mail_json,priorities_json,generated_at "
        "FROM personal_briefing_snapshots WHERE user_id = %s AND for_date = %s",
        (int(user_id), for_date),
    )
    if not row:
        return None
    return {
        "google_account_hash": row["google_account_hash"],
        "calendar": json.loads(row["calendar_json"]),
        "mail": json.loads(row["mail_json"]),
        "priorities": json.loads(row["priorities_json"]),
        "generated_at": row["generated_at"],
    }


def put_snapshot(
    user_id: int,
    for_date: date,
    google_account_hash: str,
    calendar: dict[str, Any],
    mail: dict[str, Any],
    priorities: list[dict[str, Any]],
    generated_at: datetime,
) -> None:
    """Atomically replace one user's snapshot while retaining prior rows on failure."""

    execute(
        "INSERT INTO personal_briefing_snapshots "
        "(user_id,for_date,google_account_hash,calendar_json,mail_json,priorities_json,generated_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE for_date=VALUES(for_date),"
        "google_account_hash=VALUES(google_account_hash),calendar_json=VALUES(calendar_json),"
        "mail_json=VALUES(mail_json),priorities_json=VALUES(priorities_json),"
        "generated_at=VALUES(generated_at)",
        (
            int(user_id),
            for_date,
            google_account_hash,
            _json_dumps(calendar),
            _json_dumps(_clean_mail(mail)),
            _json_dumps(priorities),
            generated_at,
        ),
    )


def delete_for_user(user_id: int) -> None:
    """Delete all snapshots owned by one user."""

    execute("DELETE FROM personal_briefing_snapshots WHERE user_id = %s", (int(user_id),))


def cleanup(before_date: date) -> int:
    """Delete snapshots older than a cutoff and return the affected row count."""

    return int(
        execute(
            "DELETE FROM personal_briefing_snapshots WHERE for_date < %s",
            (before_date,),
        )
        or 0
    )
