"""Review and publish only the FX comparison fix, with file and data backups."""
from __future__ import annotations

import argparse
import base64
import difflib
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
TASK = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'qa_artifacts'))
from feedback_release_20260908 import remote, restart_service

SOURCES = ['app/core/fx_rates.py', 'app/api/jandi_briefing_api.py', 'scripts/fx_relay.py']
TESTS = ['tests/test_fx_monthly_basis.py', 'tests/test_fx_relay_monthly.py']


def sha(blob):
    return hashlib.sha256(blob).hexdigest() if blob is not None else None


def save(name, value):
    (TASK / name).write_text(json.dumps(value, indent=2, ensure_ascii=True), encoding='utf-8')


def read(name):
    return json.loads((TASK / name).read_text(encoding='utf-8'))


def prepare():
    names = SOURCES + TESTS
    snapshot = remote("""
import base64, json
from pathlib import Path
from app.db.mariadb import fetch_one
from app.core.fx_rates import month_before
names = """ + repr(names) + """
latest = fetch_one('SELECT MAX(for_date) d FROM fx_rates')['d']
print(json.dumps({'latest_day': str(latest), 'target': str(month_before(latest)), 'files': {
    p: base64.b64encode(Path(p).read_bytes()).decode('ascii') if Path(p).exists() else None
    for p in names}}))
""")
    plan, review = {}, []
    for name in names:
        raw = snapshot['files'][name]
        original = base64.b64decode(raw) if raw is not None else None
        baseline_path = TASK / 'baseline' / name
        if baseline_path.exists() and (name in SOURCES or original is not None):
            baseline = baseline_path.read_bytes()
            assert original is not None and baseline.replace(b'\r\n', b'\n') == original.replace(b'\r\n', b'\n'), 'Live source changed: ' + name
        else:
            assert original is None, 'New test already exists on server: ' + name
        candidate = (ROOT / name).read_bytes()
        compile(candidate, name, 'exec')
        destination = TASK / 'candidate' / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(candidate)
        plan[name] = {'before': sha(original), 'after': sha(candidate)}
        review.extend(difflib.unified_diff(
            (original or b'').decode('utf-8').splitlines(keepends=True),
            candidate.decode('utf-8').splitlines(keepends=True),
            fromfile='live/' + name, tofile='candidate/' + name))
    target = snapshot['target']
    spec = importlib.util.spec_from_file_location('fx_release_relay', ROOT / 'scripts/fx_relay.py')
    relay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(relay)
    history = relay.fetch_history(target)
    assert history, 'Comparison source is unavailable'
    rows, source, quoted = history
    assert {r['currency'] for r in rows} == set(relay.CURRENCIES)
    payload = {'for_date': quoted, 'source': source, 'rates': rows, 'only_missing': True}
    save('history.json', payload)
    save('plan.json', {'files': plan, 'history_sha': sha((TASK / 'history.json').read_bytes()),
                       'latest_day': snapshot['latest_day'], 'target': target})
    (TASK / 'review.diff').write_text(''.join(review), encoding='utf-8')
    print(json.dumps({'prepared': names, 'current_quote': snapshot['latest_day'],
                      'monthly_target': target, 'actual_history_quote': quoted,
                      'history_rows': len(rows)}))


