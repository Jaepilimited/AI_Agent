"""Cella-owned employees and permissions, authenticated by Entra ID."""
from __future__ import annotations

import secrets
from urllib.parse import urlparse
from uuid import UUID

import bcrypt
import requests
import structlog

from app.config import ALL_MODELS, get_settings
from app.core.group_autoassign import assign_default_group
from app.db.mariadb import execute, fetch_one, get_maria_conn

logger = structlog.get_logger(__name__)
_LOCK = "cella_directory_identity"
_GRAPH = "https://graph.microsoft.com/v1.0/users"
_SELECT = "id,displayName,mail,userPrincipalName,department,userType,accountEnabled"


class DirectoryError(Exception):
    def __init__(self, message: str, status: int = 403):
        super().__init__(message)
        self.status = status


def _uuid(value: object) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise DirectoryError("회사 계정 식별자를 확인하지 못했습니다.") from None


def require_active_identity(claims: dict) -> tuple[str, str]:
    """Called only after cryptographic ID-token verification."""
    tenant = _uuid(get_settings().entra_tenant_id)
    if _uuid(claims.get("tid")) != tenant:
        raise DirectoryError("회사 Entra ID 계정으로 로그인해 주세요.")
    oid = _uuid(claims.get("oid"))
    username = str(claims.get("preferred_username") or claims.get("upn") or "")
    if str(claims.get("acct", "0")) != "0" or "#ext#" in username.lower():
        raise DirectoryError("외부 게스트 계정은 셀라를 이용할 수 없습니다.")
    idp = str(claims.get("idp") or "").lower().rstrip("/")
    issuers = {f"https://sts.windows.net/{tenant}",
               f"https://login.microsoftonline.com/{tenant}/v2.0"}
    if idp and idp not in issuers:
        raise DirectoryError("회사 소속 계정으로 로그인해 주세요.")
    return tenant, oid


def _email(value: object) -> str:
    value = str(value or "").strip().lower()
    if value.count("@") != 1 or len(value) > 255 or any(c.isspace() for c in value):
        return ""
    local, domain = value.split("@")
    return value if local and "." in domain and "#ext#" not in local else ""


def _profile(claims: dict) -> dict:
    email = _email(claims.get("preferred_username")) or _email(claims.get("upn"))
    email = email or _email(claims.get("email"))
    return {"email": email, "display_name": str(claims.get("name") or "")[:200],
            "department": str(claims.get("department") or "")[:200]}


def _lock(cur) -> None:
    cur.execute("SELECT GET_LOCK(%s, 15) AS acquired", (_LOCK,))
    if not cur.fetchone().get("acquired"):
        raise DirectoryError("사용자 정보를 처리 중입니다. 잠시 후 다시 시도해 주세요.", 503)


def _unlock(cur) -> None:
    cur.execute("SELECT RELEASE_LOCK(%s)", (_LOCK,))


def _company_domains(cur) -> set[str]:
    configured = getattr(get_settings(), "entra_employee_domains", "") or ""
    if configured.strip():
        return {v.strip().lower() for v in configured.split(",") if v.strip()}
    # The imported staff roster is the bootstrap authority until Graph is enabled.
    cur.execute("SELECT DISTINCT LOWER(SUBSTRING_INDEX(email, '@', -1)) AS domain "
                "FROM directory_users WHERE email LIKE %s",
                ("%@%",))
    domains = {r["domain"] for r in cur.fetchall() if r.get("domain")}
    return domains - {"gmail.com", "outlook.com", "hotmail.com", "live.com",
                      "yahoo.com", "naver.com", "noemail.local"}


