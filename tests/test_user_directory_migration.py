"""Preservation and restart safety for the one-time directory cutover.

The stateful DB double models committed DDL surviving a later rollback. SQL
execution against MariaDB is covered separately by rollout integration checks.
"""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime
import json
import re
from types import SimpleNamespace

import pytest

from app.core import user_directory_migration as migration


TENANT = "tenant-company"
STAMP = datetime(2026, 9, 8, 2, 30)


def _column(name, column_type="varchar(200)", nullable="YES", default=None, extra="", key=""):
    return {"name": name, "column_type": column_type, "is_nullable": nullable,
            "column_default": default, "extra": extra, "column_key": key,
            "collation_name": "utf8mb4_unicode_ci" if "char" in column_type else None}


def _person(person_id, **overrides):
    row = {
        "id": person_id, "username": f"employee{person_id}", "display_name": "Employee",
        "email": f"employee{person_id}@company.example", "department": "Data",
        "full_dn": "CN=Original,OU=Employees", "is_active": 1,
        "synced_at": STAMP, "created_at": STAMP, "updated_at": STAMP,
        "deal_id_prefix": "SK", "can_view_fi": 1, "can_view_visitor_analytics": 1,
        # A future original column must survive without a hand-maintained list.
        "employment_code": f"E{person_id}",
    }
    row.update(overrides)
    return row


class _Database:
    def __init__(self):
        self.schema = "migration_test"
        self.tables = {
            "ad_users": [_person(1), _person(2, can_view_fi=0)],
            "users": [{"id": 81, "ad_user_id": 1, "entra_oid": "oid-one", "last_login": STAMP,
                       "role": "admin"}],
            "user_groups": [{"id": 31, "ad_user_id": 2, "group_id": 7, "assigned_at": STAMP}],
            "legacy_badges": [{"id": 41, "ad_user_id": 1}],
        }
        self.columns = {
            table: {name: _column(name) for name in rows[0]}
            for table, rows in self.tables.items()
        }
        self.columns["ad_users"].update({
            "id": _column("id", "int(11)", "NO", extra="auto_increment", key="PRI"),
            "username": _column("username", "varchar(100)", "NO", key="UNI"),
            "can_view_fi": _column("can_view_fi", "tinyint(1)", "NO", "0"),
            "can_view_visitor_analytics": _column("can_view_visitor_analytics", "tinyint(1)", "NO", "0"),
            "updated_at": _column("updated_at", "datetime", extra="on update current_timestamp()"),
        })
        self.indexes = {"ad_users": {"PRIMARY": ["id"], "username": ["username"]}}
        self.foreign_keys = [
            self.fk("users", "users_ibfk_1", "SET NULL"),
            self.fk("user_groups", "user_groups_ibfk_1", "CASCADE"),
            self.fk("legacy_badges", "badges_directory_fk", "RESTRICT"),
        ]
        self.statements = []
        self.connections = []
        self.lock_available = True
        self.fail_fk_once = None
        self.after_rebind = None
        self.skip_binding_write = False

    def fk(self, table, name, delete_rule):
        return migration._ForeignKey(self.schema, table, name, ("ad_user_id",),
                                     self.schema, "ad_users", ("id",), delete_rule, "CASCADE")

    def connect(self):
        connection = _Connection(self)
        self.connections.append(connection)
        return connection

    def seed_directory(self, rows):
        self.tables["directory_users"] = deepcopy(rows)
        self.columns["directory_users"] = deepcopy(self.columns["ad_users"])
        self.indexes["directory_users"] = deepcopy(self.indexes["ad_users"])


class _Connection:
    def __init__(self, db):
        self.db = db
        self.committed = deepcopy(db.tables)
        self.closed = False
        self.rollbacks = 0
        self.released = False

    def cursor(self):
        return _Cursor(self)

    def begin(self):
        self.committed = deepcopy(self.db.tables)

    def commit(self):
        self.committed = deepcopy(self.db.tables)

    def rollback(self):
        self.db.tables = deepcopy(self.committed)
        self.rollbacks += 1

    def close(self):
        self.closed = True


