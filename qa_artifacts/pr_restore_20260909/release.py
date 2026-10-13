"""Prepare and publish only the reviewed PR integration, then ingest and verify it."""
import argparse
import base64
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
TASK = Path(__file__).resolve().parent
SOURCES = ['app/core/pr_issues.py', 'app/agents/orchestrator.py',
           'app/agents/qdrant_agent.py', 'app/main.py',
           'app/core/self_check.py', 'app/core/data_freshness.py', 'app/core/safety.py',
           'data/golden_set.json']
TESTS = ['tests/test_pr_issues.py', 'tests/test_pr_routing.py', 'tests/test_pr_monitoring.py']
sys.path.insert(0, str(ROOT / 'qa_artifacts'))
from feedback_release_20260908 import remote, restart_service


def digest(blob):
    return hashlib.sha256(blob).hexdigest() if blob is not None else None


def save(name, value):
    (TASK / name).write_text(json.dumps(value, ensure_ascii=True, indent=2), encoding='utf-8')


def read(name):
    return json.loads((TASK / name).read_text(encoding='utf-8'))


def prepare():
    paths = SOURCES + TESTS
    snapshot = remote('import base64,json\nfrom pathlib import Path\npaths=' + repr(paths)
        + "\nprint(json.dumps({'files':{p:base64.b64encode(Path(p).read_bytes()).decode('ascii') "
          "if Path(p).exists() else None for p in paths}}))")
    known = read('probe.json')['hashes']
    previous_plan = read('plan.json') if (TASK / 'plan.json').exists() else {}
    plan, changes = {}, []
    for name in paths:
        original = snapshot['files'][name]
        before = base64.b64decode(original) if original is not None else None
        after = (ROOT / name).read_bytes()
        if name.endswith('.py'):
            compile(after, name, 'exec')
        elif name.endswith('.json'):
            json.loads(after)
        if name in known:
            assert digest(before) in (known[name], digest(after), previous_plan.get(name, {}).get('after')), f'Production changed: {name}'
        elif name in TESTS:
            assert before is None or digest(before) in (digest(after), previous_plan.get(name, {}).get('after')), f'Unexpected existing test: {name}'
        else:
            baseline = subprocess.check_output(['git', 'show', f'HEAD:{name}'], cwd=ROOT)
            assert before is not None and (before.replace(b'\r\n', b'\n') == baseline.replace(b'\r\n', b'\n')
                    or before == after), f'Unreviewed production difference: {name}'
        if before is not None:
            target = TASK / 'baseline' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(before)
        target = TASK / 'candidate' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(after)
        plan[name] = {'before': digest(before), 'after': digest(after)}
        changes += difflib.unified_diff((before or b'').decode('utf-8').splitlines(True),
                after.decode('utf-8').splitlines(True),
                fromfile='production/' + name, tofile='candidate/' + name)
    save('plan.json', plan)
    (TASK / 'release.diff').write_text(''.join(changes), encoding='utf-8')
    print(json.dumps({'prepared': list(plan), 'changed': [p for p, s in plan.items()
                                if s['before'] != s['after']]}))


def publish():
    plan = read('plan.json')
    assert read('full_suite_result.json')['exit_code'] == 0
    assert read('focused_result.json')['exit_code'] == 0
    files = {}
    for name, meta in plan.items():
        blob = (TASK / 'candidate' / name).read_bytes()
        assert digest(blob) == meta['after'] == digest((ROOT / name).read_bytes()), name
        files[name] = base64.b64encode(blob).decode('ascii')
    packed = base64.b64encode(json.dumps({'plan': plan, 'files': files}).encode()).decode('ascii')
    result = remote('''
import base64, hashlib, json, os, shutil, time
from pathlib import Path
data=json.loads(base64.b64decode(''' + repr(packed) + '''))
root=Path.cwd()
assert str(root)=='/home/jeffrey/AI_Agent'
allowed=''' + repr(SOURCES + TESTS) + '''
assert set(data['plan'])==set(allowed)
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
for name,meta in data['plan'].items():
    assert sha(root/name) in (meta['before'],meta['after']), 'Production changed: '+name
    blob=base64.b64decode(data['files'][name])
    assert hashlib.sha256(blob).hexdigest()==meta['after']
    if name.endswith('.py'):
        compile(blob,name,'exec')
    elif name.endswith('.json'):
        json.loads(blob)
release=root/'.release_backups'/time.strftime('pr-list-%Y%m%d-%H%M%S')
release.mkdir(parents=True,exist_ok=False)
(release/'plan.json').write_text(json.dumps(data['plan'],indent=2))
changed=[]
for name,meta in data['plan'].items():
    target=root/name
    if sha(target)==meta['after']:
        continue
    if target.exists():
        backup=release/name
        backup.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(target,backup)
        assert sha(backup)==meta['before']
    target.parent.mkdir(parents=True,exist_ok=True)
    temp=target.with_name(target.name+'.pr-release.tmp')
    temp.write_bytes(base64.b64decode(data['files'][name]))
    assert sha(target)==meta['before'], 'Concurrent edit: '+name
    os.replace(temp,target)
    changed.append(name)
assert all(sha(root/p)==meta['after'] for p,meta in data['plan'].items())
print(json.dumps({'published':True,'backup':str(release),'changed':changed,'hashes_match':True}))
''')
    save('published.json', result)
    print(json.dumps(result))


def ingest():
    assert read('published.json')['published']
    result = remote('''
import json
from app.core.pr_issues import sync_pr_issues, status
from app.core.self_check import track_job
with track_job('pr_issues_sync_daily') as job:
    result=sync_pr_issues()
    assert not result.get('empty') and not result.get('cleanup_refused') and result.get('ok') is not False, result
    job.set_note(str(result)[:400])
print(json.dumps({'sync':result,'status':status()},default=str,ensure_ascii=True))
''')
    save('ingested.json', result)
    print(json.dumps(result, ensure_ascii=True))


