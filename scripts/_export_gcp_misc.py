"""GCP user_groups / sql_cache / audit_logs(5/5 이후) 내보내기 (임시, 서버에서 실행)"""
import json, datetime
from dotenv import dotenv_values
import pymysql

def _ser(o):
    if isinstance(o, (datetime.datetime, datetime.date)):
        return o.isoformat(sep=" ")
    raise TypeError

cfg = dotenv_values('.env')
conn = pymysql.connect(host='127.0.0.1', user=cfg['MARIADB_USER'], password=cfg['MARIADB_PASSWORD'],
                       database='skin1004_ai', charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)
cur = conn.cursor()

out = {}
cur.execute("SELECT * FROM user_groups")
out["user_groups"] = cur.fetchall()
cur.execute("SELECT * FROM access_groups")
out["access_groups"] = cur.fetchall()
cur.execute("SELECT * FROM sql_cache")
out["sql_cache"] = cur.fetchall()
cur.execute("SELECT * FROM audit_logs WHERE created_at >= '2026-05-05' ORDER BY id")
out["audit_logs"] = cur.fetchall()
# ad_users: user_groups FK 검증용 (id, username만)
cur.execute("SELECT id, username FROM ad_users")
out["ad_users_ref"] = cur.fetchall()

with open("/tmp/gcp_misc_export.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, default=_ser)
print({k: len(v) for k, v in out.items()})
