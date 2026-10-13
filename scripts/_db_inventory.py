"""MariaDB 테이블 인벤토리: 행 수 + 최신 갱신 시각 (로컬/GCP 공용, 임시)"""
import sys, io, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass
import pymysql

try:
    from app.config import get_settings
    s = get_settings()
    conn = pymysql.connect(host=s.mariadb_host, port=int(s.mariadb_port), user=s.mariadb_user,
                           password=s.mariadb_password, database=s.mariadb_database, charset='utf8mb4',
                           cursorclass=pymysql.cursors.DictCursor)
except Exception:
    from dotenv import dotenv_values
    cfg = dotenv_values('.env')
    conn = pymysql.connect(host='127.0.0.1', user=cfg['MARIADB_USER'], password=cfg['MARIADB_PASSWORD'],
                           database='skin1004_ai', charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)

cur = conn.cursor()
cur.execute("SHOW TABLES")
tables = [list(r.values())[0] for r in cur.fetchall()]
out = {}
for t in tables:
    cur.execute(f"SELECT COUNT(*) c FROM `{t}`")
    cnt = cur.fetchone()["c"]
    ts = None
    cur.execute(f"SHOW COLUMNS FROM `{t}`")
    cols = [r["Field"] for r in cur.fetchall()]
    for tc in ("updated_at", "created_at", "synced_at", "last_used_at", "last_login"):
        if tc in cols:
            cur.execute(f"SELECT MAX(`{tc}`) m FROM `{t}`")
            m = cur.fetchone()["m"]
            ts = str(m) if m else None
            break
    out[t] = {"rows": cnt, "latest": ts}
print(json.dumps(out, ensure_ascii=False, indent=1))