def _find_person(cur, tenant: str, oid: str, email: str, *, graph_verified: bool = False) -> dict | None:
    cur.execute("SELECT * FROM directory_users WHERE entra_tenant_id=%s AND entra_oid=%s "
                "FOR UPDATE", (tenant, oid))
    person = cur.fetchone()
    if person:
        return person
    if not email or (not graph_verified and email.split("@")[1] not in _company_domains(cur)):
        raise DirectoryError("회사 사용자 명단과 계정을 연결하지 못했습니다. 관리자에게 문의해 주세요.")
    # One-time migration match. Bound identities are never reassigned by email.
    cur.execute("SELECT * FROM directory_users "
                "WHERE LOWER(SUBSTRING_INDEX(email, '@', 1))=%s FOR UPDATE",
                (email.split("@")[0],))
    matches = cur.fetchall()
    if len(matches) > 1:
        raise DirectoryError("동일한 계정 이름이 여러 명에게 연결돼 있습니다. 관리자에게 문의해 주세요.")
    if not matches:
        return None
    person = matches[0]
    if person.get("entra_oid") and (person["entra_oid"] != oid or
                                    person.get("entra_tenant_id") != tenant):
        raise DirectoryError("이미 다른 회사 계정과 연결된 사용자입니다. 관리자에게 문의해 주세요.")
    return person


def _insert_person(cur, tenant: str, oid: str, profile: dict) -> dict:
    cur.execute("INSERT INTO directory_users "
                "(username,display_name,email,department,is_active,can_view_fi,"
                "can_view_visitor_analytics,entra_tenant_id,entra_oid,identity_source,synced_at) "
                "VALUES (%s,%s,%s,%s,1,0,0,%s,%s,'entra',NOW())",
                ("entra:" + oid, profile["display_name"], profile["email"],
                 profile["department"], tenant, oid))
    return {"id": cur.lastrowid, "is_active": 1, **profile}


def _update_profile(cur, person: dict, tenant: str, oid: str, profile: dict,
                    *, signed_in: bool = False, active: bool = True) -> None:
    # Empty optional claims must not erase an imported department/name.
    name = profile["display_name"] or person.get("display_name") or ""
    email = profile["email"] or person.get("email") or ""
    if signed_in and person.get("email"):
        # A login UPN may use a different domain from the employee's mailbox.
        # Keep the contact/OAuth address until a directory mail update supplies it.
        email = person["email"]
    department = profile["department"] or person.get("department") or ""
    extra = ", last_signin_at=NOW()" if signed_in else ""
    cur.execute("UPDATE directory_users SET display_name=%s,email=%s,department=%s,"
                "entra_tenant_id=%s,entra_oid=%s,identity_source='entra',is_active=%s,"
                "synced_at=NOW()" + extra + " WHERE id=%s",
                (name, email, department, tenant, oid, int(active), person["id"]))


def provision_from_claims(claims: dict) -> dict:
    """Preserve the directory person; create a login account exactly once."""
    tenant, oid = require_active_identity(claims)
    profile = _profile(claims)
    try:
        conn = get_maria_conn()
    except Exception as exc:
        logger.warning("directory_connection_failed", error_type=type(exc).__name__)
        raise DirectoryError("사용자 정보를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.", 503) from None
    locked = False
    try:
        with conn.cursor() as cur:
            _lock(cur)
            locked = True
            conn.begin()
            person = _find_person(cur, tenant, oid, profile["email"])
            if person is None:
                person = _insert_person(cur, tenant, oid, profile)
            if not person.get("is_active"):
                raise DirectoryError("이용이 중지된 계정입니다. 관리자에게 문의해 주세요.")
            cur.execute("SELECT id,role,email,entra_oid,is_active,ad_user_id,"
                        "requires_group_assignment FROM users "
                        "WHERE ad_user_id=%s OR entra_oid=%s FOR UPDATE", (person["id"], oid))
            accounts = cur.fetchall()
            if len(accounts) > 1:
                raise DirectoryError("사용자 연결이 중복돼 있습니다. 관리자에게 문의해 주세요.")
            account = accounts[0] if accounts else None
            created = account is None
            if account:
                if not account.get("is_active"):
                    raise DirectoryError("이용이 중지된 계정입니다. 관리자에게 문의해 주세요.")
                if account.get("entra_oid") not in (None, "", oid) or account["ad_user_id"] != person["id"]:
                    raise DirectoryError("기존 사용자 연결을 확인해 주세요.", 409)
                cur.execute("UPDATE users SET entra_oid=%s,last_login=NOW(),"
                            "must_change_password=0 WHERE id=%s", (oid, account["id"]))
            else:
                password_hash = bcrypt.hashpw(secrets.token_urlsafe(48).encode(),
                                               bcrypt.gensalt()).decode()
                cur.execute("INSERT INTO users "
                            "(email,password_hash,display_name,role,allowed_models,ad_user_id,"
                            "is_active,last_login,entra_oid,must_change_password,requires_group_assignment) "
                            "VALUES (%s,%s,%s,'user',%s,%s,1,NOW(),%s,0,%s)",
                            (person.get("email") or profile["email"] or f"entra_{oid}@noemail.local",
                             password_hash, profile["display_name"] or person.get("display_name") or "",
                             ALL_MODELS, person["id"], oid, 1))
                account = {"id": cur.lastrowid, "role": "user", "ad_user_id": person["id"],
                           "requires_group_assignment": 1}
            # 새 계정(플래그 1)에 브랜드 그룹이 없으면 부서로 기본 그룹을 붙인다 (2026-09-16).
            # ⚠️ 옛 계정(플래그 0)은 그룹이 없어도 전체가 열려 있으므로 건드리지 않는다 —
            #    뒤늦게 붙이면 접근이 조용히 좁아진다. 플래그 자체는 그대로 둔다: 그룹이
            #    나중에 회수되면 다시 막혀야 한다
            if account.get("requires_group_assignment"):
                assign_default_group(cur, person["id"],
                                     profile["department"] or person.get("department"))
            _update_profile(cur, person, tenant, oid, profile, signed_in=True)
            conn.commit()
            return {"id": int(account["id"]), "role": account["role"],
                    "ad_user_id": int(person["id"]), "created": created}
    except DirectoryError:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        logger.warning("directory_provision_failed", error_type=type(exc).__name__)
        raise DirectoryError("사용자 정보를 저장하지 못했습니다. 관리자에게 문의해 주세요.", 503) from None
    finally:
        if locked:
            with conn.cursor() as cur:
                _unlock(cur)
        conn.close()


