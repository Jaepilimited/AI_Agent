"""Read-only Sheets freshness audit; never call any sync or refresh function."""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'qa_artifacts'))
from feedback_release_20260908 import remote

BASE = r'''
import datetime as dt
import hashlib
import json
from collections import Counter
from pathlib import Path
import pymysql
from app.config import get_settings

settings = get_settings()
assert settings.mariadb_host == '10.1.200.5'
conn = pymysql.connect(host=settings.mariadb_host, port=int(settings.mariadb_port),
    user=settings.mariadb_user, password=settings.mariadb_password,
    database=settings.mariadb_database, charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor, connect_timeout=10,
    read_timeout=15, write_timeout=10)
with conn.cursor() as cur:
    cur.execute('SET TRANSACTION READ ONLY')
conn.begin()
def query(sql, args=()):
    assert sql.lstrip().upper().startswith('SELECT ')
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()
def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str,
        separators=(',', ':')).encode('utf-8')).hexdigest()
def rows_hash(rows):
    return digest(sorted(digest(row) for row in rows))
def compare(source, stored):
    a, b = Counter(digest(row) for row in source), Counter(digest(row) for row in stored)
    return dict(source_rows=len(source), stored_rows=len(stored), matches=a == b,
        source_hash=rows_hash(source), stored_hash=rows_hash(stored),
        missing_from_copy=sum((a-b).values()), extra_in_copy=sum((b-a).values()))
result = {'queried_at_kst': dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat()}
'''

MODES = {}
MODES['stats'] = r'''
result['clock'] = query('SELECT NOW() db_now, UTC_TIMESTAMP() db_utc, @@session.time_zone tz')[0]
tables = ['op_inventory', 'op_inventory_expiry', 'awards_rankings', 'model_rights',
    'model_right_periods', 'product_ingredients', 'product_ingredient_map']
result['copies'] = {}
for table in tables:
    result['copies'][table] = query('SELECT COUNT(*) n, MIN(synced_at) oldest_sync, '
        'MAX(synced_at) newest_sync, COUNT(DISTINCT synced_at) sync_versions FROM ' + table)[0]
    if table.startswith('op_inventory'):
        result['copies'][table]['source_stamps'] = query('SELECT sheet_updated_at, COUNT(*) n FROM '
            + table + ' GROUP BY sheet_updated_at')
jobs = ['op_inventory_sync_daily', 'awards_sync_daily', 'model_rights_sync_daily',
    'ingredient_sync_daily', 'cs_cache_hourly', 'pr_issues_sync_daily']
result['jobs'] = {}
for job in jobs:
    result['jobs'][job] = {'latest': query('SELECT started_at, finished_at, ok, detail, duration_ms '
        'FROM job_runs WHERE job_id=%s ORDER BY started_at DESC LIMIT 3', (job,)),
        'last_success': query('SELECT started_at, finished_at, detail FROM job_runs '
            'WHERE job_id=%s AND ok=1 ORDER BY started_at DESC LIMIT 1', (job,)),
        'last_failure': query('SELECT started_at, finished_at, detail FROM job_runs '
            'WHERE job_id=%s AND ok=0 ORDER BY started_at DESC LIMIT 1', (job,)),
        'last_seven_days': query('SELECT ok, COUNT(*) n FROM job_runs '
            'WHERE job_id=%s AND started_at>=NOW()-INTERVAL 7 DAY GROUP BY ok', (job,))}
from app.core import pr_issues
result['pr_manifest'] = pr_issues.status()
files = ['app/core/inventory.py', 'app/core/awards.py', 'app/core/model_rights.py',
    'app/core/ingredients.py', 'app/core/pr_issues.py', 'app/agents/cs_agent.py', 'app/main.py']
result['source_hashes'] = {f: hashlib.sha256(Path(f).read_bytes().replace(b'\r\n',b'\n')).hexdigest()
    for f in files}
'''