def activate():
    assert read('ingested.json')['status']['count'] > 0
    restart_service()
    result = remote('''
import json,time,subprocess,requests
session=requests.Session()
session.trust_env=False
deadline=time.monotonic()+40
status=None
while time.monotonic()<deadline:
    try:
        status=session.get('http://127.0.0.1:3000/health',timeout=3).status_code
        if status==200:
            break
    except requests.RequestException:
        pass
    time.sleep(2)
active=subprocess.check_output(['systemctl','is-active','ai-craver'],text=True).strip()
print(json.dumps({'health':status,'service':active}))
''')
    save('activated.json', result)
    print(json.dumps(result))
    assert result['health'] == 200 and result['service'] == 'active'


def verify():
    plan = read('plan.json')
    result = remote('''
import ast, asyncio, hashlib, json, requests, subprocess
from pathlib import Path
from app.agents.orchestrator import OrchestratorAgent
from app.agents import qdrant_agent
from app.core import pr_issues, self_check
from app.api.routes import list_datasources
from app.core.golden_runner import _make_token
from app.config import get_settings
from qdrant_client.models import FieldCondition, Filter, MatchValue

session=requests.Session()
session.trust_env=False
response=session.get('http://127.0.0.1:3000/api/datasources',timeout=10)
result={'datasources_http':response.status_code}
result['pr_sources']=[r for r in asyncio.run(list_datasources()) if r['key']=='PR']
scheduler=session.get('http://127.0.0.1:3000/api/scheduler/status',timeout=10)
result['scheduler_http']=scheduler.status_code
result['pr_jobs']=[]
tree=ast.parse(Path('app/main.py').read_text(encoding='utf-8'))
for node in ast.walk(tree):
    if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='add_job':
        values={k.arg:ast.literal_eval(k.value) for k in node.keywords if isinstance(k.value,ast.Constant)}
        if values.get('id')=='pr_issues_sync_daily':
            result['pr_jobs'].append(values)
result['scheduler_enabled']=not bool(get_settings().migrated_redirect_url)
journal=subprocess.run(['journalctl','-u','ai-craver','--since','10 minutes ago','--no-pager'],
    capture_output=True,text=True,check=True).stdout
result['pr_job_start_logs']=[line for line in journal.splitlines() if '_pr_issues_sync_job' in line][-3:]
result['status']=pr_issues.status()
result['index_count']=qdrant_agent.index_team_counts(refresh=True).get('PR',0)
try:
    result['source_filter_count']=qdrant_agent._get_client().count(
        collection_name=qdrant_agent.COLLECTION, exact=True,
        count_filter=Filter(must=[FieldCondition(key=k,match=MatchValue(value=v))
            for k,v in [('team','PR'),('source_kind','pr_issues'),
                        ('source_sheet_id',pr_issues.SHEET_ID)]])).count
except Exception as error:
    result['source_filter_error']=str(error)
result['checks']={}
for check in self_check.CHECKS:
    if check.id in ('pr_vectors_present','pr_sheet_freshness','pr_unknown_team','pr_month_unresolved'):
        reading=check.fn()
        result['checks'][check.id]={'ok':reading.ok,'detail':reading.detail}
queries=['PR 이슈에서 브라질 Sephora 입점 내용 알려줘',
         '@@PR Music Bank World Tour Barcelona 2026 내용 알려줘']
token=_make_token('admin')
result['answers']={}
for query in queries:
    response=session.post('http://127.0.0.1:3000/v1/chat/completions',
        json={'model':'claude','stream':False,'messages':[{'role':'user','content':query}]},
        cookies={'token':token},timeout=120)
    response.raise_for_status()
    body=response.json()
    answer=(body.get('choices') or [{}])[0].get('message',{}).get('content','')
    result['answers'][query]={'http':response.status_code,'source':body.get('route'),'answer':answer}
result['hashes']={p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in ''' + repr(list(plan)) + '''}
print(json.dumps(result,ensure_ascii=True,default=str))
''')
    save('verified.json', result)
    assert result['datasources_http'] == 401 and len(result['pr_sources']) == 1
    assert result['pr_sources'][0]['group'] == '브랜드 성과'
    assert result['scheduler_http'] == 401 and len(result['pr_jobs']) == 1
    assert result['scheduler_enabled'] and result['pr_jobs'][0]['hour'] == 4 and result['pr_jobs'][0]['minute'] == 50
    assert result['status']['count'] > 0 and result['status']['count'] == result['index_count']
    assert result.get('source_filter_count') == result['index_count'], result.get('source_filter_error')
    assert result['checks']['pr_vectors_present']['ok']
    assert result['checks']['pr_sheet_freshness']['ok']
    for name, meta in plan.items():
        assert result['hashes'][name] == meta['after'], name
    for question, answer in result['answers'].items():
        body = answer['answer'] if isinstance(answer, dict) else str(answer)
        assert '1MLuwW74Nvv33YW6cVG5qW4uJLGuj3miUqcky9r20A8c' in body, question
        assert '작성월' in body, question
        assert any(word in body for word in ('브라질','Barcelona','바르셀로나','Sephora')), question
    print(json.dumps({'verified':True,'count':result['index_count'],
        'pr_sources':result['pr_sources'],'checks':result['checks'],
        'pr_jobs':result['pr_jobs'],'answer_queries':list(result['answers'])},ensure_ascii=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare','publish','ingest','activate','verify'])
    args = parser.parse_args()
    globals()[args.action]()
