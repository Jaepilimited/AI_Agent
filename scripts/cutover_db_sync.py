"""컷오버용 최종 DB 동기화: 기존 프로덕션 → 신규 DB 서버.

기존 프로덕션(172.16.1.250 / MariaDB 11.7 / skin1004_ai)에서 AI Agent 테이블만
핫덤프(--single-transaction)해서 신규 DB(10.1.200.5 / ai)에 복원한다.
crm_* 테이블은 CRM 전용 서버로 별도 이관되므로 제외한다.

사용:
    set CRAVER_SSH_PW=...
    set CRAVER_DB_PW=...
    python scripts/cutover_db_sync.py            # 덤프 + 복원 + 검증
    python scripts/cutover_db_sync.py --verify   # 양쪽 행수 대조만

주의: 덤프 시작 이후 기존 프로덕션에 들어온 쓰기는 신규 DB에 반영되지 않는다.
      덤프~복원 소요는 약 20초이므로 그 사이 유입만 누락된다.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
DUMP_BIN = r"C:\Program Files\MariaDB 11.7\bin\mysqldump.exe"
WAS = "10.1.150.5"
REMOTE_DUMP = "/home/jeffrey/cutover.sql"
CHECK_TABLES = ["users", "ad_users", "conversations", "messages",
                "knowledge_wiki", "audit_logs", "message_feedback"]


def src_conn():
    from dotenv import load_dotenv
    load_dotenv(PROJ / ".env")
    import pymysql
    return pymysql.connect(
        host=os.getenv("MARIADB_HOST"), port=int(os.getenv("MARIADB_PORT", "3306")),
        user=os.getenv("MARIADB_USER"), password=os.getenv("MARIADB_PASSWORD"),
        database="skin1004_ai")


def counts_src():
    cur = src_conn().cursor()
    out = {}
    for t in CHECK_TABLES:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            out[t] = cur.fetchone()[0]
        except Exception:
            out[t] = None
    return out


def main() -> int:
    ssh_pw = os.getenv("CRAVER_SSH_PW", "")
    db_pw = os.getenv("CRAVER_DB_PW", "")
    if not ssh_pw or not db_pw:
        print("CRAVER_SSH_PW / CRAVER_DB_PW 환경변수가 필요합니다.")
        return 1
    try:
        import paramiko
    except ImportError:
        print("paramiko 필요")
        return 1

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(WAS, username="jeffrey", password=ssh_pw, timeout=30, banner_timeout=30)
    sftp = c.open_sftp()

    # my.cnf : 비밀번호에 '#' 가 있으면 주석으로 잘리므로 반드시 따옴표로 감싼다
    with sftp.open("/home/jeffrey/.my.cnf", "w") as f:
        f.write(f'[client]\nhost=10.1.200.5\nuser=ai\npassword="{db_pw}"\n'
                f'default-character-set=utf8mb4\n')
    sftp.chmod("/home/jeffrey/.my.cnf", 0o600)

    def run(cmd, t=3600):
        _, o, e = c.exec_command(cmd, timeout=t)
        out = o.read().decode("utf-8", "replace")
        err = e.read().decode("utf-8", "replace")
        if out.strip():
            print(out.rstrip())
        if err.strip() and "Warning" not in err and "sudo" not in err and "real" not in err:
            print("[err]", err.strip()[:300])
        return out

    def counts_dst():
        out = {}
        for t in CHECK_TABLES:
            r = run(f"mysql ai -N -e 'SELECT COUNT(*) FROM {t}' 2>/dev/null").strip()
            out[t] = int(r) if r.isdigit() else None
        return out

    if "--verify" in sys.argv:
        print("=== 행수 대조 (기존 prod vs 신규) ===")
        s, d = counts_src(), counts_dst()
        for t in CHECK_TABLES:
            mark = "일치" if s[t] == d[t] else f"차이 {(d[t] or 0) - (s[t] or 0):+d}"
            print(f"  {t:<18} prod={s[t]:<8} 신규={d[t]:<8} {mark}")
        sftp.close(); c.close()
        return 0

    # 1) 제외할 crm_* 목록 확보
    cur = src_conn().cursor()
    cur.execute("SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='skin1004_ai' AND table_name LIKE 'crm_%'")
    crm = [r[0] for r in cur.fetchall()]
    print(f"  CRM 테이블 {len(crm)}개 제외 (별도 트랙)")

    # 2) 핫덤프
    local = PROJ / "cutover.sql"
    from dotenv import load_dotenv
    load_dotenv(PROJ / ".env")
    cmd = [DUMP_BIN, "-h", os.getenv("MARIADB_HOST"), "-P", os.getenv("MARIADB_PORT", "3306"),
           "-u", os.getenv("MARIADB_USER"), f"-p{os.getenv('MARIADB_PASSWORD')}",
           "--single-transaction", "--routines", "--triggers", "--events",
           "--default-character-set=utf8mb4", "--skip-lock-tables", "skin1004_ai"]
    for t in crm:
        cmd += ["--ignore-table", f"skin1004_ai.{t}"]
    print("  덤프 중...")
    with open(local, "wb") as f:
        p = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE)
    if p.returncode != 0:
        print("  덤프 실패:", p.stderr.decode("utf-8", "replace")[:300])
        return 1
    size = local.stat().st_size / 1024 / 1024
    tables = len(re.findall(r"CREATE TABLE", local.read_text(encoding="utf-8", errors="replace")))
    print(f"  덤프 완료: {size:.1f} MB / 테이블 {tables}개")

    # 3) 전송 → 복원
    print("  전송 중...")
    sftp.put(str(local), REMOTE_DUMP)
    print("  앱 정지 → 복원 → 재기동")
    run(f"echo '{ssh_pw}' | sudo -S -p '' systemctl stop ai-craver")
    run(f"mysql ai < {REMOTE_DUMP} && echo '  복원 완료'")
    run(f"echo '{ssh_pw}' | sudo -S -p '' systemctl start ai-craver; sleep 14; "
        f"systemctl is-active ai-craver | sed 's/^/  서비스: /'")
    run("curl -s -o /dev/null -w '  /health HTTP %{http_code}\\n' --max-time 15 "
        "http://127.0.0.1:3000/health")

    # 4) 검증
    print("\n=== 행수 대조 ===")
    s, d = counts_src(), counts_dst()
    ok = True
    for t in CHECK_TABLES:
        diff = (d[t] or 0) - (s[t] or 0)
        mark = "일치" if diff == 0 else f"차이 {diff:+d}"
        if diff != 0:
            ok = False
        print(f"  {t:<18} prod={s[t]:<8} 신규={d[t]:<8} {mark}")
    print("\n  " + ("전부 일치 — 동기화 완료" if ok
                    else "차이 있음 — 덤프 이후 유입분. 필요시 재실행"))
    run(f"rm -f {REMOTE_DUMP}")
    local.unlink(missing_ok=True)
    sftp.close()
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