MODES['inventory'] = r'''
from app.core import inventory as m
data = m._read_sheet()
# The sync upserts by (sku, location), with SK/HQ_NEW winning duplicate keys.
source = {}
for row in data['rows']:
    source[(row['sku'], row['location'])] = row
fields = ['sku', 'item_name', 'location', 'qty']
stored = query('SELECT ' + ','.join(fields) + ' FROM op_inventory')
result['inventory'] = compare([{k:r[k] for k in fields} for r in source.values()], stored)
result['inventory']['source_stamp'] = data['sheet_updated_at']
result['inventory']['tab_counts'] = data.get('counts')
expiry = m.read_expiry()
source = {}
for row in expiry['rows']:
    source[(row['sku'], row['expiry_date'], row['lot'])] = row
fields = ['sku', 'item_name', 'expiry_date', 'lot', 'qty']
stored = query('SELECT ' + ','.join(fields) + ' FROM op_inventory_expiry')
result['expiry'] = compare([{k:r[k] for k in fields} for r in source.values()], stored)
result['expiry']['source_stamp'] = expiry['sheet_updated_at']
'''

MODES['awards'] = r'''
from app.core import awards as m
rows = m.parse_rows(m._read_sheet())
fields = m._FIELDS
source = [{k:r[k] for k in fields} for r in rows]
stored = query('SELECT ' + ','.join(fields) + ' FROM awards_rankings')
result['awards'] = compare(source, stored)
'''

MODES['ingredients'] = r'''
from app.core import ingredients as m
rows = m._read_sheet()
fields = ['norm_key','name_en','name_kr','brand','line','size',
    'ingredients_kr','ingredients_en','active_kr','key_ingredients']
source = [{k:r[k] for k in fields} for r in rows]
stored = query('SELECT ' + ','.join(fields) + ' FROM product_ingredients')
result['ingredients'] = compare(source, stored)
'''

MODES['expiry_details'] = r'''
from app.core import inventory as m
source = {(r['sku'], r['expiry_date'], r['lot']):r for r in m.read_expiry()['rows']}
stored = {(r['sku'], r['expiry_date'], r['lot']):r for r in query(
    'SELECT sku,item_name,expiry_date,lot,qty FROM op_inventory_expiry')}
changes = []
for key in source.keys() & stored.keys():
    diffs = {k: {'source':source[key][k], 'stored':stored[key][k]} for k in stored[key]
        if source[key][k] != stored[key][k]}
    if diffs:
        changes.append({'key':key, 'diffs':diffs})
result['expiry_differences'] = {'source_only_keys': len(source.keys()-stored.keys()),
    'copy_only_keys': len(stored.keys()-source.keys()), 'changed_rows': len(changes),
    'changes': changes[:10],
    'source_only': [source[k] for k in sorted(source.keys()-stored.keys())][:10],
    'stored_only': [stored[k] for k in sorted(stored.keys()-source.keys())][:10]}
'''

MODES['expiry_normalized'] = r'''
from app.core import inventory as m
rows = m.read_expiry()['rows']
fields = ['sku','item_name','expiry_date','lot','qty']
source = {(r['sku'],r['expiry_date'],r['lot']):r for r in rows}
stored = query('SELECT '+','.join(fields)+' FROM op_inventory_expiry')
def normalized(row):
    return {k:(row[k].upper() if k == 'lot' else row[k]) for k in fields}
result['expiry_lot_case_normalized'] = compare([normalized(r) for r in source.values()],
    [normalized(r) for r in stored])
result['lot_storage'] = query('SELECT COLUMN_NAME,COLLATION_NAME FROM '
    'information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() '
    "AND TABLE_NAME='op_inventory_expiry' AND COLUMN_NAME='lot'")
'''

