"""Move Cella authorization out of AD once, retaining the AD rollback archive.

MariaDB DDL commits implicitly. Each foreign key is replaced in one ALTER, and
every restart validates any partially copied rows before continuing. A committed
completion marker is the only permission to stop comparing against the archive;
after that marker, legacy rows are never read or copied by this module again.
"""

from collections import Counter
from dataclasses import dataclass, replace
import hashlib
import json

import structlog

from app.config import get_settings
from app.db.mariadb import get_maria_conn


logger = structlog.get_logger(__name__)
MIGRATION_KEY = "ad_to_directory_v1"
_SOURCE = "ad_users"
_DIRECTORY = "directory_users"
_MARKERS = "directory_migrations"
_REQUIRED_COLUMNS = {
    "id", "username", "display_name", "email", "department", "full_dn",
    "is_active", "synced_at", "created_at", "updated_at", "deal_id_prefix",
    "can_view_fi", "can_view_visitor_analytics",
}
_IDENTITY_COLUMNS = {
    "entra_oid": "VARCHAR(64) NULL",
    "entra_tenant_id": "VARCHAR(64) NULL",
    "identity_source": "VARCHAR(16) NOT NULL DEFAULT 'legacy'",
    "last_signin_at": "DATETIME NULL",
}
_FK_RULES = {"RESTRICT", "CASCADE", "SET NULL", "NO ACTION", "SET DEFAULT"}


class DirectoryMigrationError(RuntimeError):
    """A preservation check failed; the migration must not mark completion."""


def _quote(name):
    if not isinstance(name, str) or not name or "\x00" in name:
        raise DirectoryMigrationError("Invalid database identifier")
    return "`" + name.replace("`", "``") + "`"


def _table(schema, name):
    return f"{_quote(schema)}.{_quote(name)}"


def _exists(cur, schema, table):
    cur.execute(
        "SELECT 1 AS present FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND TABLE_TYPE = 'BASE TABLE'",
        (schema, table),
    )
    return bool(cur.fetchone())


def _columns(cur, schema, table):
    cur.execute(
        "SELECT COLUMN_NAME AS name, COLUMN_TYPE AS column_type, "
        "IS_NULLABLE AS is_nullable, COLUMN_DEFAULT AS column_default, "
        "EXTRA AS extra, COLLATION_NAME AS collation_name, COLUMN_KEY AS column_key "
        "FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = %s "
        "AND TABLE_NAME = %s ORDER BY ORDINAL_POSITION",
        (schema, table),
    )
    return {row["name"]: row for row in cur.fetchall()}


def _ddl(conn, cur, sql):
    # Explicit boundaries: rollback cannot undo earlier successful MariaDB DDL.
    conn.commit()
    cur.execute(sql)
    conn.commit()


def _ensure_login_assignment_flag(conn, cur, schema):
    if _exists(cur, schema, "users"):
        columns = _columns(cur, schema, "users")
        if "requires_group_assignment" not in columns:
            _ddl(
                conn, cur, f"ALTER TABLE {_table(schema, 'users')} "
                "ADD COLUMN requires_group_assignment TINYINT(1) NOT NULL DEFAULT 0",
            )


def _ensure_schema(conn, cur, schema, source_columns):
    if not _exists(cur, schema, _DIRECTORY):
        # LIKE retains every original column, index, default and collation.
        _ddl(conn, cur, f"CREATE TABLE {_table(schema, _DIRECTORY)} "
             f"LIKE {_table(schema, _SOURCE)}")
    columns = _columns(cur, schema, _DIRECTORY)
    for name, definition in source_columns.items():
        if columns.get(name) != definition:
            raise DirectoryMigrationError(f"Directory schema conflicts with source column: {name}")
    additions = [
        f"ADD COLUMN {_quote(name)} {definition}"
        for name, definition in _IDENTITY_COLUMNS.items() if name not in columns
    ]
    if additions:
        _ddl(conn, cur, f"ALTER TABLE {_table(schema, _DIRECTORY)} " + ", ".join(additions))
    cur.execute(
        "SELECT INDEX_NAME AS name, NON_UNIQUE AS non_unique, "
        "COLUMN_NAME AS column_name, SEQ_IN_INDEX AS position, SUB_PART AS sub_part "
        "FROM INFORMATION_SCHEMA.STATISTICS WHERE TABLE_SCHEMA = %s "
        "AND TABLE_NAME = %s ORDER BY INDEX_NAME, SEQ_IN_INDEX",
        (schema, _DIRECTORY),
    )
    indexes = {}
    for row in cur.fetchall():
        if not row["non_unique"] and row["sub_part"] is None:
            indexes.setdefault(row["name"], []).append(row["column_name"])
    if not any(set(cols) == {"entra_tenant_id", "entra_oid"} and len(cols) == 2
               for cols in indexes.values()):
        _ddl(conn, cur, f"ALTER TABLE {_table(schema, _DIRECTORY)} "
             "ADD UNIQUE INDEX ux_directory_entra_identity (entra_tenant_id, entra_oid)")
    _ensure_login_assignment_flag(conn, cur, schema)
    _ddl(conn, cur, f"CREATE TABLE IF NOT EXISTS {_table(schema, _MARKERS)} ("
         "migration_key VARCHAR(100) NOT NULL PRIMARY KEY, "
         "completed_at DATETIME NOT NULL, details_json LONGTEXT NOT NULL) ENGINE=InnoDB")


