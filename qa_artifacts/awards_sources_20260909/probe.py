"""Inspect stored award source links without changing the award records."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from feedback_release_20260908 import remote

QUERY = r'''
import hashlib, json
from pathlib import Path
import pymysql
from app.config import get_settings

s = get_settings()
assert s.mariadb_host == '10.1.200.5'
conn = pymysql.connect(host=s.mariadb_host, port=int(s.mariadb_port), user=s.mariadb_user,
    password=s.mariadb_password, database=s.mariadb_database,
    cursorclass=pymysql.cursors.DictCursor, connect_timeout=10, read_timeout=15)
result = {}
try:
    with conn.cursor() as cur:
        cur.execute('SET TRANSACTION READ ONLY')
        conn.begin()
        cur.execute("SELECT COUNT(*) total, SUM(TRIM(source_url)<>'') with_source, "
                    "SUM(source_url REGEXP '^https?://') with_url FROM awards_rankings")
        result['counts'] = cur.fetchone()
        cur.execute("SELECT source_url, COUNT(*) n FROM awards_rankings "
                    "GROUP BY source_url ORDER BY n DESC LIMIT 20")
        result['source_values'] = cur.fetchall()
finally:
    conn.rollback()
    conn.close()
result['hashes'] = {p:hashlib.sha256(Path(p).read_bytes()).hexdigest() if Path(p).exists() else None
                    for p in ['app/core/awards.py','tests/test_awards.py']}
print(json.dumps(result, default=str, ensure_ascii=True))
'''

if __name__ == '__main__':
    result = remote(QUERY)
    (Path(__file__).parent / 'probe.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True))
