"""신규 서버(WAS)에 FI 열람 권한을 반영하고 실제로 막히는지 검증한다.

`deploy_new_server.py was` 로 코드를 올린 다음 실행한다. 코드 배포만으로는
DB 쪽이 비어 있어 아무도 손익을 못 보게 된다 — 이 스크립트가 그 간극을 메운다.

사용:
    set CRAVER_SSH_PW=...
    python scripts/rollout_fi_access.py            # 컬럼 확인 → 8명 부여 → E2E
    python scripts/rollout_fi_access.py --check    # 변경 없이 현황만 확인

하는 일:
  1. ad_users.can_view_fi 컬럼 존재 확인 (앱 기동 시 자동 생성되지만 확인은 필요)
  2. scripts/grant_fi_access.py 로 승인된 8명에게 권한 부여
  3. 서버 내부에서 실제 HTTP 로 차단/허용 동작 검증
"""
from __future__ import annotations

import os
import sys

WAS = "10.1.150.5"
REMOTE = "/home/jeffrey/AI_Agent"

REMOTE_SCRIPT = r'''
import json, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, "/home/jeffrey/AI_Agent")
from dotenv import load_dotenv
load_dotenv("/home/jeffrey/AI_Agent/.env")
import jwt, httpx, pymysql
from app.config import get_settings
from app.core.security import FI_ACCESS_DENIED_MESSAGE

CHECK_ONLY = os.environ.get("FI_CHECK_ONLY") == "1"
BASE = "http://127.0.0.1:3000"
S = get_settings()
DENY_HEAD = FI_ACCESS_DENIED_MESSAGE.split("\n")[0]
results = []

def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))

cn = pymysql.connect(host=os.getenv("MARIADB_HOST"), port=int(os.getenv("MARIADB_PORT", "3306")),
                     user=os.getenv("MARIADB_USER"), password=os.getenv("MARIADB_PASSWORD"),
                     database=os.getenv("MARIADB_DATABASE"), cursorclass=pymysql.cursors.DictCursor)
cur = cn.cursor()

# ── 1. 컬럼 확인 ──
print("[1] 컬럼 확인")
cur.execute("SHOW COLUMNS FROM ad_users LIKE 'can_view_fi'")
col = cur.fetchone()
check("ad_users.can_view_fi 존재", bool(col), "" if col else "앱이 아직 기동 안 됐거나 배포 누락")
if not col:
    print("\n중단: 컬럼이 없어 이후 단계를 건너뜁니다. ai-craver 재기동 후 다시 실행하세요.")
    cn.close(); sys.exit(1)

# ── 2. 권한 부여 ──
print("\n[2] 권한 부여")
FI_USERNAMES = ("jeffrey", "smyang", "chris", "hejin", "hlnam", "chloe_woo", "kwak", "sckang")
ph = ", ".join(["%s"] * len(FI_USERNAMES))
cur.execute(f"SELECT username, display_name, can_view_fi FROM ad_users WHERE username IN ({ph})",
            FI_USERNAMES)
rows = {r["username"].lower(): r for r in cur.fetchall()}
missing = [u for u in FI_USERNAMES if u not in rows]
check("승인 8명 모두 ad_users 에 존재", not missing, f"누락: {missing}" if missing else "")
if missing:
    cn.close(); sys.exit(1)

before = sum(1 for u in FI_USERNAMES if rows[u]["can_view_fi"])
print(f"       부여 전 허용 인원: {before}/8")
if CHECK_ONLY:
    print("       --check 모드 — DB 변경 없음")
else:
    cur.execute(f"UPDATE ad_users SET can_view_fi = 1 WHERE username IN ({ph})", FI_USERNAMES)
    cn.commit()
    print(f"       UPDATE 적용 (영향 행 {cur.rowcount})")

cur.execute("SELECT COUNT(*) c FROM ad_users WHERE is_active = 1 AND can_view_fi = 1")
total = cur.fetchone()["c"]
check("허용 인원 정확히 8명", total == 8 or CHECK_ONLY, f"현재 {total}명")

cur.execute("SELECT u.id, u.email, u.role FROM users u WHERE u.role='admin' ORDER BY u.id LIMIT 1")
admin = cur.fetchone()
cur.execute("SELECT u.id, u.email, u.role, u.display_name FROM users u "
            "JOIN ad_users a ON u.ad_user_id = a.id "
            "WHERE a.can_view_fi = 1 AND u.role <> 'admin' LIMIT 1")
allowed = cur.fetchone()
cur.execute("SELECT u.id, u.email, u.role, u.display_name FROM users u "
            "LEFT JOIN ad_users a ON u.ad_user_id = a.id "
            "WHERE u.role <> 'admin' AND COALESCE(a.can_view_fi, 0) = 0 LIMIT 1")
denied = cur.fetchone()
cn.close()

def tok(u):
    return jwt.encode({"user_id": u["id"], "email": u.get("email") or "", "role": u["role"],
                       "brand_filter": "",
                       "exp": datetime.now(timezone.utc) + timedelta(minutes=25)},
                      S.jwt_secret_key, algorithm="HS256")

def ask(u, q, c):
    r = c.post("/v1/chat/completions",
               json={"model": "claude", "stream": False,
                     "messages": [{"role": "user", "content": q}]},
               cookies={"token": tok(u)})
    if r.status_code != 200:
        return f"<HTTP {r.status_code}>"
    d = r.json()
    return (d.get("choices", [{}])[0].get("message", {}) or {}).get("content", "")

# ── 3. 실제 동작 검증 ──
print("\n[3] 실제 동작 검증 (서버 내부 HTTP)")
with httpx.Client(base_url=BASE, timeout=240.0) as c:
    for label, u, expect in (("admin", admin, True), ("허용자", allowed, True), ("차단자", denied, False)):
        if not u:
            check(f"/me {label} 계정 확보", False, "해당 계정 없음"); continue
        r = c.get("/api/auth/me", cookies={"token": tok(u)})
        got = r.json().get("can_view_fi") if r.status_code == 200 else None
        check(f"/me {label} can_view_fi={expect}", got is expect, f"got={got}")

    if denied:
        a = ask(denied, "2026년 6월 영업이익 얼마야?", c)
        check("차단자 손익 질문 → 안내 메시지", DENY_HEAD in a, repr(a[:100]))
        a = ask(denied, "안녕? 너는 뭘 할 수 있어?", c)
        check("차단자 일반 질문은 정상", len(a) > 10 and DENY_HEAD not in a, f"len={len(a)}")
        r = c.get("/api/admin/ad/users", cookies={"token": tok(denied)})
        check("비관리자 admin API 403", r.status_code == 403, f"status={r.status_code}")

    target = allowed or admin
    if target:
        a = ask(target, "2026년 6월 영업이익 얼마야?", c)
        check("허용자 손익 질문 → 정상 조회", DENY_HEAD not in a and len(a) > 20, repr(a[:150]))
        print(f"\n[허용자 답변 미리보기]\n{a[:400]}")

    if admin:
        r = c.get("/api/admin/ad/users", params={"fi_only": "true"}, cookies={"token": tok(admin)})
        us = r.json() if r.status_code == 200 else []
        check("admin fi_only 필터 8명", len(us) == 8,
              f"{len(us)}명: {sorted(u.get('display_name') or '' for u in us)}")

fails = [n for n, ok in results if not ok]
print("\n" + "=" * 50)
print(f"총 {len(results)}건 — 통과 {len(results)-len(fails)} / 실패 {len(fails)}")
for n in fails:
    print("  실패:", n)
sys.exit(1 if fails else 0)
'''