@dataclass(frozen=True)
class _ForeignKey:
    schema: str
    table: str
    name: str
    columns: tuple
    referenced_schema: str
    referenced_table: str
    referenced_columns: tuple
    delete_rule: str
    update_rule: str


def _foreign_keys(cur, schema):
    cur.execute(
        "SELECT k.TABLE_SCHEMA AS child_schema, k.TABLE_NAME AS child_table, "
        "k.CONSTRAINT_NAME AS name, k.COLUMN_NAME AS column_name, "
        "k.ORDINAL_POSITION AS position, k.REFERENCED_TABLE_SCHEMA AS parent_schema, "
        "k.REFERENCED_TABLE_NAME AS parent_table, k.REFERENCED_COLUMN_NAME AS parent_column, "
        "r.DELETE_RULE AS delete_rule, r.UPDATE_RULE AS update_rule "
        "FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE k "
        "JOIN INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS r "
        "ON r.CONSTRAINT_SCHEMA = k.CONSTRAINT_SCHEMA "
        "AND r.TABLE_NAME = k.TABLE_NAME AND r.CONSTRAINT_NAME = k.CONSTRAINT_NAME "
        "WHERE k.REFERENCED_TABLE_SCHEMA = %s "
        "AND k.REFERENCED_TABLE_NAME IN ('ad_users', 'directory_users') "
        "ORDER BY k.TABLE_SCHEMA, k.TABLE_NAME, k.CONSTRAINT_NAME, k.ORDINAL_POSITION",
        (schema,),
    )
    grouped = {}
    for row in cur.fetchall():
        grouped.setdefault((row["child_schema"], row["child_table"], row["name"]), []).append(row)
    result = []
    for (child_schema, child_table, name), rows in grouped.items():
        first = rows[0]
        if (first["delete_rule"] not in _FK_RULES or first["update_rule"] not in _FK_RULES
                or [int(row["position"]) for row in rows] != list(range(1, len(rows) + 1))):
            raise DirectoryMigrationError("Invalid foreign key metadata")
        result.append(_ForeignKey(
            child_schema, child_table, name, tuple(row["column_name"] for row in rows),
            first["parent_schema"], first["parent_table"],
            tuple(row["parent_column"] for row in rows), first["delete_rule"], first["update_rule"],
        ))
    return result


def _rebound_key(foreign_key):
    if foreign_key.referenced_table == _DIRECTORY:
        return foreign_key
    # MariaDB 10.11 cannot drop and add the same constraint name in one ALTER.
    # A new deterministic name keeps the replacement atomic and retryable.
    identity = "\x00".join((foreign_key.schema, foreign_key.table, foreign_key.name))
    name = "directory_fk_" + hashlib.sha256(identity.encode()).hexdigest()[:24]
    return replace(foreign_key, name=name, referenced_table=_DIRECTORY)


def _rebind_sql(foreign_key):
    replacement = _rebound_key(foreign_key)
    return (
        f"ALTER TABLE {_table(foreign_key.schema, foreign_key.table)} "
        f"DROP FOREIGN KEY {_quote(foreign_key.name)}, "
        f"ADD CONSTRAINT {_quote(replacement.name)} FOREIGN KEY "
        f"({', '.join(map(_quote, foreign_key.columns))}) "
        f"REFERENCES {_table(foreign_key.referenced_schema, _DIRECTORY)} "
        f"({', '.join(map(_quote, foreign_key.referenced_columns))}) "
        f"ON DELETE {foreign_key.delete_rule} ON UPDATE {foreign_key.update_rule}"
    )


def _read_rows(cur, schema, table):
    # Locking reads see current committed data after DDL, not an older snapshot.
    cur.execute(f"SELECT * FROM {_table(schema, table)} FOR UPDATE")
    return list(cur.fetchall())


