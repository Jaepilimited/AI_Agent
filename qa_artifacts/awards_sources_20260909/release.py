"""Publish and verify the award source-link change against its reviewed snapshot."""
import argparse
import base64
import difflib
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
TASK = Path(__file__).resolve().parent
SOURCE = 'app/core/awards.py'
sys.path.insert(0, str(ROOT / 'qa_artifacts'))
from feedback_release_20260908 import remote, restart_service


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def save(name, data):
    (TASK / name).write_text(json.dumps(data, indent=2, ensure_ascii=True), encoding='utf-8')


def read(name):
    return json.loads((TASK / name).read_text(encoding='utf-8'))


def prepare():
    snapshot = remote("import base64,json\nfrom pathlib import Path\np=Path(" + repr(SOURCE)
                      + ")\nprint(json.dumps({'file':base64.b64encode(p.read_bytes()).decode('ascii')}))")
    before = base64.b64decode(snapshot['file'])
    assert before == (TASK / 'baseline' / SOURCE).read_bytes(), 'Live awards source changed'
    after = (ROOT / SOURCE).read_bytes()
    compile(after, SOURCE, 'exec')
    target = TASK / 'candidate' / SOURCE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(after)
    plan = {'source': SOURCE, 'before': sha(before), 'after': sha(after)}
    save('plan.json', plan)
    (TASK / 'release.diff').write_text(''.join(difflib.unified_diff(
        before.decode('utf-8').splitlines(True), after.decode('utf-8').splitlines(True),
        fromfile='production/' + SOURCE, tofile='candidate/' + SOURCE)), encoding='utf-8')
    print(json.dumps({'prepared': SOURCE, 'before': plan['before'], 'after': plan['after']}))


def publish():
    plan = read('plan.json')
    assert read('full_suite_result.json')['exit_code'] == 0
    blob = (TASK / 'candidate' / SOURCE).read_bytes()
    assert sha(blob) == plan['after'] == sha((ROOT / SOURCE).read_bytes())
    payload = base64.b64encode(json.dumps({'plan': plan,
        'file': base64.b64encode(blob).decode('ascii')}).encode()).decode('ascii')
    result = remote("""
import base64, hashlib, json, os, shutil, time
from pathlib import Path
data = json.loads(base64.b64decode(""" + repr(payload) + """))
plan = data['plan']
assert plan['source'] == 'app/core/awards.py'
root = Path.cwd()
assert str(root) == '/home/jeffrey/AI_Agent'
target = root / plan['source']
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
assert digest(target) in (plan['before'], plan['after']), 'Production source changed'
already_present = digest(target) == plan['after']
release = root / '.release_backups' / time.strftime('awards-sources-%Y%m%d-%H%M%S')
release.mkdir(parents=True, exist_ok=True)
blob = base64.b64decode(data['file'])
compile(blob, plan['source'], 'exec')
assert hashlib.sha256(blob).hexdigest() == plan['after']
if not already_present:
    backup = release / 'awards.py'
    shutil.copy2(target, backup)
    assert digest(backup) == plan['before']
    temp = target.with_name('awards.py.source-links.tmp')
    temp.write_bytes(blob)
    assert digest(target) == plan['before'], 'Production source changed before replacement'
    os.replace(temp, target)
assert digest(target) == plan['after']
print(json.dumps({'published': True, 'already_present': already_present,
                  'source': plan['source'], 'hashes_match': True, 'backup': str(release)}))
""")
    save('published.json', result)
    restart_service()
    readiness = remote("""
import json, time
import requests
session = requests.Session()
session.trust_env = False
deadline, status = time.monotonic() + 40, None
while time.monotonic() < deadline:
    try:
        status = session.get('http://127.0.0.1:3000/health', timeout=3).status_code
        if status == 200:
            break
    except requests.RequestException:
        pass
    time.sleep(2)
print(json.dumps({'health': status}))
""")
    result.update(readiness)
    save('published.json', result)
    print(json.dumps(result))
    assert result['health'] == 200


def verify():
    result = remote("""
import hashlib, json, subprocess
from pathlib import Path
from app.core import awards
cases = {}
for name, query in [('awards', '2026년 수상'), ('rankings', '쇼피'), ('no_match', '2026년 6월')]:
    found = awards.search(query)
    answer = awards.format_answer(found)
    cases[name] = {'total': found['total'], 'answer': answer}
cases['not_loaded'] = {'answer': awards.format_answer({'rows': [], 'table_empty': True})}
cases['usage'] = {'answer': awards.usage_permission_answer()}
link = '[수상·랭킹 원본 시트](' + awards.SHEET_URL + ')'
summary = {name:{'link_count':item['answer'].count(link), 'total':item.get('total')}
           for name, item in cases.items()}
service = subprocess.run(['systemctl','is-active','ai-craver'], capture_output=True,
                         text=True, timeout=10).stdout.strip()
print(json.dumps({'source_sha':hashlib.sha256(Path('app/core/awards.py').read_bytes()).hexdigest(),
                  'service':service, 'link':link, 'cases':summary, 'answers':cases}, ensure_ascii=True))
""")
    save('verification.json', result)
    assert result['source_sha'] == read('plan.json')['after']
    assert result['service'] == 'active'
    assert all(case['link_count'] == 1 for case in result['cases'].values())
    assert result['cases']['awards']['total'] > 0
    assert result['cases']['no_match']['total'] == 0
    print(json.dumps({key:value for key,value in result.items() if key != 'answers'}, ensure_ascii=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare','publish','verify'])
    globals()[parser.parse_args().action]()