def ensure_directory_sync_table() -> None:
    execute("""CREATE TABLE IF NOT EXISTS directory_sync_state (
        id TINYINT PRIMARY KEY, attempted_at DATETIME NULL, succeeded_at DATETIME NULL,
        user_count INT NOT NULL DEFAULT 0, error_message VARCHAR(500) NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")


def directory_sync_status() -> dict:
    row = fetch_one("SELECT attempted_at,succeeded_at,user_count,error_message "
                    "FROM directory_sync_state WHERE id=1") or {}
    return {k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in row.items()}


def _graph_users() -> list[dict]:
    s = get_settings()
    tenant = _uuid(s.entra_tenant_id)
    try:
        response = requests.post(
            f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
            data={"client_id": s.entra_client_id, "client_secret": s.entra_client_secret,
                  "grant_type": "client_credentials", "scope": "https://graph.microsoft.com/.default"},
            timeout=15,
        )
        if response.status_code != 200:
            raise DirectoryError("Entra 사용자 조회 인증을 확인해 주세요.", 503)
        token_response = response.json()
        if not isinstance(token_response, dict):
            raise DirectoryError("Entra 사용자 조회 인증을 확인해 주세요.", 503)
        token = token_response.get("access_token")
        if not token:
            raise DirectoryError("Entra 사용자 조회 인증을 확인해 주세요.", 503)
        url, params, seen, users = _GRAPH, {"$select": _SELECT, "$top": 999}, set(), []
        while url:
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.netloc != "graph.microsoft.com" or url in seen:
                raise DirectoryError("Entra 사용자 목록의 다음 페이지를 확인하지 못했습니다.", 503)
            seen.add(url)
            response = requests.get(url, params=params, headers={"Authorization": "Bearer " + token},
                                    timeout=20)
            if response.status_code == 403:
                raise DirectoryError("Entra 전체 사용자 동기화 권한이 없습니다. IT 관리자에게 "
                                     "Microsoft Graph User.Read.All 앱 권한과 관리자 동의를 요청해 주세요.", 503)
            if response.status_code != 200:
                raise DirectoryError("Entra 사용자 목록을 가져오지 못했습니다. 잠시 후 다시 시도해 주세요.", 503)
            page = response.json()
            if not isinstance(page, dict) or not isinstance(page.get("value"), list):
                raise DirectoryError("Entra 사용자 목록 응답을 확인하지 못했습니다.", 503)
            users.extend(page["value"])
            url, params = page.get("@odata.nextLink"), None
        if not users:
            raise DirectoryError("Entra 사용자 목록이 비어 있어 기존 사용자 정보를 유지했습니다.", 503)
        return users
    except requests.RequestException:
        raise DirectoryError("Entra 사용자 조회 서버에 연결하지 못했습니다. "
                             "graph.microsoft.com:443 네트워크 허용을 확인해 주세요.", 503) from None
    except (ValueError, TypeError):
        raise DirectoryError("Entra 사용자 목록 응답을 확인하지 못했습니다.", 503) from None


def _apply_graph_users(users: list[dict]) -> int:
    if not isinstance(users, list) or not users or any(not isinstance(user, dict) for user in users):
        raise DirectoryError("Entra 사용자 목록이 불완전하여 기존 사용자 정보를 유지했습니다.", 503)
    tenant = _uuid(get_settings().entra_tenant_id)
    normalized = []
    ids = set()
    for user in users:
        oid = _uuid(user.get("id"))
        if oid in ids or user.get("userType") not in ("Member", "Guest") or not isinstance(user.get("accountEnabled"), bool):
            raise DirectoryError("Entra 사용자 목록이 불완전하여 기존 사용자 정보를 유지했습니다.", 503)
        ids.add(oid)
        normalized.append((oid, user))
    conn = get_maria_conn()
    locked = False
    try:
        with conn.cursor() as cur:
            _lock(cur)
            locked = True
            conn.begin()
            count = 0
            for oid, user in normalized:
                profile = {"display_name": str(user.get("displayName") or "")[:200],
                           "email": _email(user.get("mail")) or _email(user.get("userPrincipalName")),
                           "department": str(user.get("department") or "")[:200]}
                cur.execute("SELECT * FROM directory_users WHERE entra_tenant_id=%s AND entra_oid=%s "
                            "FOR UPDATE", (tenant, oid))
                person = cur.fetchone()
                member = user["userType"] == "Member"
                if not member and not person:
                    continue
                if not person:
                    match_email = _email(user.get("userPrincipalName")) or profile["email"]
                    person = _find_person(cur, tenant, oid, match_email, graph_verified=True)
                if person and not _email(user.get("mail")):
                    profile["email"] = person.get("email") or profile["email"]
                if not person:
                    person = _insert_person(cur, tenant, oid, profile)
                _update_profile(cur, person, tenant, oid, profile,
                                active=member and user["accountEnabled"])
                count += int(member)
            marks = ",".join(["%s"] * len(ids))
            cur.execute("UPDATE directory_users SET is_active=0 WHERE entra_tenant_id=%s "
                        f"AND entra_oid IS NOT NULL AND entra_oid NOT IN ({marks})", (tenant, *sorted(ids)))
            cur.execute("INSERT INTO directory_sync_state "
                        "(id,attempted_at,succeeded_at,user_count,error_message) VALUES (1,NOW(),NOW(),%s,NULL) "
                        "ON DUPLICATE KEY UPDATE attempted_at=NOW(),succeeded_at=NOW(),"
                        "user_count=VALUES(user_count),error_message=NULL", (count,))
            conn.commit()
            return count
    except Exception:
        conn.rollback()
        raise
    finally:
        if locked:
            with conn.cursor() as cur:
                _unlock(cur)
        conn.close()


def sync_directory() -> dict:
    try:
        count = _apply_graph_users(_graph_users())
        return {"ok": True, "synced_users": count, "message": f"Entra 사용자 {count}명을 동기화했습니다."}
    except Exception as exc:
        message = str(exc) if isinstance(exc, DirectoryError) else "사용자 동기화를 완료하지 못했습니다."
        logger.warning("directory_sync_failed", error_type=type(exc).__name__)
        execute("INSERT INTO directory_sync_state (id,attempted_at,error_message) VALUES (1,NOW(),%s) "
                "ON DUPLICATE KEY UPDATE attempted_at=NOW(),error_message=VALUES(error_message)", (message[:500],))
        return {"ok": False, "synced_users": 0, "error": message}