def _validate_copy(source_rows, directory_rows, columns, *, complete):
    source = {row["id"]: row for row in source_rows}
    target = {row["id"]: row for row in directory_rows}
    if len(source) != len(source_rows) or len(target) != len(directory_rows):
        raise DirectoryMigrationError("Duplicate directory IDs")
    if set(target) - set(source):
        raise DirectoryMigrationError("Directory contains IDs absent from the migration source")
    if complete and set(source) != set(target):
        raise DirectoryMigrationError("Directory row count or IDs changed during migration")
    for person_id, target_row in target.items():
        if any(source[person_id].get(name) != target_row.get(name) for name in columns):
            raise DirectoryMigrationError(f"Directory copy conflict for ID {person_id}")


def _assert_unchanged(before, after, label):
    # Counter retains duplicate memberships as well as their exact values.
    def signature(rows):
        return Counter(tuple(sorted(row.items())) for row in rows)
    if signature(before) != signature(after):
        raise DirectoryMigrationError(f"{label} changed during migration")


def _entra_bindings(users, directory_rows, tenant, *, require_bound=False):
    linked = [row for row in users if row.get("entra_oid")]
    if linked and not tenant:
        raise DirectoryMigrationError("Entra tenant is required to import existing sign-in identities")
    directory = {row["id"]: row for row in directory_rows}
    bindings = []
    claimed_people, claimed_oids = set(), set()
    for user in linked:
        person_id, oid = user.get("ad_user_id"), str(user["entra_oid"]).strip()
        if person_id not in directory or not oid:
            raise DirectoryMigrationError("Existing Entra login has no valid directory identity")
        if person_id in claimed_people or oid.casefold() in claimed_oids:
            raise DirectoryMigrationError("Existing Entra identities conflict")
        person = directory[person_id]
        existing = (person.get("entra_tenant_id"), person.get("entra_oid"))
        if existing != (tenant, oid) and (require_bound or existing != (None, None)):
            raise DirectoryMigrationError(f"Entra identity conflicts for directory ID {person_id}")
        claimed_people.add(person_id)
        claimed_oids.add(oid.casefold())
        bindings.append((oid, tenant, user.get("last_login"), person_id))
    return bindings


def _marker(cur, schema):
    if not _exists(cur, schema, _MARKERS):
        return None
    cur.execute(f"SELECT completed_at, details_json FROM {_table(schema, _MARKERS)} "
                "WHERE migration_key = %s", (MIGRATION_KEY,))
    return cur.fetchone()


def _status(cur, schema, marker):
    exists = _exists(cur, schema, _DIRECTORY)
    if marker and not exists:
        raise DirectoryMigrationError("Migration is marked complete but directory_users is missing")
    total, linked = 0, 0
    if exists:
        columns = _columns(cur, schema, _DIRECTORY)
        expression = "SUM(entra_oid IS NOT NULL AND entra_tenant_id IS NOT NULL)"
        if not {"entra_oid", "entra_tenant_id"}.issubset(columns):
            expression = "0"
        cur.execute(f"SELECT COUNT(*) AS total, {expression} AS entra_linked "
                    f"FROM {_table(schema, _DIRECTORY)}")
        counts = cur.fetchone()
        total, linked = int(counts["total"]), int(counts["entra_linked"] or 0)
    return {
        "completed": bool(marker), "migration_key": MIGRATION_KEY,
        "directory_users": total, "entra_linked": linked,
        "completed_at": str(marker["completed_at"]) if marker else None,
    }


def migration_status() -> dict:
    """Read migration state without creating tables or reading the AD archive."""
    conn = get_maria_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DATABASE() AS db_name")
            schema = cur.fetchone()["db_name"]
            return _status(cur, schema, _marker(cur, schema))
    finally:
        conn.close()