def main() -> int:
    check_only = "--check" in sys.argv
    pw = os.getenv("CRAVER_SSH_PW", "")
    if not pw:
        print("CRAVER_SSH_PW 환경변수가 필요합니다. (노션 AI Craver 페이지 참조)")
        return 1
    try:
        import paramiko
    except ImportError:
        print("paramiko 필요: pip install paramiko")
        return 1

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(WAS, username="jeffrey", password=pw, timeout=30, banner_timeout=30)
    sftp = c.open_sftp()
    with sftp.open(f"{REMOTE}/_fi_rollout.py", "w") as f:
        f.write(REMOTE_SCRIPT)

    mode = "현황 확인" if check_only else "권한 부여 + 검증"
    print(f"===== FI 열람 권한 롤아웃 ({mode}) — {WAS} =====\n")
    env = "FI_CHECK_ONLY=1 " if check_only else ""
    _, o, e = c.exec_command(
        f"cd {REMOTE} && {env}./venv/bin/python _fi_rollout.py 2>&1; "
        f"rc=$?; rm -f _fi_rollout.py; exit $rc",
        timeout=2400,
    )
    print(o.read().decode("utf-8", "replace").rstrip())
    rc = o.channel.recv_exit_status()
    err = e.read().decode("utf-8", "replace").strip()
    if err:
        print("[stderr]", err[:400])
    sftp.close()
    c.close()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
