"""Inspect production FX dates using a read-only transaction."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from feedback_release_20260908 import remote

QUERY = r'''
import hashlib, json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import pymysql
from app.config import get_settings
from app.core import fx_rates

s = get_settings()
assert s.mariadb_host == '10.1.200.5'
conn = pymysql.connect(host=s.mariadb_host, port=int(s.mariadb_port),
    user=s.mariadb_user, password=s.mariadb_password, database=s.mariadb_database,
    cursorclass=pymysql.cursors.DictCursor, connect_timeout=10, read_timeout=15)
result = {}
try:
    with conn.cursor() as cur:
        cur.execute('SET TRANSACTION READ ONLY')
        conn.begin()
        cur.execute('SELECT for_date, COUNT(*) currencies, MIN(source) source, '
                    'MAX(fetched_at) last_fetched FROM fx_rates GROUP BY for_date ORDER BY for_date')
        result['dates'] = cur.fetchall()
        def one(sql, params):
            cur.execute(sql, params)
            return cur.fetchone()
        def all_rows(sql, params):
            cur.execute(sql, params)
            return cur.fetchall()
        fx_rates.ensure_tables = lambda: None
        fx_rates.fetch_one = one
        fx_rates.fetch_all = all_rows
        today = datetime.now(ZoneInfo('Asia/Seoul')).date()
        result['today'] = str(today)
        result['latest'] = fx_rates.latest(today)
finally:
    conn.rollback()
    conn.close()
result['hashes'] = {p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
                    for p in ['app/core/fx_rates.py', 'scripts/fx_relay.py']}
print(json.dumps(result, default=str, ensure_ascii=True))
'''

if __name__ == '__main__':
    result = remote(QUERY)
    (Path(__file__).parent / 'probe_latest.json').write_text(
        json.dumps(result, indent=2, ensure_ascii=True), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True))
