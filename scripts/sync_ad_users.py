"""Sync Active Directory users to MariaDB.

Queries AD via LDAPS for active users, then upserts into MariaDB ad_users table.
After sync, auto-heals display names from users table (Korean names take priority).

Usage:
  python scripts/sync_ad_users.py              # Full sync + auto-heal
  python scripts/sync_ad_users.py --dry-run    # Preview only (no DB writes)
  python scripts/sync_ad_users.py --heal-only  # Skip AD sync, run name heal only
"""
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(BASE_DIR, ".env"))

import pymysql

LOCK_FILE = Path(BASE_DIR) / "logs" / "ad_sync.lock"
LOG_FILE  = Path(BASE_DIR) / "logs" / "ad_sync.log"
JANDI_URL = "https://wh.jandi.com/connect-api/webhook/11320800/7c1bdd4a0947be10377703affd57e97a"


# ── 로깅 ─────────────────────────────────────────────────────────────────────

_log_lines: list[str] = []

def step(label: str):
    msg = f"\n[STEP] {label}\n" + "-" * 50
    print(msg)
    _log_lines.append(msg)

def ok(msg: str):
    line = f"  OK  {msg}"
    print(line)
    _log_lines.append(line)

def info(msg: str):
    line = f"  >>  {msg}"
    print(line)
    _log_lines.append(line)

def err(msg: str):
    line = f"  !!  {msg}"
    print(line, file=sys.stderr)
    _log_lines.append(line)

def _write_log(success: bool):
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        status = "SUCCESS" if success else "FAILED"
        header = f"\n{'='*60}\n[{timestamp}] {status}\n{'='*60}"
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(header + "\n")
            f.write("\n".join(_log_lines) + "\n")
    except Exception:
        pass


# ── Jandi 알림 ───────────────────────────────────────────────────────────────

