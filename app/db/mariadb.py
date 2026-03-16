"""DB layer — auto-selects backend by server port.

Port 3000 (production) → MariaDB (shared, persistent)
Port 3001 (development) → SQLite  (local file, isolated)

Interface is identical: fetch_all, fetch_one, execute, execute_lastid.
"""
import os
import sqlite3
import threading
from pathlib import Path

import pymysql
from dbutils.pooled_db import PooledDB
import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Auto-detect mode: PORT env var or fallback to config
# ---------------------------------------------------------------------------
_PORT = int(os.environ.get("PORT", "0")) or get_settings().port
_DEV_MODE = _PORT != 3000

if _DEV_MODE:
    logger.info("db_mode_sqlite", port=_PORT, path="data/dev.db")
else:
    logger.info("db_mode_mariadb", port=_PORT)


# ===========================================================================
# MariaDB backend (production)
# ===========================================================================
_pool = None


def _get_pool() -> PooledDB:
    """Get or create MariaDB connection pool (singleton)."""
    global _pool
    if _pool is None:
        s = get_settings()
        _pool = PooledDB(
            creator=pymysql,
            maxconnections=10,
            mincached=2,
            maxcached=5,
            blocking=True,
            host=s.mariadb_host,
            port=int(s.mariadb_port),
            user=s.mariadb_user,
            password=s.mariadb_password,
            database=s.mariadb_database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
        )
        logger.info("mariadb_pool_initialized", max=10, cached=2)
    return _pool


def _maria_fetch_all(sql: str, params: tuple = ()) -> list[dict]:
    conn = _get_pool().connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def _maria_fetch_one(sql: str, params: tuple = ()) -> dict | None:
    conn = _get_pool().connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()
    finally:
        conn.close()


def _maria_execute(sql: str, params: tuple = ()) -> int:
    conn = _get_pool().connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
            return cur.rowcount
    finally:
        conn.close()


def _maria_execute_lastid(sql: str, params: tuple = ()) -> int:
    conn = _get_pool().connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
            return cur.lastrowid
    finally:
        conn.close()


