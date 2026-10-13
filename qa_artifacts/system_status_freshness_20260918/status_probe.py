"""Read-only production metadata audit. Never invokes sync/startup or notification jobs."""
import json
from pathlib import Path
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "qa_artifacts"))
import feedback_release_20260908 as access

COMMON = '''
import ast, json, datetime as dt
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from app.config import get_settings
settings = get_settings()
kst = dt.timezone(dt.timedelta(hours=9))
result = {"queried_at_kst": dt.datetime.now(kst).isoformat()}
'''

SCRIPTS = {
"inventory": '''
tree = ast.parse(Path('app/agents/orchestrator.py').read_text())
for node in ast.walk(tree):
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '_DB_REGISTRY' for t in node.targets):
        result['registry'] = ast.literal_eval(node.value)
from app.agents.sql_agent import _source_table_map
result['bq_sources'] = _source_table_map(settings)
from app.core.safety import _MONITORED_TABLES
result['monitored_tables'] = _MONITORED_TABLES
from app.db.mariadb import fetch_all, fetch_one
result['db_time'] = fetch_one('SELECT NOW() as server_now, UTC_TIMESTAMP() as utc_now, @@session.time_zone as session_timezone')
result['latest_jobs'] = fetch_all('SELECT j.job_id,j.started_at,j.finished_at,j.ok,j.detail,j.duration_ms FROM job_runs j JOIN (SELECT job_id,MAX(id) id FROM job_runs GROUP BY job_id) x ON j.id=x.id ORDER BY j.job_id')
result['last_selfcheck'] = fetch_one('SELECT * FROM self_check_runs ORDER BY id DESC LIMIT 1')
if result['last_selfcheck']:
    result['selfcheck_failures'] = fetch_all('SELECT check_id,category,severity,ok,detail,repaired,repair_note,created_at FROM self_check_results WHERE run_id=%s AND (ok=0 OR repaired=1)', (result['last_selfcheck']['id'],))
from app.core import mailer
result['mail'] = {'enabled':mailer.is_enabled(), 'status':mailer.status()}
from app.core.google_auth import GoogleAuthManager
result['gws_token_file_count'] = len(list(GoogleAuthManager().token_dir.glob('*.json')))
from app.reports import blocks
result['report_block_count'] = len(blocks.BLOCKS)
import requests
def read_health(url):
    try:
        response=requests.get(url,timeout=10)
        return {'status':response.status_code,'body':response.json()}
    except Exception as e:
        return {'error_type':type(e).__name__, 'error':str(e)[:250]}
result['health'] = {u:read_health(u) for u in ['http://127.0.0.1:3000/health','http://127.0.0.1:3000/health/ready','http://10.1.100.5/health/ready']}
''',
"bq_metadata": '''
from app.agents.sql_agent import _source_table_map
from app.core.bigquery import get_bigquery_client
client = get_bigquery_client().client
sources = _source_table_map(settings)
result['sources'] = sources
paths = sorted(set(p for items in sources.values() for p in items))
def read_table(path):
    try:
        t = client.get_table(path,timeout=20)
        return {'path':path,'type':t.table_type,'rows':t.num_rows,'bytes':t.num_bytes,'created':t.created,'modified':t.modified,'partition_field':getattr(t.time_partitioning,'field',None),'schema':[{'name':s.name,'type':s.field_type,'mode':s.mode} for s in t.schema],'external_source_format':getattr(t.external_data_configuration,'source_format',None)}
    except Exception as e:
        return {'path':path, 'error_type':type(e).__name__,'error':str(e)[:350]}
with ThreadPoolExecutor(max_workers=6) as executor:
    result['tables'] = list(executor.map(read_table,paths))
'''
}

mode = sys.argv[1]
value = access.remote(COMMON + SCRIPTS[mode] + '\nprint(json.dumps(result,default=str,ensure_ascii=True))\n')
target = Path(__file__).with_name('status_' + mode + '.json')
assert not target.exists(), str(target)
target.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'artifact':str(target),'result':value},ensure_ascii=False))