MODES['rights'] = r'''
import os
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from app.core import model_rights as m
creds = Credentials.from_service_account_file(os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
    or settings.google_application_credentials,
    scopes=['https://www.googleapis.com/auth/spreadsheets.readonly'])
service = build('sheets', 'v4', credentials=creds, cache_discovery=False)
meta = service.spreadsheets().get(spreadsheetId=m.SPREADSHEET_ID,
    fields='sheets(properties(title))').execute()
tabs = [s['properties']['title'] for s in meta['sheets']]
data = service.spreadsheets().values().batchGet(spreadsheetId=m.SPREADSHEET_ID,
    ranges=["'"+tab+"'!A1:R1000" for tab in tabs]).execute()
models = []
for tab, values in zip(tabs, data['valueRanges']):
    models.extend(m.parse_tab(values.get('values', []), tab))
source_models, source_periods = [], []
for model in models:
    source_models.append(dict(model_name=model['name'][:100], sheet_tab=model['tab'][:100],
        product_line=model['line'][:100], online_ok=int(model['online']),
        offline_ok=int(model['offline']), media=model['media'][:300],
        shoot_date=model['shoot_date'][:30], agency=model['agency'],
        marked_unusable=int(model['marked_unusable'])))
    for p in model['periods']:
        source_periods.append(dict(model_name=model['name'][:100],sheet_tab=model['tab'][:100],
            extension_no=p['ext'],region=p['region'][:40],period_raw=p['raw'][:120],
            start_date=p['start'],end_date=p['end'],upload_expired=int(p['upload_expired']),
            fee=p['fee'][:60]))
for name, source in [('models', source_models), ('periods', source_periods)]:
    table = 'model_rights' if name == 'models' else 'model_right_periods'
    fields = list(source[0])
    stored = query('SELECT ' + ','.join(fields) + ' FROM ' + table)
    result[name] = compare(source, stored)
result['source_tabs'] = len(tabs)
'''

MODES['pr'] = r'''
from app.core import pr_issues as m
from app.agents import qdrant_agent as q
source = m.parse_rows(m._read_sheet())
manifest = m._load_manifest()
stored = [p['payload'] for p in manifest['points']]
result['source_vs_manifest'] = compare(source, stored)
client = q._get_client()
cloud = m._cloud_payloads(client, [p['id'] for p in manifest['points']])
result['manifest_vs_cloud'] = compare(stored, list(cloud.values()))
from qdrant_client.models import Filter, FieldCondition, MatchValue
flt = Filter(must=[FieldCondition(key=k,match=MatchValue(value=v)) for k,v in
    [('team','PR'),('source_kind',m._SOURCE_KIND),('source_sheet_id',m.SHEET_ID)]])
result['cloud_pr_total'] = client.count(collection_name=q.COLLECTION, count_filter=flt,
    exact=True).count
result['manifest_synced_at'] = manifest.get('synced_at')
result['pending_recovery'] = m._pending_path().exists()
'''

MODES['drive'] = r'''
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from app.core import inventory, awards, model_rights, ingredients, pr_issues
sources = {'BP': settings.cs_spreadsheet_id, 'OP':inventory.SHEET_ID,
    'awards':awards.SHEET_ID, 'rights':model_rights.SPREADSHEET_ID,
    'ingredients':ingredients.SPREADSHEET_ID, 'PR':pr_issues.SHEET_ID}
creds = Credentials.from_service_account_file(settings.google_application_credentials,
    scopes=['https://www.googleapis.com/auth/drive.readonly'])
drive = build('drive', 'v3', credentials=creds, cache_discovery=False)
result['sources'] = {}
for name, file_id in sources.items():
    try:
        meta = drive.files().get(fileId=file_id, fields='modifiedTime').execute()
        utc = dt.datetime.fromisoformat(meta['modifiedTime'].replace('Z','+00:00'))
        result['sources'][name] = {'modified_at_kst':utc.astimezone(
            dt.timezone(dt.timedelta(hours=9))).isoformat()}
    except Exception as exc:
        result['sources'][name] = {'error_type':type(exc).__name__,
            'http_status':getattr(getattr(exc,'resp',None),'status',None)}
'''