# ===========================================================================
# SQLite backend (development)
# ===========================================================================
_SQLITE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "dev.db"
_sqlite_lock = threading.Lock()
_sqlite_initialized = False

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT,
    password_hash TEXT,
    display_name TEXT,
    role TEXT DEFAULT 'user',
    allowed_models TEXT,
    ad_user_id INTEGER,
    is_active INTEGER DEFAULT 1,
    last_login TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS ad_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE,
    display_name TEXT,
    email TEXT UNIQUE,
    department TEXT,
    full_dn TEXT,
    is_active INTEGER DEFAULT 1,
    synced_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    user_id INTEGER,
    title TEXT,
    model TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT,
    role TEXT,
    content TEXT,
    model TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS access_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE,
    description TEXT,
    brand_filter TEXT
);
CREATE TABLE IF NOT EXISTS user_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ad_user_id INTEGER,
    group_id INTEGER,
    UNIQUE(ad_user_id, group_id)
);
CREATE TABLE IF NOT EXISTS sql_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_hash TEXT UNIQUE,
    query_text TEXT,
    generated_sql TEXT,
    brand_filter TEXT,
    hit_count INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    last_used_at TEXT DEFAULT (datetime('now'))
);
"""


def _sqlite_conn() -> sqlite3.Connection:
    """Get a SQLite connection with dict-like row factory."""
    conn = sqlite3.connect(str(_SQLITE_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _sqlite_init():
    """Create tables and sync auth data from MariaDB (once)."""
    global _sqlite_initialized
    if _sqlite_initialized:
        return

    _SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = _sqlite_conn()
    try:
        conn.executescript(_SQLITE_SCHEMA)
        conn.commit()

        # Sync ad_users + users from MariaDB so login works on dev
        cur = conn.execute("SELECT COUNT(*) AS cnt FROM ad_users")
        if cur.fetchone()["cnt"] == 0:
            _sync_auth_from_maria(conn)

        _sqlite_initialized = True
        logger.info("sqlite_dev_initialized", path=str(_SQLITE_PATH))
    finally:
        conn.close()


def _sync_auth_from_maria(sqlite_conn: sqlite3.Connection):
    """Copy ad_users and users from MariaDB into SQLite for dev login."""
    try:
        s = get_settings()
        maria = pymysql.connect(
            host=s.mariadb_host, port=int(s.mariadb_port),
            user=s.mariadb_user, password=s.mariadb_password,
            database=s.mariadb_database, charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )
        try:
            with maria.cursor() as cur:
                # ad_users
                cur.execute("SELECT * FROM ad_users")
                rows = cur.fetchall()
                if rows:
                    cols = list(rows[0].keys())
                    placeholders = ",".join(["?"] * len(cols))
                    col_names = ",".join(cols)
                    for r in rows:
                        vals = tuple(r[c] for c in cols)
                        sqlite_conn.execute(
                            f"INSERT OR IGNORE INTO ad_users ({col_names}) VALUES ({placeholders})",
                            vals,
                        )
                    logger.info("sqlite_synced_ad_users", count=len(rows))

                # users
                cur.execute("SELECT * FROM users")
                rows = cur.fetchall()
                if rows:
                    cols = list(rows[0].keys())
                    placeholders = ",".join(["?"] * len(cols))
                    col_names = ",".join(cols)
                    for r in rows:
                        vals = tuple(r[c] for c in cols)
                        sqlite_conn.execute(
                            f"INSERT OR IGNORE INTO users ({col_names}) VALUES ({placeholders})",
                            vals,
                        )
                    logger.info("sqlite_synced_users", count=len(rows))

                # access_groups
                cur.execute("SELECT * FROM access_groups")
                rows = cur.fetchall()
                if rows:
                    cols = list(rows[0].keys())
                    placeholders = ",".join(["?"] * len(cols))
                    col_names = ",".join(cols)
                    for r in rows:
                        vals = tuple(r[c] for c in cols)
                        sqlite_conn.execute(
                            f"INSERT OR IGNORE INTO access_groups ({col_names}) VALUES ({placeholders})",
                            vals,
                        )

                # user_groups
                cur.execute("SELECT * FROM user_groups")
                rows = cur.fetchall()
                if rows:
                    cols = list(rows[0].keys())
                    placeholders = ",".join(["?"] * len(cols))
                    col_names = ",".join(cols)
                    for r in rows:
                        vals = tuple(r[c] for c in cols)
                        sqlite_conn.execute(
                            f"INSERT OR IGNORE INTO user_groups ({col_names}) VALUES ({placeholders})",
                            vals,
                        )

            sqlite_conn.commit()
        finally:
            maria.close()
    except Exception as e:
        logger.warning("sqlite_maria_sync_failed", error=str(e))


def _to_sqlite_sql(sql: str) -> str:
    """Convert MySQL-style SQL to SQLite-compatible SQL."""
    # %s → ? placeholder
    return sql.replace("%s", "?")


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return dict(row)


def _sqlite_fetch_all(sql: str, params: tuple = ()) -> list[dict]:
    _sqlite_init()
    with _sqlite_lock:
        conn = _sqlite_conn()
        try:
            cur = conn.execute(_to_sqlite_sql(sql), params)
            return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()


def _sqlite_fetch_one(sql: str, params: tuple = ()) -> dict | None:
    _sqlite_init()
    with _sqlite_lock:
        conn = _sqlite_conn()
        try:
            cur = conn.execute(_to_sqlite_sql(sql), params)
            return _row_to_dict(cur.fetchone())
        finally:
            conn.close()


def _sqlite_execute(sql: str, params: tuple = ()) -> int:
    _sqlite_init()
    with _sqlite_lock:
        conn = _sqlite_conn()
        try:
            cur = conn.execute(_to_sqlite_sql(sql), params)
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()


def _sqlite_execute_lastid(sql: str, params: tuple = ()) -> int:
    _sqlite_init()
    with _sqlite_lock:
        conn = _sqlite_conn()
        try:
            cur = conn.execute(_to_sqlite_sql(sql), params)
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()


# ===========================================================================
# Public interface — routes to the right backend
# ===========================================================================

if _DEV_MODE:
    fetch_all = _sqlite_fetch_all
    fetch_one = _sqlite_fetch_one
    execute = _sqlite_execute
    execute_lastid = _sqlite_execute_lastid
else:
    fetch_all = _maria_fetch_all
    fetch_one = _maria_fetch_one
    execute = _maria_execute
    execute_lastid = _maria_execute_lastid


# Backward compat: some code imports get_maria_conn directly
def get_maria_conn():
    """Get a pooled MariaDB connection (production only)."""
    return _get_pool().connection()
