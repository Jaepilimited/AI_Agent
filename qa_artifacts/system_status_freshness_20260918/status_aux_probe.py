"""Read-only stored self-check evidence and source coverage metadata."""
import json
from pathlib import Path
import sys
sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'qa_artifacts'))
import feedback_release_20260908 as access
code = '''
import json, datetime as dt
from app.db.mariadb import fetch_all, fetch_one
result={'queried_at_kst':dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat()}
result['source_selfchecks']=fetch_all("SELECT check_id,ok,detail,created_at FROM self_check_results WHERE run_id=(SELECT MAX(id) FROM self_check_runs) AND check_id IN ('data_freshness','freshness_coverage','cs_cache','pr_sheet_freshness','awards_sheet_freshness','static_stale_copies','static_notion_dates')")
result['value_cache']=fetch_all('SELECT name, n, updated_at FROM bq_value_cache ORDER BY name')
result['last_notion_success']=fetch_one("SELECT job_id,started_at,finished_at,ok,detail FROM job_runs WHERE job_id='qdrant_pipeline_daily' AND ok=1 ORDER BY id DESC LIMIT 1")
result['source_files']={}
from pathlib import Path
import hashlib
for name in ['app/core/safety.py','app/frontend/chat.js','app/agents/orchestrator.py','app/core/cs_dashboard.py']:
 p=Path(name)
 result['source_files'][name]={'sha256':hashlib.sha256(p.read_bytes().replace(b'\\r\\n',b'\\n')).hexdigest()}
print(json.dumps(result,default=str,ensure_ascii=True))
'''
value=access.remote(code)
target=Path(__file__).with_name('status_aux.json')
assert not target.exists()
target.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(value,ensure_ascii=False))
