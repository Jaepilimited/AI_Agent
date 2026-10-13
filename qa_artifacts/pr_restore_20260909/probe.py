"""Inspect the approved PR list tab and deployed PR registration, without writes."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
TASK = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'qa_artifacts'))
from feedback_release_20260908 import remote

QUERY = r'''
import ast, hashlib, json
from pathlib import Path
from app.core.awards import _sheets_service
from app.agents.qdrant_agent import _get_client, COLLECTION
from qdrant_client.models import Filter, FieldCondition, MatchValue

sheet_id = '1MLuwW74Nvv33YW6cVG5qW4uJLGuj3miUqcky9r20A8c'
result = {}
paths = ['app/core/pr_issues.py', 'data/pr_vectors.json',
         'app/agents/orchestrator.py', 'app/agents/qdrant_agent.py',
         'app/main.py', 'app/core/self_check.py']
result['hashes'] = {p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
                    if Path(p).exists() else None for p in paths}
tree = ast.parse(Path('app/agents/orchestrator.py').read_text(encoding='utf-8'))
for node in ast.walk(tree):
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name)
            and t.id == '_DB_REGISTRY' for t in node.targets):
        entries = ast.literal_eval(node.value)
        result['pr_sources'] = [e for e in entries if str(e.get('key', '')).lower() == 'pr']
try:
    rows = _sheets_service().spreadsheets().values().get(
        spreadsheetId=sheet_id, range="'리스트'!A:Z").execute().get('values', [])
    result['sheet'] = {'ok': True, 'tab': '리스트', 'rows': rows}
except Exception as exc:
    result['sheet'] = {'ok': False, 'error': str(exc)}
try:
    filt = Filter(must=[FieldCondition(key='team', match=MatchValue(value='PR'))])
    result['pr_cloud_count'] = _get_client().count(
        collection_name=COLLECTION, count_filter=filt, exact=True).count
except Exception as exc:
    result['cloud_error'] = str(exc)
print(json.dumps(result, ensure_ascii=True))
'''

if __name__ == '__main__':
    result = remote(QUERY)
    (TASK / 'probe.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    report = {key: value for key, value in result.items() if key != 'sheet'}
    report['sheet'] = {key: value for key, value in result['sheet'].items() if key != 'rows'}
    rows = result['sheet'].get('rows', [])
    report['sheet']['row_count'] = len(rows)
    report['sheet']['header_preview'] = rows[:6]
    print(json.dumps(report, ensure_ascii=True))
