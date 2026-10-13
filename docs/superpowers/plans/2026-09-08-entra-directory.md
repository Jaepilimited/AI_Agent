# Entra Directory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Preserve Cella permissions independently of AD and enroll employees on first verified Entra login.

**Architecture:** Copy the existing directory once into directory_users, preserve integer identities and foreign-key relationships, then move runtime reads to that table. Entra login provisions Cella accounts transactionally; bulk Graph sync updates profiles without changing Cella authorization.

**Tech Stack:** Python/FastAPI, PyJWT, requests, PyMySQL/MariaDB, existing vanilla JavaScript admin UI.

**Spec:** ../specs/2026-09-08-entra-directory-design.md

## Global Constraints

- Existing users.id and directory person IDs must not change.
- FI, visitor and group grants are preserved and remain Admin-managed.
- Keep old ad_users data as a rollback archive.
- No new package dependency; do not output tokens or credentials.
- Preserve other sessions' uncommitted changes; deploy an explicit task-file manifest.
- Graph permission/proxy failure must be visible; it must not erase users or disable them in bulk.

## Task 1: Directory migration and schema

Files: app/core/user_directory_migration.py, tests/test_user_directory_migration.py.

Interfaces:
    ensure_directory_tables() -> dict
    migration_status() -> dict

- [x] Test a source directory with both grants, an existing linked login, and prelogin memberships.
- [x] Create directory_users with the original IDs and profile/permission columns plus Entra identity metadata.
- [x] Copy legacy data once, import existing Entra links, rebind users/user_groups foreign keys, verify preservation and write a completion marker.
- [x] Repeat migration; assert a subsequently revoked grant is not restored.
- [x] Verify migration failures leave the old source available and cannot mark completion.

## Task 2: Entra profile sync and first-login enrollment

Files: app/core/user_directory.py, app/core/entra_auth.py, app/api/entra_routes.py, tests/test_user_directory.py, tests/test_entra_auth.py.

Interfaces:
    sync_directory() -> dict
    provision_from_claims(claims: dict) -> dict
    require_active_identity(claims: dict) -> None

- [x] Exercise trusted/foreign/guest identities, unique/conflicting migration matches, missing login account and repeat callback.
- [x] Serialize provisioning, preserve existing IDs/permissions, update verified profile fields and last_login, clear local-password reset state after Entra authentication.
- [x] Implement Graph pagination and profile/member status updates; preserve permissions, refuse partial or malformed snapshots.
- [x] Surface Graph failures as a readable unavailable state without blocking enrollment of imported employees.

## Task 3: Runtime and Admin consumers

Files: app/api/auth_api.py, app/api/auth_middleware.py, app/api/admin_group_api.py, app/api/routes.py, app/api/admin_api.py, app/db/mariadb.py, app/main.py; existing briefing/report/profile/feedback/password modules.

- [x] Change authorization/profile SQL to directory_users, keeping compatibility integer columns.
- [x] Expose /api/admin/directory users/stats/department/sync operations.
- [x] Preserve manual FI/visitor/group updates and cache invalidation; block inactive directory identities.
- [x] Start the idempotent migration before consumers; update scheduled directory checks and legacy sync entry point.
- [x] Run existing consumer and permission regressions.

## Task 4: Admin UI

Files: app/frontend/chat.js, app/frontend/chat.html, tests/frontend/test_db_team_visitor_access.py.

- [x] Use the neutral directory API, Cella-user labels and Entra sync status.
- [x] Distinguish linked identity from completed first sign-in; keep permission controls for prelogin employees.
- [x] Preserve the visitor filter and department behavior; remove obsolete password-reset controls from the live UI.
- [x] Run node syntax and focused admin browser tests.

## Task 5: Rollout and documentation

Files: scripts/sync_ad_users.py, scripts/sync_entra_users.py, CLAUDE.md, generated AGENTS.md, task deployment and verification artifacts.

- [x] Update rules to the approved enrollment/authorization behavior and regenerate AGENTS.md using scripts/sync_agents_md.py.
- [x] Run focused regression suite and deploy preflight.
- [x] Take an explicit migration/permission snapshot and a rollback copy of selected production source files.
- [x] Apply the migration and selected-file rollout, restart the actual WAS, verify health and account/grant/group invariants.
- [x] Report verified behavior and the separate Graph permission/network prerequisite.