class _Cursor:
    def __init__(self, conn):
        self.conn = conn
        self.db = conn.db
        self.result = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def fetchone(self):
        return deepcopy(self.result[0]) if self.result else None

    def fetchall(self):
        return deepcopy(self.result)

    def execute(self, sql, params=()):
        sql = " ".join(sql.split())
        self.db.statements.append((sql, params))
        self.result = []
        if sql == "SELECT DATABASE() AS db_name":
            self.result = [{"db_name": self.db.schema}]
        elif sql.startswith("SELECT GET_LOCK"):
            self.result = [{"acquired": int(self.db.lock_available)}]
        elif sql.startswith("SELECT RELEASE_LOCK"):
            self.conn.released = True
        elif "FROM INFORMATION_SCHEMA.TABLES" in sql:
            self.result = [{"present": 1}] if params[1] in self.db.tables else []
        elif "FROM INFORMATION_SCHEMA.COLUMNS" in sql:
            self.result = list(self.db.columns.get(params[1], {}).values())
        elif "FROM INFORMATION_SCHEMA.STATISTICS" in sql:
            self.result = [
                {"name": name, "non_unique": 0, "column_name": col,
                 "position": pos, "sub_part": None}
                for name, columns in self.db.indexes.get(params[1], {}).items()
                for pos, col in enumerate(columns, 1)
            ]
        elif "FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE" in sql:
            self.result = [
                {"child_schema": fk.schema, "child_table": fk.table, "name": fk.name,
                 "column_name": col, "position": pos, "parent_schema": fk.referenced_schema,
                 "parent_table": fk.referenced_table, "parent_column": parent_col,
                 "delete_rule": fk.delete_rule, "update_rule": fk.update_rule}
                for fk in self.db.foreign_keys
                for pos, (col, parent_col) in enumerate(zip(fk.columns, fk.referenced_columns), 1)
            ]
        elif sql.startswith("CREATE TABLE") and " LIKE " in sql:
            self.db.seed_directory([])
        elif sql.startswith("CREATE TABLE"):
            self.db.tables.setdefault("directory_migrations", [])
            self.db.columns.setdefault("directory_migrations", {})
        elif sql.startswith("ALTER TABLE"):
            self._alter(sql)
        elif sql.startswith("SELECT * FROM"):
            table = re.search(r"FROM `[^`]+`\.`([^`]+)`", sql).group(1)
            self.result = self.db.tables[table]
        elif sql.startswith("SELECT completed_at"):
            self.result = [row for row in self.db.tables["directory_migrations"]
                           if row["migration_key"] == params[0]]
        elif sql.startswith("SELECT COUNT(*) AS total"):
            rows = self.db.tables["directory_users"]
            self.result = [{"total": len(rows), "entra_linked": sum(
                bool(row.get("entra_oid") and row.get("entra_tenant_id")) for row in rows)}]
        elif sql.startswith("INSERT INTO") and "`directory_users`" in sql:
            present = {row["id"] for row in self.db.tables["directory_users"]}
            for source in self.db.tables["ad_users"]:
                if source["id"] not in present:
                    self.db.tables["directory_users"].append(dict(
                        deepcopy(source), entra_oid=None, entra_tenant_id=None,
                        identity_source="legacy", last_signin_at=None))
        elif sql.startswith("UPDATE"):
            if not self.db.skip_binding_write:
                oid, tenant, signin, person_id = params
                person = next(row for row in self.db.tables["directory_users"] if row["id"] == person_id)
                person.update(entra_oid=oid, entra_tenant_id=tenant, identity_source="entra",
                              last_signin_at=person.get("last_signin_at") or signin)
        elif sql.startswith("INSERT INTO") and "`directory_migrations`" in sql:
            self.db.tables["directory_migrations"].append({
                "migration_key": params[0], "completed_at": STAMP, "details_json": params[1]})
        else:
            raise AssertionError(f"Unexpected SQL in migration: {sql}")

    def _alter(self, sql):
        table = re.search(r"ALTER TABLE `[^`]+`\.`([^`]+)`", sql).group(1)
        if "DROP FOREIGN KEY" in sql:
            name = re.search(r"DROP FOREIGN KEY `([^`]+)`", sql).group(1)
            replacement_name = re.search(r"ADD CONSTRAINT `([^`]+)`", sql).group(1)
            if self.db.fail_fk_once == name:
                self.db.fail_fk_once = None
                raise RuntimeError("simulated atomic ALTER failure")
            self.db.foreign_keys = [replace(fk, name=replacement_name, referenced_table="directory_users") if fk.name == name else fk
                                    for fk in self.db.foreign_keys]
            if self.db.after_rebind:
                callback, self.db.after_rebind = self.db.after_rebind, None
                callback(self.db)
        elif "ADD UNIQUE INDEX" in sql:
            self.db.indexes[table]["ux_directory_entra_identity"] = ["entra_tenant_id", "entra_oid"]
        else:
            for name in (*migration._IDENTITY_COLUMNS, "requires_group_assignment"):
                if re.search(r"ADD COLUMN `?" + name + r"`? ", sql):
                    self.db.columns[table][name] = _column(name)
                    default = "legacy" if name == "identity_source" else 0 if name == "requires_group_assignment" else None
                    for row in self.db.tables[table]:
                        row[name] = default


