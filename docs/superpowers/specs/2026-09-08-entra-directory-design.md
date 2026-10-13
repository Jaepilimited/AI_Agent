# Entra login and Cella-owned user directory

The user approved preserving existing Cella identities and permissions while retiring the AD dependency, including employees who never registered with Cella (2026-09-08 conversation).

## Outcome

- Entra authenticates users; first login attaches to the preserved Cella directory person and creates a login account when absent.
- Existing users.id, directory person IDs, FI/visitor grants, SK/DD memberships, conversations and reports remain unchanged.
- Admin manages Cella users, including employees awaiting their first login.
- A separate directory_users table owns profile and permission data. Existing ad_user_id columns retain their integer values as compatibility identifiers, with foreign keys rebound to directory_users.
- The old ad_users table remains a rollback archive, no longer an active authorization source. No deletion of company AD or Entra accounts.

## Identity and provisioning

Validate signed ID token audience, issuer, expiry, tenant, nonce and immutable object ID. Reject foreign tenants and identifiable guests. Match by tenant/object ID first. For the one-time migration only, use a unique company username match against the imported directory. Refuse ambiguous matches and already-bound conflicting identities.

Serialize first-login creation in a database transaction; concurrent callbacks must return one existing account. Imported people retain grants and memberships, even if they have never registered. New directory records and login accounts start as ordinary users, with FI and visitor grants false. Never derive admin or group access from ID-token role/group/name strings. Successful Entra sign-in refreshes profile fields and last-login time and clears the obsolete local-password-change requirement.

New login accounts carry requires_group_assignment=1. Until an Admin assigns a group with a nonempty brand filter, login/profile remain available and chat, reports, SQL downloads and saved-question execution wait for assignment. Existing accounts retain their prior access (flag=0). Group removal returns new accounts to the hold; Admin roles are exempt. Preserve existing contact/OAuth email on sign-in because the login UPN may use a different domain. Graph mail updates are handled separately.

## Directory synchronization

Implement Microsoft Graph application-permission directory reads with pagination, member/enabled state, explicit error handling, and transactional updates. Existing Cella permission columns and group memberships are never overwritten by synchronization. Do not disable accounts on partial responses, permission errors or missing pages.

Live capability checks: application token endpoint succeeds; Graph is blocked by the WAS proxy; a read from DB_PC returns 403 Authorization_RequestDenied. Full scheduled Entra roster synchronization requires Graph User.Read.All application permission/admin consent and WAS/APP network allowance to graph.microsoft.com:443. The UI must report this unavailable state rather than claim success. First login and imported employee enrollment remain independent of bulk Graph availability.

## Migration

Create directory_users with preserved IDs and directory fields, copy all existing rows and grants once, import current users.entra_oid links, and rebind the two existing AD foreign keys. Mark completion only after counts, IDs, grants and membership preservation checks pass. Repeated startup must not recopy stale AD data or restore revoked permissions. Keep original AD data for rollback.

Migrate every active SQL consumer to directory_users. Remove the LDAP cron dependency by replacing the legacy script with a compatibility entry to the Entra sync operation; keep legacy command paths only where required for an existing scheduler. Admin endpoints use /api/admin/directory.

## Validation and rollout

Cover first login for registered and unregistered employees, repeat/concurrent callbacks, identity conflict/foreign tenant/guest/inactive user rejection, preservation of grants and groups, Graph pagination/failure, and idempotent migration. Run existing FI, visitor, password-disabled, report and consumer regressions plus admin browser checks.

Compare production migration dry-run and after-state counts. Deploy only reviewed task files, since this worktree contains other sessions' changes. Verify service health, login methods, migration marker, preserved identities/grants/memberships and the new admin routes. Do not report live Microsoft interactive sign-in as tested without an actual sign-in.

Applied at 2026-09-08 12:38 KST. WAS received 27 reviewed files; APP received 16 files adapted to its older baseline. Preserved 521 directory people, 85 login accounts and 281 memberships. Admin query confirms 454 active employees, including 375 without a login account; 4 accounts have Entra links. All selected production hashes match, the service is active and health is OK, password=false/entra=true. The AD cron is replaced with Entra at 22:00. Graph sync attempted on APP at 12:41 and failed because network access is blocked; a directory fingerprint confirms that failure changed no employee rows.

MariaDB DDL is not rolled back by restoring old code. After migration starts, keep the Entra authority and old AD writer disabled; repair forward. Source/identity backups are retained at /home/jeffrey/AI_Agent/.release_backups/entra_directory_20260908_r2. A first rollout attempt stopped before DDL on a deployment path check; original code, environment and cron were restored and health verified before the successful retry.