def _jandi_notify(title: str, body: str, color: str = "#e89200"):
    try:
        msg = {
            "body": body,
            "connectColor": color,
            "connectInfo": [{"title": title, "description": datetime.now().strftime("%Y-%m-%d %H:%M")}],
        }
        data = json.dumps(msg).encode("utf-8")
        req = urllib.request.Request(
            JANDI_URL,
            data=data,
            headers={
                "Accept": "application/vnd.tosslab.jandi-v2+json",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5).read()
    except Exception as e:
        # 조용히 삼키면 "알림이 안 온다 = 문제가 없다" 로 오해하게 된다.
        # 실제로 APP 서버조차 프록시에서 wh.jandi.com 이 403 이라 이 알림은
        # 계속 실패하고 있었는데, `except: pass` 라 로그에 흔적이 없었다
        # (2026-08-18 확인). 이 저장소가 반복해서 겪는 조용한 실패다.
        err(f"잔디 알림 실패 — {type(e).__name__}: {str(e)[:120]}")


# ── 환경변수 ──────────────────────────────────────────────────────────────────

def get_env(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(f"환경변수 없음: {key}")
    return val


# ── DB 연결 ───────────────────────────────────────────────────────────────────

def get_db_connection():
    return pymysql.connect(
        host=get_env("MARIADB_HOST"),
        port=int(os.getenv("MARIADB_PORT", "3306")),
        user=get_env("MARIADB_USER"),
        password=get_env("MARIADB_PASSWORD"),
        database=get_env("MARIADB_DATABASE"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


# ── 이름 수동 오버라이드 ──────────────────────────────────────────────────────
# 미등록 사용자(users 테이블에 없는 사람)의 AD displayName이 영문인 경우 여기에 추가.
# 이미 가입한 사람은 auto-heal이 자동으로 처리하므로 추가 불필요.
_NAME_OVERRIDES: dict[str, str] = {
    "jeffrey@skin1004korea.com": "임재필",
    "smyang@skin1004korea.com": "양승민",
    "yang.ceci@skin1004korea.com": "양다솜",
    "light6475@skin1004korea.com": "서민경",
    "roselee@skin1004korea.com": "이지영",
    "js.bae@skin1004korea.com": "배진서",
    "chris@skin1004korea.com": "이형섭",
    "jsp@skin1004korea.com": "박지수",
    "jkoh@skin1004korea.com": "오자경",
    "kak@skin1004korea.com": "김경아",
    "skylar@skin1004korea.com": "노소영",
    "jaepilyoon@cravercorp.com": "윤재필",
    "hjcheon@skin1004korea.com": "천혜진",
    "jhlee@skin1004korea.com": "이주훈",
    "doheek@skin1004korea.com": "김도희",
    "jyb@skin1004korea.com": "정예빈",
    "phj@skin1004korea.com": "박혜진",
    "yajeong@skin1004korea.com": "정윤아",
    "haily@skin1004korea.com": "신혜연",
    "hjseo10@skin1004korea.com": "서현재",
    "sjhan@skin1004korea.com": "한수진",
    "jay.sim@skin1004korea.com": "심재권",
    "sekim@skin1004korea.com": "김세은",
    "jhk@skin1004korea.com": "강지훈",
    "matthew@skin1004korea.com": "진광열",
    "jhbyeon@skin1004korea.com": "변지혜",
    "hayoon@skin1004korea.com": "정하윤",
    "jes@skin1004korea.com": "조은서",
    "camilak0314@skin1004korea.com": "김서현",
    "pa1004@skin1004korea.com": "안나",
    # ── 영문+한자 복합 display_name 유저 (email 키) ────────────────────────
    "dspark@skin1004korea.com": "박다솜",
    "dhlee@cravercorp.com": "이도학",
    "gayeon@cravercorp.com": "유가연",
    "chillahs@cravercorp.com": "이한서",
    "jungle@cravercorp.com": "정재명",
    "kmsgkgk@cravercorp.com": "강민성",
    "ksw@cravercorp.com": "권상우",
    "csj@cravercorp.com": "최서정",
    "twchoi@cravercorp.com": "최태웅",
    "yein@cravercorp.com": "김예인",
    "ydlee@skin1004korea.com": "이용두",
    # ── 순수 영문 display_name (email 키) ─────────────────────────────────
    "cjswngur@cravercorp.com": "천주혁",
    "hichun@cravercorp.com": "전항일",
    "moonsub.song@cravercorp.com": "송문섭",
    # ── 이메일 없는 유저 (username 키) ────────────────────────────────────
    "hrkang": "강희림",
    "hban": "안홍비",
    "whlee": "이원희",
}

# ── 부서/이메일 수동 오버라이드 ────────────────────────────────────────────────
# 2026-07-22: CRM(skin1004-crm/scripts/sync-ad-users.py)의 B2B팀 전용 포크에만
# 있던 오버라이드를 병합. Task Scheduler(SKIN1004-AD-Sync-Daily)가 다시 이 스크립트를
# 정식으로 실행하게 되면서, CRM 포크가 갖고 있던 부서/이메일 보정이 이 스크립트에도
# 필요해졌다 (CRM 쪽은 B2B 팀 검증/신규팀원 자동등록 전용으로 남고, ad_users 동기화
# 자체는 여기 한 곳에서만 한다).
_DEPARTMENT_OVERRIDES: dict[str, str] = {
    # 신혜연: AD 상 'Sales Dept'에서 끊겨 영업1 필터에서 누락됨. 팀장이므로 강제.
    "haily": "Craver_Accounts > Users > Brand Division > Sales Dept > 영업1 > 글로벌세일즈1",
}

_EMAIL_OVERRIDES: dict[str, str] = {
    # 보정된 한글 display_name 기준 (이름 오버라이드 적용 후).
    "서주희": "juhee.seo@skin1004korea.com",
    "송민서": "minseosong@skin1004korea.com",
    "이수현": "shlee1@skin1004korea.com",
    "조민경": "mkcho@skin1004korea.com",
    "최서연": "sychoi@skin1004korea.com",
    "신혜연": "haily@skin1004korea.com",
    "이새":   "jesse.lee@skin1004korea.com",
    "조한나": "hanna13@skin1004korea.com",
    # 2026-08-05: AD 에 mail 속성이 없어 users.email 이 빈 문자열로 남아 있던 건
    # (2026-03-17 noemail.local 폴백 도입 전에 가입해 대체값조차 못 받았다).
    "전휘빈": "hbjeon@skin1004korea.com",
}


# ── 복합 display_name 자동 추출 ──────────────────────────────────────────────
# AD가 "EngName_한글이름_漢字" 형태로 저장하는 경우 한글 부분만 추출.
# 명시적 _NAME_OVERRIDES 가 없을 때만 적용.
_COMPLEX_RE = re.compile(r'^[A-Za-z0-9_]+_([가-힣]{2,6})(?:_[^\s]*)?$')

def _extract_korean_name(display_name: str) -> str | None:
    m = _COMPLEX_RE.match(display_name)
    return m.group(1) if m else None


# ── STEP 1: AD에서 사용자 가져오기 (재시도 3회) ──────────────────────────────

def _entry_to_user(entry) -> dict:
    """Convert an ldap3 entry without its stdout-dependent string renderer."""
    dn = entry.entry_dn
    ou_parts = [part.replace("OU=", "") for part in dn.split(",") if part.startswith("OU=")]
    ou_path = " > ".join(reversed(ou_parts)) if ou_parts else "Root"

    username = entry.sAMAccountName.value
    display = entry.displayName.value if entry.displayName and entry.displayName.value else entry.name.value
    email = entry.mail.value if entry.mail and entry.mail.value else None

    return {
        "username": username,
        "display_name": display,
        "email": email,
        "department": ou_path,
        "full_dn": dn,
    }


def fetch_ad_users(retries: int = 3) -> list[dict]:
    from ldap3 import Server, Connection, Tls, ALL, SUBTREE

    ad_server  = get_env("AD_SERVER")
    ad_user    = get_env("AD_USER")
    ad_password = get_env("AD_PASSWORD")
    search_base = get_env("AD_SEARCH_BASE")

    info(f"AD 서버 연결: {ad_server}:636 (LDAPS)")

    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            tls_config = Tls(validate=ssl.CERT_NONE, version=ssl.PROTOCOL_TLSv1_2)
            server = Server(ad_server, port=636, use_ssl=True, tls=tls_config, get_info=ALL)

            with Connection(server, user=ad_user, password=ad_password, auto_bind=True) as conn:
                ok("AD 인증 성공")
                search_filter = (
                    "(&(objectClass=user)(objectCategory=person)"
                    "(!(userAccountControl:1.2.840.113556.1.4.803:=2)))"
                )
                conn.search(
                    search_base=search_base,
                    search_filter=search_filter,
                    search_scope=SUBTREE,
                    attributes=["sAMAccountName", "name", "displayName", "mail", "department"],
                )

                users = [_entry_to_user(entry) for entry in conn.entries]

                ok(f"AD 사용자 {len(users)}명 조회 완료")
                return users

        except Exception as e:
            last_exc = e
            err(f"AD 연결 실패 (시도 {attempt}/{retries}): {e}")
            if attempt < retries:
                time.sleep(5 * attempt)

    raise RuntimeError(f"AD 연결 {retries}회 모두 실패: {last_exc}")


# ── STEP 2: MariaDB에 upsert ─────────────────────────────────────────────────

def sync_to_db(users: list[dict], dry_run: bool = False):
    if dry_run:
        info("DRY RUN - DB 변경 없음")
        auto_preview = 0
        for u in users:
            email    = u.get("email") or ""
            username = u.get("username") or ""
            if email in _NAME_OVERRIDES:
                name = _NAME_OVERRIDES[email]
                tag  = "[override]"
            elif username in _NAME_OVERRIDES:
                name = _NAME_OVERRIDES[username]
                tag  = "[override]"
            else:
                korean = _extract_korean_name(u["display_name"])
                if korean:
                    name = korean
                    tag  = "[auto]"
                    auto_preview += 1
                else:
                    name = u["display_name"]
                    tag  = ""
            print(f"  {name} ({u['username']}) {tag} / {u['email'] or 'N/A'}")
        info(f"총 {len(users)}명 (자동 정제 예정: {auto_preview}명)")
        return

    conn = get_db_connection()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    inserted = updated = override_applied = auto_cleaned = 0

    try:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE ad_users SET is_active = 0, synced_at = %s", (now,))

            for u in users:
                email = u.get("email") or ""
                username = u.get("username") or ""
                if email in _NAME_OVERRIDES:
                    u["display_name"] = _NAME_OVERRIDES[email]
                    override_applied += 1
                elif username in _NAME_OVERRIDES:
                    u["display_name"] = _NAME_OVERRIDES[username]
                    override_applied += 1
                else:
                    # 복합 display_name 자동 정제 (EngName_한글_漢字 → 한글)
                    korean = _extract_korean_name(u["display_name"])
                    if korean:
                        info(f"  자동 정제: {u['username']} '{u['display_name']}' → '{korean}'")
                        u["display_name"] = korean
                        auto_cleaned += 1

                if username in _DEPARTMENT_OVERRIDES:
                    override_dept = _DEPARTMENT_OVERRIDES[username]
                    if u.get("department") != override_dept:
                        info(f"  부서 오버라이드: {u['display_name']}({username}) → '{override_dept}'")
                    u["department"] = override_dept

                # 이메일 오버라이드는 보정된 한글 이름 기준으로 매칭한다.
                if u["display_name"] in _EMAIL_OVERRIDES:
                    override_email = _EMAIL_OVERRIDES[u["display_name"]]
                    if u.get("email") != override_email:
                        info(f"  이메일 오버라이드: {u['display_name']} → '{override_email}'")
                    u["email"] = override_email

                cursor.execute("""
                    INSERT INTO ad_users (username, display_name, email, department, full_dn, is_active, synced_at)
                    VALUES (%s, %s, %s, %s, %s, 1, %s)
                    ON DUPLICATE KEY UPDATE
                        display_name = VALUES(display_name),
                        email        = VALUES(email),
                        department   = VALUES(department),
                        full_dn      = VALUES(full_dn),
                        is_active    = 1,
                        synced_at    = VALUES(synced_at)
                """, (u["username"], u["display_name"], u["email"], u["department"], u["full_dn"], now))

                if cursor.lastrowid:
                    inserted += 1
                else:
                    updated += 1

            conn.commit()

        ok(f"신규: {inserted}명 / 갱신: {updated}명 / 이름 오버라이드: {override_applied}명 / 자동 정제: {auto_cleaned}명")

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) as cnt FROM ad_users WHERE is_active = 1")
            active = cur.fetchone()["cnt"]
            cur.execute("SELECT COUNT(*) as cnt FROM ad_users WHERE is_active = 0")
            inactive = cur.fetchone()["cnt"]
        ok(f"활성: {active}명 / 비활성(AD에서 삭제됨): {inactive}명")

    finally:
        conn.close()


# ── STEP 3: 이름 자동 보정 ───────────────────────────────────────────────────

def heal_names(dry_run: bool = False, _retries: int = 3):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # 선제 정제: users.display_name 도 복합 패턴이면 한글만 추출
            cursor.execute(
                "SELECT id, email, display_name FROM users WHERE display_name REGEXP '^[A-Za-z0-9_]+_[가-힣]'"
            )
            complex_users = cursor.fetchall()
            pre_fixed = 0
            for u in complex_users:
                korean = _extract_korean_name(u["display_name"])
                if korean:
                    if dry_run:
                        info(f"  [DRY] users 정제: {u['email']} '{u['display_name']}' → '{korean}'")
                    else:
                        cursor.execute("UPDATE users SET display_name = %s WHERE id = %s", (korean, u["id"]))
                        pre_fixed += 1
            if not dry_run and pre_fixed:
                conn.commit()
                ok(f"users 복합 display_name {pre_fixed}명 자동 정제")

            cursor.execute("""
                SELECT ad.id, ad.username, ad.display_name AS ad_name,
                       u.display_name AS korean_name, u.email
                FROM ad_users ad
                JOIN users u ON ad.id = u.ad_user_id
                WHERE u.display_name REGEXP '[가-힣]'
                  AND ad.display_name != u.display_name
                ORDER BY u.email
            """)
            targets = cursor.fetchall()

            if not targets:
                ok("보정 대상 없음 (모두 최신 상태)")
                return

            if dry_run:
                info(f"보정 대상 {len(targets)}명 (DRY RUN):")
                for r in targets:
                    print(f"    {r['username']}: '{r['ad_name']}' -> '{r['korean_name']}' ({r['email']})")
                return

            for attempt in range(1, _retries + 1):
                try:
                    cursor.execute("""
                        UPDATE ad_users ad
                        JOIN users u ON ad.id = u.ad_user_id
                        SET ad.display_name = u.display_name
                        WHERE u.display_name REGEXP '[가-힣]'
                          AND ad.display_name != u.display_name
                    """)
                    conn.commit()
                    break
                except Exception as e:
                    # MariaDB 1020: "Record has changed since last read" — concurrent write.
                    # Re-fetch + retry so we apply heal on top of the latest committed state.
                    conn.rollback()
                    if attempt < _retries:
                        err(f"이름 보정 충돌 (시도 {attempt}/{_retries}), 재시도 중: {e}")
                        time.sleep(2 * attempt)
                        cursor.execute("""
                            SELECT ad.id, ad.username, ad.display_name AS ad_name,
                                   u.display_name AS korean_name, u.email
                            FROM ad_users ad
                            JOIN users u ON ad.id = u.ad_user_id
                            WHERE u.display_name REGEXP '[가-힣]'
                              AND ad.display_name != u.display_name
                            ORDER BY u.email
                        """)
                        targets = cursor.fetchall()
                        if not targets:
                            ok("재조회 결과 보정 대상 없음 (다른 프로세스가 완료)")
                            return
                    else:
                        raise

            ok(f"{len(targets)}명 이름 보정 완료:")
            for r in targets:
                print(f"    {r['username']}: '{r['ad_name']}' -> '{r['korean_name']}' ({r['email']})")

    finally:
        conn.close()


# ── Lock (동시 실행 방지) ─────────────────────────────────────────────────────

def _acquire_lock() -> bool:
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Remove stale lock (older than 10 minutes) before attempting atomic create.
    if LOCK_FILE.exists():
        age = time.time() - LOCK_FILE.stat().st_mtime
        if age >= 600:
            try:
                LOCK_FILE.unlink()
            except FileNotFoundError:
                pass
        else:
            return False
    try:
        # Atomic exclusive creation — raises FileExistsError if another process
        # already holds the lock (eliminates TOCTOU race).
        fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        return False

def _release_lock():
    try:
        LOCK_FILE.unlink()
    except Exception:
        pass


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    dry_run   = "--dry-run"   in sys.argv
    heal_only = "--heal-only" in sys.argv

    header = "Craver AD Sync + Name Heal"
    print("=" * 60)
    print(header)
    print("=" * 60)
    _log_lines.append(header)

    # 동시 실행 방지 (dry-run/heal-only는 lock 불필요)
    if not dry_run and not heal_only:
        if not _acquire_lock():
            err("이미 실행 중입니다 (lock file 존재). 종료.")
            sys.exit(1)

    success = False
    try:
        if not heal_only:
            step("STEP 1/2  AD -> MariaDB 동기화")
            try:
                users = fetch_ad_users()
            except Exception as e:
                err(f"AD 조회 실패: {e}")
                _write_log(False)
                _jandi_notify(
                    "❌ AD Sync 실패 — AD 조회 오류",
                    f"AD 서버에서 사용자 목록을 가져오지 못했습니다.\n오류: {e}",
                    color="#FF4444",
                )
                sys.exit(2)

            try:
                sync_to_db(users, dry_run=dry_run)
            except Exception as e:
                err(f"DB upsert 실패: {e}")
                _write_log(False)
                _jandi_notify(
                    "❌ AD Sync 실패 — DB 저장 오류",
                    f"AD 사용자를 DB에 저장하지 못했습니다.\n오류: {e}",
                    color="#FF4444",
                )
                sys.exit(3)
        else:
            info("--heal-only 모드: AD sync 건너뜀")

        step("STEP 2/2  이름 자동 보정 (users -> ad_users)")
        try:
            heal_names(dry_run=dry_run)
        except Exception as e:
            err(f"이름 보정 실패: {e}")
            _write_log(False)
            _jandi_notify(
                "⚠️ AD Sync 경고 — 이름 보정 실패",
                f"sync는 완료됐으나 이름 보정에 실패했습니다.\n오류: {e}",
                color="#FFA500",
            )
            sys.exit(4)

        print("\n" + "=" * 60)
        print("완료!")
        print("=" * 60)
        success = True

    finally:
        _write_log(success)
        if not dry_run and not heal_only:
            _release_lock()


def _tracked_main():
    """크론 실행을 job_runs 에 기록한다 — 조용히 실패하지 않도록.

    (2026-08-04: APP 서버 .env 의 DB 비밀번호 오타로 6일간 매일 밤 실패했는데
     아무도 몰랐다. dry-run/heal-only 는 정기 실행이 아니므로 기록하지 않는다.)
    """
    if "--dry-run" in sys.argv or "--heal-only" in sys.argv:
        return main()
    try:
        from app.core.self_check import track_job
    except Exception:
        return main()  # 기록 불가여도 본 작업은 수행한다
    with track_job("ad_sync") as jr:
        main()
        jr.set_note("AD sync 완료")


if __name__ == "__main__":
    _tracked_main()