@pytest.fixture
def database(monkeypatch):
    db = _Database()
    monkeypatch.setattr(migration, "get_maria_conn", db.connect)
    monkeypatch.setattr(migration, "get_settings", lambda: SimpleNamespace(entra_tenant_id=TENANT))
    return db


def test_migrates_registered_and_prelogin_people_without_changing_grants_or_memberships(database):
    original = deepcopy(database.tables)
    status = migration.ensure_directory_tables()

    assert status["completed"] is True and status["migrated"] is True
    assert (status["directory_users"], status["entra_linked"]) == (2, 1)
    assert database.tables["ad_users"] == original["ad_users"]
    for person in database.tables["directory_users"]:
        source = next(row for row in original["ad_users"] if row["id"] == person["id"])
        assert {key: person[key] for key in source} == source
    assert database.tables["user_groups"] == original["user_groups"]
    assert database.tables["legacy_badges"] == original["legacy_badges"]
    assert database.tables["users"] == [dict(original["users"][0], requires_group_assignment=0)]
    linked, prelogin = database.tables["directory_users"]
    assert (linked["entra_oid"], linked["entra_tenant_id"], linked["last_signin_at"]) == ("oid-one", TENANT, STAMP)
    assert prelogin["identity_source"] == "legacy" and prelogin["entra_oid"] is None
    assert all(fk.referenced_table == "directory_users" for fk in database.foreign_keys)
    evidence = json.loads(database.tables["directory_migrations"][0]["details_json"])
    assert evidence == {"source_users": 2, "login_accounts": 1, "group_memberships": 1,
                        "fi_grants": 1, "visitor_grants": 2, "entra_links": 1, "foreign_keys": 3}
    assert database.connections[-1].released and database.connections[-1].closed


def test_restart_preserves_revoked_rights_without_reading_the_archive(database):
    migration.ensure_directory_tables()
    person = database.tables["directory_users"][0]
    person.update(can_view_fi=0, can_view_visitor_analytics=0, display_name="Updated in Cella")
    database.tables["user_groups"].clear()
    database.tables["users"][0]["requires_group_assignment"] = 1
    # The completion marker is sufficient even if an operator removes the archive later.
    del database.tables["ad_users"]
    database.statements.clear()
    expected = deepcopy(database.tables)

    status = migration.ensure_directory_tables()

    assert status["completed"] is True and status["migrated"] is False
    assert database.tables == expected
    assert not any("ad_users" in sql or "ad_users" in params for sql, params in database.statements)


def test_status_is_read_only_and_reports_pending_before_migration(database):
    assert migration.migration_status() == {
        "completed": False, "migration_key": migration.MIGRATION_KEY,
        "directory_users": 0, "entra_linked": 0, "completed_at": None,
    }
    assert all(sql.startswith("SELECT") for sql, _ in database.statements)


def test_missing_source_fails_without_creating_a_completed_empty_directory(database):
    del database.tables["ad_users"]
    with pytest.raises(migration.DirectoryMigrationError, match="source ad_users is missing"):
        migration.ensure_directory_tables()
    assert "directory_users" not in database.tables
    assert "directory_migrations" not in database.tables
    assert database.connections[-1].released and database.connections[-1].rollbacks == 1


def test_partial_copy_conflict_aborts_before_rebinding_or_overwriting(database):
    database.seed_directory([_person(1, can_view_fi=0)])
    expected_source = deepcopy(database.tables["ad_users"])
    with pytest.raises(migration.DirectoryMigrationError, match="copy conflict"):
        migration.ensure_directory_tables()
    assert database.tables["directory_users"][0]["can_view_fi"] == 0
    assert database.tables["ad_users"] == expected_source
    assert all(fk.referenced_table == "ad_users" for fk in database.foreign_keys)
    assert not database.tables.get("directory_migrations")


def test_interrupted_ddl_can_resume_without_losing_ids_or_memberships(database):
    original = deepcopy(database.tables)
    database.fail_fk_once = "user_groups_ibfk_1"
    with pytest.raises(RuntimeError, match="atomic ALTER failure"):
        migration.ensure_directory_tables()
    assert database.foreign_keys[0].referenced_table == "directory_users"
    assert database.foreign_keys[1].referenced_table == "ad_users"
    assert len(database.tables["directory_users"]) == 2
    assert not database.tables["directory_migrations"]
    assert migration.ensure_directory_tables()["completed"] is True
    assert database.tables["ad_users"] == original["ad_users"]
    assert database.tables["user_groups"] == original["user_groups"]
    assert all(fk.referenced_table == "directory_users" for fk in database.foreign_keys)