def publish():
    plan = read('plan.json')
    validation = read('validation.json')
    assert validation['files'] == plan['files'] and validation['focused_exit'] == 0
    assert validation['suite_reviewed'], 'Full-suite failures must be reviewed before publication'
    files = {}
    for name, hashes in plan['files'].items():
        blob = (TASK / 'candidate' / name).read_bytes()
        assert sha(blob) == hashes['after'] == sha((ROOT / name).read_bytes()), name
        files[name] = base64.b64encode(blob).decode('ascii')
    encoded = base64.b64encode(json.dumps({'plan': plan['files'], 'files': files}).encode()).decode('ascii')
    result = remote("""
import base64, hashlib, json, os, shutil, time
from pathlib import Path
data = json.loads(base64.b64decode(""" + repr(encoded) + """))
root = Path.cwd()
release = root / '.fx-releases' / time.strftime('monthly-basis-%Y%m%d-%H%M%S')
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
already_present = []
for name, hashes in data['plan'].items():
    actual = digest(root / name)
    assert actual in (hashes['before'], hashes['after']), 'Live file changed: ' + name
    if actual == hashes['after']:
        already_present.append(name)
    blob = base64.b64decode(data['files'][name])
    compile(blob, name, 'exec')
    stage = release / 'candidate' / name
    stage.parent.mkdir(parents=True, exist_ok=True)
    stage.write_bytes(blob)
    assert digest(stage) == hashes['after'], name
for name, hashes in data['plan'].items():
    assert digest(root / name) in (hashes['before'], hashes['after']), 'Live file changed during staging: ' + name
for name in data['files']:
    target = root / name
    if digest(target) == data['plan'][name]['after']:
        continue
    assert digest(target) == data['plan'][name]['before'], 'Live file changed before replacement: ' + name
    if target.exists():
        backup = release / 'backup' / name
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + '.fx-monthly.tmp')
    shutil.copy2(release / 'candidate' / name, temp)
    os.replace(temp, target)
assert all(digest(root / n) == h['after'] for n, h in data['plan'].items())
print(json.dumps({'published': True, 'release': str(release), 'hashes_match': True,
                  'already_present': already_present}))
""")
    save('publish_result.json', result)
    restart_service()
    result['restarted'] = True
    save('publish_result.json', result)
    readiness = remote("""
import json, time
import requests
session = requests.Session()
session.trust_env = False
deadline = time.monotonic() + 40
status = None
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
    result['health'] = readiness['health']
    save('publish_result.json', result)
    print(json.dumps(result))
    assert result['health'] == 200, 'Service did not become ready after restart'


def backfill():
    plan = read('plan.json')
    assert sha((TASK / 'history.json').read_bytes()) == plan['history_sha']
    payload = read('history.json')
    encoded = base64.b64encode(json.dumps(payload).encode()).decode('ascii')
    result = remote("""
import base64, json
from pathlib import Path
import requests
from app.config import get_settings
from app.db.mariadb import fetch_all
payload = json.loads(base64.b64decode(""" + repr(encoded) + """))
release = Path(""" + repr(read('publish_result.json')['release']) + """
)
rows = fetch_all('SELECT * FROM fx_rates WHERE for_date=%s', (payload['for_date'],))
(release / 'fx_before_backfill.json').write_text(json.dumps(rows, default=str), encoding='utf-8')
session = requests.Session()
session.trust_env = False
response = session.post('http://127.0.0.1:3000/api/internal/fx', json=payload,
    headers={'X-Relay-Token': get_settings().briefing_relay_token}, timeout=15)
print(json.dumps({'http_status': response.status_code, 'response': response.json(),
                  'existing_rows_preserved': len(rows)}))
""")
    save('backfill_result.json', result)
    print(json.dumps(result))
    assert result['http_status'] == 200
    assert result['response']['history_needed_for'] == ''


def verify():
    result = remote("""
import json, subprocess
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
from app.core import fx_rates
from app.core.work_briefing import _fx_lines
fx = fx_rates.latest(datetime.now(ZoneInfo('Asia/Seoul')).date())
session = requests.Session()
session.trust_env = False
health = session.get('http://127.0.0.1:3000/health', timeout=10)
active = subprocess.run(['systemctl', 'is-active', 'ai-craver'], capture_output=True,
                         text=True, timeout=10)
print(json.dumps({'health': health.status_code, 'service': active.stdout.strip(),
                  'fx': fx, 'rendered_heading': next(line for line in _fx_lines(fx) if line)},
                 ensure_ascii=True))
""")
    save('verify_result.json', result)
    print(json.dumps(result, ensure_ascii=True))
    assert result['health'] == 200 and result['service'] == 'active'
    assert result['fx']['basis_date'] == read('history.json')['for_date']
    assert len(result['fx']['items']) == 9
    comparison = {row['currency']: row for row in read('history.json')['rates']}
    for item in result['fx']['items']:
        assert item['was_krw'] == comparison[item['currency']]['krw'], item['currency']
        assert item['unit'] == comparison[item['currency']]['unit'], item['currency']
        assert item['change_pct'] is not None, item['currency']


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'publish', 'backfill', 'verify'])
    parser.add_argument('--label', action='store_true', help='Publish the monthly comparison wording follow-up')
    arguments = parser.parse_args()
    if arguments.label:
        assert arguments.action != 'backfill', 'The label follow-up does not modify FX data'
        TASK = TASK / 'label'
        SOURCES = ['app/core/fx_rates.py']
        TESTS = ['tests/test_fx_monthly_basis.py', 'tests/test_work_briefing.py']
    globals()[arguments.action]()
    if arguments.label and arguments.action == 'verify':
        result = read('verify_result.json')
        expected = '한 달 전 대비 (' + result['fx']['basis_date'] + ' 고시)'
        assert result['fx']['basis_note'] == expected
        assert expected in result['rendered_heading']