def ensure_directory_tables() -> dict:
    """Copy and verify the legacy directory once; raise on any preservation failure.

    This is a startup migration, before request handlers and directory sync run.
    The old AD writer must be stopped for rollout. Concurrent legacy changes are
    detected, never overwritten. A failed DDL step can be retried after inspection.
    """
    conn = get_maria_conn()
    locked = False
    lock_name = None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DATABASE() AS db_name")
            schema = cur.fetchone()["db_name"]
            if not schema:
                raise DirectoryMigrationError("A selected database is required")
            lock_name = "cella:directory-migration:" + hashlib.sha256(schema.encode()).hexdigest()[:24]
            cur.execute("SELECT GET_LOCK(%s, 30) AS acquired", (lock_name,))
            locked = cur.fetchone()["acquired"] == 1
            if not locked:
                raise DirectoryMigrationError("Could not acquire the directory migration lock")
            marker = _marker(cur, schema)
            if marker:
                _ensure_login_assignment_flag(conn, cur, schema)
                return dict(_status(cur, schema, marker), migrated=False)
            if not _exists(cur, schema, _SOURCE):
                raise DirectoryMigrationError("Migration source ad_users is missing; refusing an empty migration")
            source_columns = _columns(cur, schema, _SOURCE)
            if not _REQUIRED_COLUMNS.issubset(source_columns):
                raise DirectoryMigrationError("Migration source is missing required profile or permission columns")
            _ensure_schema(conn, cur, schema, source_columns)

            conn.begin()
            source = _read_rows(cur, schema, _SOURCE)
            directory = _read_rows(cur, schema, _DIRECTORY)
            _validate_copy(source, directory, source_columns, complete=False)
            foreign_keys = _foreign_keys(cur, schema)
            child_tables = {(fk.schema, fk.table) for fk in foreign_keys}
            child_tables.update((schema, name) for name in ("users", "user_groups")
                                if _exists(cur, schema, name))
            children = {(db, table): _read_rows(cur, db, table)
                        for db, table in sorted(child_tables)}
            users = children.get((schema, "users"), [])
            source_ids = {row["id"] for row in source}
            for name in ("users", "user_groups"):
                if any(row.get("ad_user_id") is not None and row["ad_user_id"] not in source_ids
                       for row in children.get((schema, name), [])):
                    raise DirectoryMigrationError(f"{name} has an orphaned directory reference")

            writable_columns = [name for name, col in source_columns.items()
                                if "GENERATED" not in col["extra"].upper()]
            column_sql = ", ".join(map(_quote, writable_columns))
            selected = ", ".join("s." + _quote(name) for name in writable_columns)
            cur.execute(f"INSERT INTO {_table(schema, _DIRECTORY)} ({column_sql}) "
                        f"SELECT {selected} FROM {_table(schema, _SOURCE)} s "
                        f"LEFT JOIN {_table(schema, _DIRECTORY)} d ON d.id = s.id WHERE d.id IS NULL")
            directory = _read_rows(cur, schema, _DIRECTORY)
            tenant = str(get_settings().entra_tenant_id or "").strip()
            bindings = _entra_bindings(users, directory, tenant)
            for binding in bindings:
                cur.execute(f"UPDATE {_table(schema, _DIRECTORY)} SET entra_oid = %s, "
                            "entra_tenant_id = %s, identity_source = 'entra', "
                            "last_signin_at = COALESCE(last_signin_at, %s), "
                            "updated_at = updated_at WHERE id = %s", binding)
            _validate_copy(source, _read_rows(cur, schema, _DIRECTORY), source_columns, complete=True)
            conn.commit()

            for foreign_key in foreign_keys:
                if foreign_key.referenced_table == _SOURCE:
                    _ddl(conn, cur, _rebind_sql(foreign_key))

            conn.begin()
            _assert_unchanged(source, _read_rows(cur, schema, _SOURCE), "AD source")
            final_directory = _read_rows(cur, schema, _DIRECTORY)
            _validate_copy(source, final_directory, source_columns, complete=True)
            # Check the imported immutable identities as well as the original grants.
            _entra_bindings(users, final_directory, tenant, require_bound=True)
            for (db, table), rows in children.items():
                _assert_unchanged(rows, _read_rows(cur, db, table), f"{db}.{table}")
            expected_keys = {_rebound_key(fk) for fk in foreign_keys}
            if set(_foreign_keys(cur, schema)) != expected_keys:
                raise DirectoryMigrationError("Foreign key definitions changed during migration")
            verification = {
                "source_users": len(source), "login_accounts": len(users),
                "group_memberships": len(children.get((schema, "user_groups"), [])),
                "fi_grants": sum(bool(row["can_view_fi"]) for row in source),
                "visitor_grants": sum(bool(row["can_view_visitor_analytics"]) for row in source),
                "entra_links": len(bindings), "foreign_keys": len(foreign_keys),
            }
            cur.execute(f"INSERT INTO {_table(schema, _MARKERS)} "
                        "(migration_key, completed_at, details_json) VALUES (%s, UTC_TIMESTAMP(), %s)",
                        (MIGRATION_KEY, json.dumps(verification, sort_keys=True)))
            conn.commit()
            return dict(_status(cur, schema, _marker(cur, schema)), migrated=True)
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            if locked:
                with conn.cursor() as cur:
                    cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
        except Exception:
            logger.warning("directory_migration_lock_release_failed")
        finally:
            conn.close()