@pytest.mark.parametrize("table, field, value", [
    ("ad_users", "can_view_fi", 0),
    ("users", "id", 999),
    ("user_groups", "group_id", 999),
    ("legacy_badges", "ad_user_id", 2),
])
def test_concurrent_preservation_changes_cannot_mark_completion(database, table, field, value):
    database.after_rebind = lambda db: db.tables[table][0].update({field: value})
    with pytest.raises(migration.DirectoryMigrationError, match="changed during migration"):
        migration.ensure_directory_tables()
    assert not database.tables["directory_migrations"]


def test_imported_identity_must_be_verified_before_the_completion_marker(database):
    database.skip_binding_write = True
    with pytest.raises(migration.DirectoryMigrationError, match="Entra identity conflicts"):
        migration.ensure_directory_tables()
    assert not database.tables["directory_migrations"]


def test_fk_replacement_keeps_delete_update_rules_and_uses_one_atomic_statement(database):
    migration.ensure_directory_tables()
    alters = [sql for sql, _ in database.statements if "DROP FOREIGN KEY" in sql]
    assert len(alters) == 3
    for sql in alters:
        assert sql.count("ALTER TABLE") == 1 and ";" not in sql
        assert ", ADD CONSTRAINT" in sql and "REFERENCES `migration_test`.`directory_users`" in sql
        assert "ON UPDATE CASCADE" in sql
    assert "ON DELETE SET NULL" in next(sql for sql in alters if "`users`" in sql)
    assert "ON DELETE CASCADE" in next(sql for sql in alters if "`user_groups`" in sql)
    assert not any("foreign_key_checks" in sql.lower() for sql, _ in database.statements)


def test_fk_replacement_supports_composite_keys_and_quotes_identifiers():
    fk = migration._ForeignKey("separate-db", "odd`child", "fk`name", ("a", "b"),
                               "migration_test", "ad_users", ("id", "username"), "RESTRICT", "NO ACTION")
    sql = migration._rebind_sql(fk)
    assert "`separate-db`.`odd``child`" in sql and "`fk``name`" in sql
    assert "FOREIGN KEY (`a`, `b`)" in sql and "(`id`, `username`)" in sql
    assert sql.endswith("ON DELETE RESTRICT ON UPDATE NO ACTION")


@pytest.mark.parametrize("mutation", [
    lambda rows: rows.append(_person(3)),
    lambda rows: rows[0].update(username="someone-else"),
    lambda rows: rows[0].update(can_view_visitor_analytics=0),
    lambda rows: rows[0].update(employment_code="CHANGED"),
])
def test_copy_validator_rejects_identity_grant_and_original_column_changes(mutation):
    source = [_person(1), _person(2)]
    target = deepcopy(source)
    mutation(target)
    with pytest.raises(migration.DirectoryMigrationError):
        migration._validate_copy(source, target, source[0].keys(), complete=True)


def test_partial_copy_allows_only_identical_existing_rows():
    source = [_person(1), _person(2)]
    migration._validate_copy(source, [dict(source[1], entra_oid=None)], source[0].keys(), complete=False)
    with pytest.raises(migration.DirectoryMigrationError, match="row count or IDs"):
        migration._validate_copy(source, source[:1], source[0].keys(), complete=True)


@pytest.mark.parametrize("users, directory, tenant", [
    ([{"ad_user_id": 1, "entra_oid": "oid"}], [_person(1)], ""),
    ([{"ad_user_id": None, "entra_oid": "oid"}], [_person(1)], TENANT),
    ([{"ad_user_id": 1, "entra_oid": "oid"}], [dict(_person(1), entra_oid="other", entra_tenant_id=TENANT)], TENANT),
    ([{"ad_user_id": 1, "entra_oid": "oid"}, {"ad_user_id": 2, "entra_oid": "OID"}], [_person(1), _person(2)], TENANT),
    ([{"ad_user_id": 1, "entra_oid": "one"}, {"ad_user_id": 1, "entra_oid": "two"}], [_person(1)], TENANT),
])
def test_entra_bootstrap_rejects_missing_tenant_and_conflicting_identity_links(users, directory, tenant):
    with pytest.raises(migration.DirectoryMigrationError):
        migration._entra_bindings(users, directory, tenant)


def test_migration_lock_timeout_never_attempts_a_copy(database):
    database.lock_available = False
    with pytest.raises(migration.DirectoryMigrationError, match="migration lock"):
        migration.ensure_directory_tables()
    assert "directory_users" not in database.tables
    assert database.connections[-1].closed and not database.connections[-1].released


def test_completed_marker_with_missing_directory_is_an_error(database):
    migration.ensure_directory_tables()
    del database.tables["directory_users"]
    with pytest.raises(migration.DirectoryMigrationError, match="marked complete"):
        migration.ensure_directory_tables()
