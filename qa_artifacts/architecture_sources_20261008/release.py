"""Scoped architecture release: snapshot, validate, publish six files, verify."""
import base64
import contextlib
import difflib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
import entra_access_count_20260908 as runner

FILES = ('app/flow/spec.py', 'app/flow/graph.py', 'app/core/static_checks.py',
         'app/frontend/chat.html', 'app/frontend/chat.js', 'app/static/style.css')

def digest(data):
    return hashlib.sha256(data).hexdigest()

def remote(code):
    runner.REMOTE_QUERY = code
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        rc = runner.main()
    if rc:
        raise RuntimeError(output.getvalue()[-4000:])
    for line in reversed(output.getvalue().splitlines()):
        if line.startswith('{'):
            return json.loads(line)
    raise RuntimeError('No structured server response')

def prepare():
    assert not (HERE / 'plan.json').exists(), 'Existing release snapshot must be retained'
    response = remote('import base64,json\nfrom pathlib import Path\npaths=' + repr(FILES) +
                      '\nprint(json.dumps({p:base64.b64encode(Path(p).read_bytes()).decode() for p in paths}))')
    manifest = {}
    report = []
    for name in FILES:
        before = base64.b64decode(response[name])
        after = (ROOT / name).read_bytes()
        for directory, data in [('before', before), ('candidate', after)]:
            dest = HERE / directory / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        manifest[name] = {'before': digest(before), 'after': digest(after)}
        head = subprocess.run(['git', 'show', 'HEAD:' + name], cwd=ROOT, capture_output=True, check=True).stdout
        print(name, 'production_matches_HEAD=', before.replace(b'\r\n', b'\n') == head.replace(b'\r\n', b'\n'))
        report.extend(difflib.unified_diff(before.decode('utf-8').splitlines(True), after.decode('utf-8').splitlines(True),
                                         fromfile='production/' + name, tofile='candidate/' + name))
    (HERE / 'release.diff').write_text(''.join(report), encoding='utf-8')
    (HERE / 'plan.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    previous = json.loads((HERE.parent / 'architecture_connections_20261008' / 'plan.json').read_text())
    assert all(manifest[name]['before'] == previous[name]['after'] for name in FILES), 'Production changed since last verified release; inspect diff'
    print('Snapshot ready: production matches previous verified release')

def refresh_candidate():
    assert not (HERE / 'stage.json').exists(), 'Staged releases must not change'
    plan = json.loads((HERE / 'plan.json').read_text())
    report = []
    for name in FILES:
        before = (HERE / 'before' / name).read_bytes()
        assert digest(before) == plan[name]['before']
        after = (ROOT / name).read_bytes()
        (HERE / 'candidate' / name).write_bytes(after)
        plan[name]['after'] = digest(after)
        report.extend(difflib.unified_diff(before.decode('utf-8').splitlines(True),
                                         after.decode('utf-8').splitlines(True),
                                         fromfile='production/' + name, tofile='candidate/' + name))
    (HERE / 'plan.json').write_text(json.dumps(plan, indent=2), encoding='utf-8')
    (HERE / 'release.diff').write_text(''.join(report), encoding='utf-8')
    print('Candidate refreshed; original production snapshot retained')

def payload():
    plan = json.loads((HERE / 'plan.json').read_text())
    data = {}
    for name in FILES:
        blob = (HERE / 'candidate' / name).read_bytes()
        assert digest(blob) == plan[name]['after']
        assert digest((ROOT / name).read_bytes()) == plan[name]['after'], 'Local candidate changed'
        data[name] = base64.b64encode(blob).decode()
    return plan, data

def stage():
    plan, data = payload()
    result = remote('''import base64,hashlib,importlib.util,json,sys,tempfile
from pathlib import Path
import app.flow, app.core
plan=PLAN
data=DATA
root=Path.cwd()
for name, item in plan.items():
    assert hashlib.sha256((root/name).read_bytes()).hexdigest()==item['before'], 'Production changed: '+name
stage=Path(tempfile.mkdtemp(prefix='architecture-20261008-', dir='/home/jeffrey'))
for name, encoded in data.items():
    p=stage/name
    p.parent.mkdir(parents=True, exist_ok=True)
    blob=base64.b64decode(encoded)
    p.write_bytes(blob)
    if name.endswith('.py'): compile(blob,str(p),'exec')
def load(name,rel):
    s=importlib.util.spec_from_file_location(name,stage/rel)
    m=importlib.util.module_from_spec(s)
    sys.modules[name]=m
    s.loader.exec_module(m)
    return m
app.flow.spec=load('app.flow.spec','app/flow/spec.py')
app.flow.graph=load('app.flow.graph','app/flow/graph.py')
g=app.flow.graph.build()
assert g.get('sources'), 'Source catalog missing'
assert all(s.get('label') and s.get('kind') and 'tables' in s for s in g['sources'])
for n in app.flow.spec.all_nodes():
    if n.fn: app.flow.graph.resolve(n.fn)
checks=load('architecture_static_checks','app/core/static_checks.py')
checks.ROOT=str(root)
ok,message=checks.flow_spec_matches_code()
assert ok,message
edges={(e['src'],e['dst']) for e in g['edges']}
assert ('intercept.fi_guard','response') in edges
assert ('router.source_gate','router.llm') in edges
print(json.dumps({'stage':str(stage),'nodes':len(g['nodes']),'edges':len(g['edges']),'flow_check':message}))
'''.replace('PLAN', repr(plan)).replace('DATA', repr(data)))
    (HERE / 'stage.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))

def publish():
    plan, _ = payload()
    staged = json.loads((HERE / 'stage.json').read_text())['stage']
    result = remote('''import hashlib,json,os,shutil,datetime
from pathlib import Path
plan=PLAN
stage=Path(STAGE)
root=Path.cwd()
for name,item in plan.items():
    assert hashlib.sha256((root/name).read_bytes()).hexdigest()==item['before'], 'Production changed: '+name
    assert hashlib.sha256((stage/name).read_bytes()).hexdigest()==item['after'], 'Stage changed: '+name
backup=Path('/home/jeffrey/architecture-backups')/datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
backup.mkdir(parents=True,exist_ok=False)
for name in plan:
    dest=backup/name
    dest.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(root/name,dest)
replaced=[]
try:
    for name in plan:
        target=root/name
        tmp=target.with_name(target.name+'.architecture-new')
        shutil.copy2(stage/name,tmp)
        os.chmod(tmp,target.stat().st_mode)
        os.replace(tmp,target)
        replaced.append(name)
except Exception:
    for name in replaced: shutil.copy2(backup/name,root/name)
    raise
print(json.dumps({'backup':str(backup),'published':list(plan)}))
'''.replace('PLAN', repr(plan)).replace('STAGE', repr(staged)))
    (HERE / 'published.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))
    restart()

def ssh_client(host):
    password = os.environ.get('CRAVER_SSH_PW', '')
    if not password:
        for line in (ROOT / 'scripts/_run_fx_relay.cmd').read_text(encoding='ascii').splitlines():
            if line.lower().startswith('set craver_ssh_pw='):
                password=line.partition('=')[2]
                break
    assert password
    client=runner.paramiko.SSHClient()
    client.load_host_keys(str(Path.home()/'.ssh/known_hosts'))
    client.connect(host,username='jeffrey',password=password,allow_agent=False,look_for_keys=False,timeout=10)
    return client,password

def restart():
    client,password=ssh_client('10.1.150.5')
    try:
        stdin,out,err=client.exec_command("sudo -S -p '' -- systemctl restart ai-craver",timeout=60)
        stdin.write(password+'\n')
        stdin.flush()
        stdin.channel.shutdown_write()
        rc=out.channel.recv_exit_status()
        assert rc==0,err.read().decode().replace(password,'[redacted]')
        print('Service restarted')
    finally:
        client.close()

def verify():
    plan,_=payload()
    result=remote('''import hashlib,json,subprocess,urllib.request
from pathlib import Path
plan=PLAN
hashes={name:hashlib.sha256(Path(name).read_bytes()).hexdigest()==item['after'] for name,item in plan.items()}
active=subprocess.check_output(['systemctl','is-active','ai-craver'],text=True).strip()
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
health={}
for address in ['http://127.0.0.1:3000/health']:
    with opener.open(address,timeout=20) as r: health[address]=r.status
assets={}
for path, marker in [('frontend/chat.js','function renderFlowAll'),('static/style.css','.flow-all-node')]:
    with opener.open('http://127.0.0.1:3000/'+path,timeout=20) as r:
        assets[path]=marker in r.read().decode()
from app.flow.graph import build
g=build()
assert g.get('sources'), 'Source catalog missing'
assert all(hashes.values()) and active=='active' and all(assets.values())
assert any(n['id']=='router.source_gate' for n in g['nodes'])
print(json.dumps({'hashes':hashes,'service':active,'health':health,'served_assets':assets,'nodes':len(g['nodes']),'edges':len(g['edges']),'sources':len(g['sources'])}))
'''.replace('PLAN',repr(plan)))
    client,password=ssh_client('10.1.100.5')
    try:
        stdin,stdout,stderr=client.exec_command('python3 -',timeout=30)
        stdin.write('''import json,urllib.request
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
result={}
for path,marker in [('health',None),('frontend/chat.js','function renderFlowAll'),('static/style.css','.flow-all-node')]:
    request=urllib.request.Request('http://127.0.0.1/'+path,headers={'Host':'ai.cravercorp.internal'})
    with opener.open(request,timeout=10) as response:
        result[path]={'status':response.status,'marker':marker is None or marker in response.read().decode()}
assert all(v['status']==200 and v['marker'] for v in result.values())
print(json.dumps(result))
''')
        stdin.flush()
        stdin.channel.shutdown_write()
        output=stdout.read().decode()
        error=stderr.read().decode()
        assert stdout.channel.recv_exit_status()==0,error.replace(password,'[redacted]')
        result['web_nginx_to_was']=json.loads(output)
        result['local_web_access']='Unavailable from DB_PC network; verified via Web server loopback with production Host header'
    finally:
        client.close()
    (HERE/'verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))

if __name__=='__main__':
    {'prepare':prepare,'refresh':refresh_candidate,'stage':stage,'publish':publish,'verify':verify,'restart':restart}[sys.argv[1]]()