MODES['product_info_tree'] = r'''
import concurrent.futures
import threading
import time
import httpx
from app.core import product_info as m
lock = threading.Lock()
next_call = [0.0]
calls = [0]
class AuditClient(httpx.Client):
    def request(self, method, url, *args, **kwargs):
        assert method.upper() == 'GET' or (method.upper() == 'POST' and str(url).endswith('/query'))
        for attempt in range(3):
            with lock:
                pause = next_call[0] - time.monotonic()
                if pause > 0:
                    time.sleep(pause)
                next_call[0] = time.monotonic() + 0.38
                calls[0] += 1
            response = super().request(method, url, *args, **kwargs)
            if response.status_code == 429 and attempt < 2:
                time.sleep(min(float(response.headers.get('Retry-After', '1')), 2))
                continue
            if response.status_code != 200:
                raise RuntimeError('Notion API status ' + str(response.status_code))
            return response
stored = query('SELECT page_id,line,product,url,synced_at FROM product_info')
old = {r['page_id']:r for r in stored}
with AuditClient(timeout=12) as client:
    root_meta = client.get('https://api.notion.com/v1/pages/' + m.ROOT_PAGE_ID,
        headers=m._headers()).json()
    line_dbs = m._child_databases(client, m.ROOT_PAGE_ID)
    lines = [r for db_id,_ in line_dbs for r in m._db_rows(client, db_id)]
def discover(line):
    with AuditClient(timeout=12) as client:
        inner = m._child_databases(client, line['id'])
        rows = []
        for db_id,_ in inner:
            for p in m._db_rows(client, db_id):
                edited = dt.datetime.fromisoformat(p['last_edited_time'].replace('Z','+00:00'))
                rows.append({'page_id':p['id'].replace('-',''), 'line':m._title_of(line).strip()[:120],
                    'product':m._title_of(p).strip()[:300], 'url':p.get('url','')[:400],
                    'edited_kst':edited.astimezone(dt.timezone(dt.timedelta(hours=9)))})
        return rows
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    pages = [row for batch in pool.map(discover, lines) for row in batch]
live = {r['page_id']:r for r in pages}
changed_fields, source_newer, unknown = [], [], []
for key,row in live.items():
    if key not in old:
        unknown.append({'page_id':key,'modified_kst':row['edited_kst']})
        continue
    fields = [k for k in ['line','product','url'] if row[k] != old[key][k]]
    if fields:
        changed_fields.append({'page_id':key,'fields':fields})
    if row['edited_kst'].replace(tzinfo=None) > old[key]['synced_at']:
        source_newer.append({'page_id':key,'modified_kst':row['edited_kst'],
            'synced_at':old[key]['synced_at']})
result['product_info'] = {'origin':'Notion', 'root_page_id':m.ROOT_PAGE_ID,
    'root_last_edited_utc':root_meta.get('last_edited_time'), 'line_rows':len(lines),
    'source_product_pages':len(pages), 'stored_product_rows':len(stored),
    'stored_newest_sync':max((r['synced_at'] for r in stored),default=None),
    'latest_source_product_modified_kst':max((r['edited_kst'] for r in pages),default=None),
    'stored_missing_from_source':list(old.keys()-live.keys()),
    'source_pages_not_copied':unknown, 'metadata_differences':changed_fields,
    'source_pages_newer_than_copy':source_newer, 'api_requests':calls[0],
    'body_content_compared':False,
    'production_module_sha256':hashlib.sha256(Path('app/core/product_info.py').read_bytes()
        .replace(b'\r\n',b'\n')).hexdigest()}
'''

MODES['cs'] = r'''
import asyncio
from app.agents import cs_agent
rows = asyncio.run(cs_agent.load_all_sheets())
result['BP_source'] = {'normalized_rows':len(rows),'content_hash':rows_hash(rows)}
result['note'] = 'Read-only source load; does not inspect or refresh live process memory.'
'''

ENDING = r'''
conn.rollback()
conn.close()
print(json.dumps(result, default=str, ensure_ascii=True))
'''

if __name__ == '__main__':
    mode = sys.argv[1]
    assert mode in MODES
    code = BASE + MODES[mode] + ENDING
    assert code.isascii(), 'Remote helper stdin requires ASCII'
    out = remote(code)
    if mode == 'stats':
        import hashlib
        out['local_source_matches_remote'] = {
            f: hashlib.sha256((ROOT/f).read_bytes().replace(b'\r\n', b'\n')).hexdigest() == h
            for f, h in out['source_hashes'].items()}
    target = Path(__file__).with_name('sheets_' + mode + '.json')
    target.write_text(json.dumps(out, ensure_ascii=False, default=str, indent=2), encoding='utf-8')
    print(json.dumps(out, ensure_ascii=False, default=str))
