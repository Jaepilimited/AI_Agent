"""신규 서버 이관 상태 검증 — 언제든 직접 돌려서 확인하는 스크립트.

사용:
    python scripts/verify_migration.py            # 전체 검증
    python scripts/verify_migration.py --net      # 네트워크만 (빠름)

접속 정보는 노션 "AI Craver" 페이지 참조.
환경변수로 넘기거나, 없으면 프롬프트 없이 종료한다 (비밀번호를 코드에 두지 않는다).
    set CRAVER_SSH_PW=...      SSH 비밀번호 (jeffrey)
    set CRAVER_DB_PW=...       DB 비밀번호 (ai)

출력은 전부 실측값이다. 추정·기억으로 채우는 항목은 없다.
"""
from __future__ import annotations

import os
import sys
import socket
import select
import threading
from datetime import datetime

SSH_PW = os.getenv("CRAVER_SSH_PW", "")
DB_PW = os.getenv("CRAVER_DB_PW", "")
USER = "jeffrey"

SERVERS = {"Web": "10.1.100.5", "WAS": "10.1.150.5", "APP": "10.1.150.105"}
DB_HOST, DB_PORT = "10.1.200.5", 3306
# ⚠️ Proxy 실주소는 10.1.50.2 다. 노션 설계서(10.1.50.5)와 IT 메일(10.5.50.2)은 둘 다 오기였고,
#    2026-07-29 실측으로 10.1.50.2:3128 만 응답함을 확인했다.
PROXY, PROXY_PORT = "10.1.50.2", 3128
# 프록시 경유로 실제 도달해야 하는 외부 도메인 (숫자 응답이면 통과, 000이면 실패)
EXT_TARGETS = [
    ("Claude", "https://api.anthropic.com/v1/models"),
    ("Gemini", "https://generativelanguage.googleapis.com/"),
    ("BigQuery", "https://bigquery.googleapis.com/"),
    ("Notion", "https://api.notion.com/v1"),
]

OK, NG, WARN = "OK  ", "실패", "주의"


def stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def probe(host: str, port: int, timeout: float = 5.0) -> str:
    """로컬에서 TCP 도달 확인. OPEN / REFUSED / TIMEOUT 을 구분해 돌려준다."""
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return "OPEN"
    except socket.timeout:
        return "TIMEOUT"
    except ConnectionRefusedError:
        return "REFUSED"
    except OSError as e:
        return f"ERR({e.errno})"
    finally:
        s.close()


def main() -> int:
    net_only = "--net" in sys.argv
    print(f"=== AI Craver 이관 검증  {stamp()} ===")
    print(f"    출발지: {socket.gethostbyname(socket.gethostname())}\n")

    print("[1] 로컬(172.16.1.250) → 신규 서버 SSH")
    for name, ip in SERVERS.items():
        r = probe(ip, 22)
        print(f"    {name:<4} {ip:<14} 22   {OK if r == 'OPEN' else NG}  ({r})")

    if not SSH_PW:
        print("\n  CRAVER_SSH_PW 미설정 — 서버 내부 검증은 건너뜁니다.")
        print("  전체 검증: set CRAVER_SSH_PW=... 후 재실행")
        return 0

    try:
        import paramiko
    except ImportError:
        print("\n  paramiko 없음 — pip install paramiko 후 재실행")
        return 1

    def sh(host: str, cmd: str, timeout: int = 120) -> str:
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        c.connect(host, username=USER, password=SSH_PW, timeout=25, banner_timeout=30)
        _, out, _ = c.exec_command(cmd, timeout=timeout)
        r = out.read().decode("utf-8", "replace")
        c.close()
        return r

    probe_sh = (
        'p() { timeout 5 bash -c "echo > /dev/tcp/$1/$2" 2>/dev/null '
        '&& echo OPEN || echo "차단/무응답"; }\n'
    )

    print(f"\n[2] 서버 → Proxy {PROXY}:{PROXY_PORT}")
    for name, ip in SERVERS.items():
        try:
            r = sh(ip, probe_sh + f"p {PROXY} {PROXY_PORT}")
            print(f"    {name:<4} → proxy:3128   {r.strip()}")
        except Exception as e:
            print(f"    {name:<4} SSH 실패: {type(e).__name__}")

    print("\n[2-1] WAS → 외부 API (프록시 경유, 000=실패)")
    px = f"http://{PROXY}:{PROXY_PORT}"
    for label, url in EXT_TARGETS:
        try:
            code = sh(SERVERS["WAS"],
                      f"curl -s -o /dev/null -w '%{{http_code}}' -x {px} --max-time 15 '{url}'").strip()
            print(f"    {label:<9} {code}{'  (도달)' if code not in ('', '000') else '  ★실패'}")
        except Exception as e:
            print(f"    {label:<9} 확인 실패: {type(e).__name__}")

    print(f"\n[3] 서버 → DB {DB_HOST}:{DB_PORT}   (대조군 — 동일 게이트웨이 경유)")
    for name, ip in SERVERS.items():
        try:
            r = sh(ip, probe_sh + f"p {DB_HOST} {DB_PORT}")
            print(f"    {name:<4} → db:3306      {r.strip()}")
        except Exception as e:
            print(f"    {name:<4} SSH 실패: {type(e).__name__}")

    if net_only:
        return 0

    print("\n[4] WAS 서비스 상태")
    try:
        r = sh(SERVERS["WAS"],
               "systemctl is-active ai-craver; "
               "systemctl is-enabled ai-craver; "
               "curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:3000/health; echo; "
               "journalctl -u ai-craver --since '-30min' --no-pager 2>/dev/null "
               "| grep -ciE 'access denied'")
        v = [x.strip() for x in r.strip().splitlines()]
        while len(v) < 4:
            v.append("?")
        print(f"    서비스 active   : {v[0]}")
        print(f"    부팅 자동시작   : {v[1]}")
        print(f"    /health         : HTTP {v[2]}")
        print(f"    DB 인증오류(30분): {v[3]}건")
    except Exception as e:
        print(f"    확인 실패: {type(e).__name__}: {e}")

    if not DB_PW:
        print("\n[5] DB 검증 — CRAVER_DB_PW 미설정으로 건너뜀")
        return 0

    print("\n[5] DB (WAS 경유 터널)")
    try:
        import pymysql
    except ImportError:
        print("    pymysql 없음 — 건너뜀")
        return 0

    cl = paramiko.SSHClient()
    cl.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cl.connect(SERVERS["WAS"], username=USER, password=SSH_PW, timeout=25)
    tr = cl.get_transport()
    lp = 13500

    def fwd(sock):
        # 터널 종료 시 소켓이 먼저 닫혀 예외가 나는 것은 정상이므로 조용히 끝낸다
        ch = None
        try:
            ch = tr.open_channel("direct-tcpip", (DB_HOST, DB_PORT), sock.getpeername())
            while True:
                r, _, _ = select.select([sock, ch], [], [], 20)
                if sock in r:
                    d = sock.recv(65536)
                    if not d:
                        break
                    ch.sendall(d)
                if ch in r:
                    d = ch.recv(65536)
                    if not d:
                        break
                    sock.sendall(d)
        except Exception:
            pass
        finally:
            for s in (ch, sock):
                try:
                    if s:
                        s.close()
                except Exception:
                    pass

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", lp))
    srv.listen(5)
    threading.Thread(
        target=lambda: [threading.Thread(target=fwd, args=(srv.accept()[0],), daemon=True).start()
                        for _ in iter(int, 1)], daemon=True).start()
    try:
        cn = pymysql.connect(host="127.0.0.1", port=lp, user="ai", password=DB_PW,
                             database="ai", connect_timeout=15)
        cur = cn.cursor()
        cur.execute("SELECT VERSION()")
        print(f"    접속          : OK  ({cur.fetchone()[0]})")
        cur.execute("SHOW COLLATION LIKE 'utf8mb4_uca1400%'")
        n = len(cur.fetchall())
        print(f"    uca1400 지원  : {n}종 " + ("(덤프 변환 불필요)" if n else "(★변환 필요)"))
        cur.execute("SELECT @@character_set_server, @@collation_server")
        print(f"    문자셋        : {cur.fetchone()}")
        cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='ai'")
        print(f"    테이블 수     : {cur.fetchone()[0]}개")
        cn.close()
    except Exception as e:
        print(f"    접속 실패     : {type(e).__name__}: {str(e)[:80]}")
    finally:
        cl.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
