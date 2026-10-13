"""Read-only audit. Persist metadata and aggregate counts only."""
from __future__ import annotations
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'qa_artifacts'))
from feedback_release_20260908 import remote

COMMON = r'''
import sys
sys.dont_write_bytecode = True
import collections, datetime as dt, json, os, re
from pathlib import Path
now = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
def emit(value):
    print(json.dumps(value, default=str, ensure_ascii=True))
'''

JOBS = COMMON + r'''
import pymysql
from app.config import get_settings
s = get_settings()
assert s.mariadb_host == '10.1.200.5'
c = pymysql.connect(host=s.mariadb_host, port=int(s.mariadb_port), user=s.mariadb_user,
    password=s.mariadb_password, database=s.mariadb_database, charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor, connect_timeout=10, read_timeout=15)
result = {'queried_at_kst': now, 'server_local_time': dt.datetime.now().isoformat()}
try:
    with c.cursor() as cur:
        cur.execute('SET TRANSACTION READ ONLY')
        c.begin()
        cur.execute("SELECT NOW() AS db_now, UTC_TIMESTAMP() AS db_utc")
        result['db_clock'] = cur.fetchone()
        cur.execute("SELECT job_id, started_at, finished_at, ok, detail, duration_ms FROM job_runs "
                    "WHERE job_id IN ('qdrant_pipeline_daily','team_sync_daily','public_notion_sync_daily') "
                    "ORDER BY started_at DESC LIMIT 32")
        result['jobs'] = cur.fetchall()
        cur.execute("SELECT DISTINCT job_id FROM job_runs WHERE job_id LIKE '%notion%' OR job_id LIKE '%qdrant%'")
        result['related_job_ids'] = cur.fetchall()
        cur.execute("SELECT team, resource_type, COUNT(*) AS n, MIN(synced_at) AS min_synced_at, "
                    "MAX(synced_at) AS max_synced_at FROM team_resources GROUP BY team, resource_type")
        result['team_resources'] = cur.fetchall()
        cur.execute("SELECT payload, updated_at FROM bq_value_cache WHERE name='NotionUnshared'")
        row = cur.fetchone()
        if row:
            value = json.loads(row['payload'])
            result['unshared'] = {'at':value.get('at'), 'updated_at':row['updated_at'],
                'by_team':dict(collections.Counter(p.get('team') for p in value.get('pages',[]))),
                'pages':[{k:p.get(k) for k in ('page_id','team','kind')} for p in value.get('pages',[])]}
finally:
    c.rollback()
    c.close()
emit(result)
'''

SNAPSHOT = COMMON + r'''
from qdrant_client import QdrantClient
from app.agents.qdrant_agent import _qdrant_url, _qdrant_api_key, _normalize_id
keys = ['team','source','source_kind','source_type','page_id','page_url','last_edited_time',
        'content_sha256','doc_sha256','document_sha256','indexed_at','ingested_at','synced_at',
        'updated_at','scraped_at','fetched_at','chunk_index','source_collection']
client = QdrantClient(url=_qdrant_url(),api_key=_qdrant_api_key(),timeout=12)
points=[]
offset=None
while True:
    batch,offset=client.scroll('Craver',offset=offset,limit=500,with_payload=keys,with_vectors=False)
    points.extend({'id':str(p.id),'payload':p.payload} for p in batch)
    if offset is None:
        break
client.close()
local_path=Path('data/notion_vectors_gemini.json')
local=[]
if local_path.exists():
    for p in json.loads(local_path.read_text(encoding='utf-8')):
        local.append({'id':str(_normalize_id(p.get('id'),p.get('payload',{}))),'payload':{k:p.get('payload',{}).get(k) for k in keys if k in p.get('payload',{})}})
emit({'queried_at_kst':now,'cloud_count':len(points),'cloud_points':points,
    'local_mtime_kst':dt.datetime.fromtimestamp(local_path.stat().st_mtime,now.tzinfo) if local_path.exists() else None,
    'local_count':len(local),'local_points':local})
'''

SOURCES = COMMON + r'''
import concurrent.futures, threading, time
import httpx
targets = TARGETS_PLACEHOLDER
token = os.getenv('NOTION_MCP_TOKEN','')
assert token, 'Notion credential unavailable'
client = httpx.Client(timeout=7,headers={'Authorization':'Bearer '+token,'Notion-Version':'2022-06-28'})
lock = threading.Lock()
next_time = [time.monotonic()]
def request(path):
    with lock:
        wait = max(0, next_time[0]-time.monotonic())
        next_time[0] = max(next_time[0],time.monotonic())+.37
    if wait:
        time.sleep(wait)
    return client.get('https://api.notion.com/v1/'+path)
def read(target):
    pid = target['page_id']
    out = {'page_id':pid,'teams':target['teams']}
    try:
        paths=['blocks/'+pid] if target.get('block') else ['pages/'+pid,'databases/'+pid]
        for path in paths:
            r=request(path)
            out['http_status']=r.status_code
            out['endpoint']=path.split('/')[0]
            if r.status_code in (400,404):
                continue
            if r.status_code==200:
                v=r.json()
                out.update({k:v.get(k) for k in ('object','last_edited_time','created_time','archived','in_trash')})
            break
    except Exception as e:
        out['error_type']=type(e).__name__
    return out
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    results=list(pool.map(read,targets))
client.close()
emit({'queried_at_kst':now,'requested':len(targets),'results':results})
'''

RUNTIME = COMMON + r'''
import subprocess
p=subprocess.run(['systemctl','show','ai-craver','--property=ActiveState,SubState,ActiveEnterTimestamp,ExecMainStartTimestamp,NRestarts,MainPID'],capture_output=True,text=True,timeout=10)
result={'queried_at_kst':now,'systemd':p.stdout.strip().splitlines(),'systemd_exit':p.returncode}
p=subprocess.run(['journalctl','-u','ai-craver','--since','2026-09-18 04:59:00','--until','2026-09-18 06:00:00','--output=json','--no-pager'],capture_output=True,text=True,timeout=15)
matches=[]
markers=['qdrant_pipeline_done','qdrant_pipeline_failed','qdrant_cloud_upserted','notion_vectors_reloaded',
    'team_link_cards_built','notion_unshared_recorded','Starting','Started','Stopping','Stopped',
    'Deactivated','application_shutdown','Application shutdown','Shutting down','Main process exited',
    'Killed','killed','upsert','동기화 완료','로컬 JSON 저장','링크 카드 반영 완료','404스킵','ERROR']
for line in p.stdout.splitlines():
    try:
        row=json.loads(line)
    except Exception:
        continue
    msg=row.get('MESSAGE','')
    found=[m for m in markers if m in msg]
    if found:
        matches.append({'at_kst':dt.datetime.fromtimestamp(int(row['__REALTIME_TIMESTAMP'])/1e6,now.tzinfo),
                        'markers':found,'pid':row.get('_PID')})
result['journal_exit']=p.returncode
result['journal_events']=matches[-150:]
emit(result)
'''

CARDS = COMMON + r'''
import pymysql
from app.config import get_settings
from app.core.team_link_index import build_link_cards, notion_page_id
s=get_settings()
c=pymysql.connect(host=s.mariadb_host,port=int(s.mariadb_port),user=s.mariadb_user,
    password=s.mariadb_password,database=s.mariadb_database,charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor,connect_timeout=10,read_timeout=15)
try:
    with c.cursor() as cur:
        cur.execute('SET TRANSACTION READ ONLY')
        c.begin()
        cur.execute("SELECT id,parent_id,team,node_type,name,resource_type,url,COALESCE(description,'') AS description,synced_at FROM team_resources ORDER BY depth,sort_order")
        rows=cur.fetchall()
finally:
    c.rollback()
    c.close()
indexed=set()
for p in json.loads(Path('data/notion_vectors_gemini.json').read_text(encoding='utf-8')):
    p=p.get('payload',{})
    if p.get('source')=='team_resources':
        continue
    indexed.add(str(p.get('page_id','')).replace('-','').lower())
    nid=notion_page_id(str(p.get('page_url','')))
    if nid:
        indexed.add(nid)
cards=build_link_cards(rows,indexed_page_ids=indexed)
emit({'queried_at_kst':now,'database_rows':len(rows),'cards':[
    {'id':str(p['id']),**{k:p['payload'].get(k) for k in ('team','page_id','page_url','content_sha256','last_edited_time')}}
    for p in cards]})
'''

HUB = COMMON + r'''
import hashlib, httpx
from scripts.notion_qdrant_pipeline import DB_HUB_ID, notion_headers, fetch_page_text, chunk_text
result={'queried_at_kst':now,'teams':[]}
with httpx.Client(timeout=10) as c:
    r=c.get('https://api.notion.com/v1/blocks/'+DB_HUB_ID+'/children?page_size=100',headers=notion_headers())
    result['hub_status']=r.status_code
    if r.status_code==200:
        v=r.json()
        result['hub_has_more']=v.get('has_more')
        for b in v.get('results',[]):
            if b.get('type')!='toggle':
                continue
            label=''.join(p.get('plain_text','') for p in b['toggle'].get('rich_text',[]))
            if label not in ('B2B1','Craver'):
                continue
            item={'team':label,'block_id':b['id'],'last_edited_time':b.get('last_edited_time'),'has_children':b.get('has_children')}
            r=c.get('https://api.notion.com/v1/blocks/'+b['id']+'/children?page_size=100',headers=notion_headers())
            item['children_http_status']=r.status_code
            if r.status_code==200:
                children=r.json().get('results',[])
                item['child_types']=dict(collections.Counter(p.get('type') for p in children))
                item['child_text_chars']=sum(len(''.join(t.get('plain_text','') for t in p.get(p.get('type',''),{}).get('rich_text',[]))) for p in children)
            if label=='Craver':
                content=fetch_page_text(b['id'],c)
                item['current_chunk_hashes']=[hashlib.sha256(x.encode()).hexdigest() for x in chunk_text(content)]
                item['current_text_chars']=len(content)
            result['teams'].append(item)
emit(result)
'''

def main():
    action = sys.argv[1]
    if action == 'sources':
        import collections, re
        snapshot = json.loads(Path(__file__).with_name('notion_snapshot.json').read_text(encoding='utf-8'))
        targets = {}
        for point in snapshot['cloud_points'] + snapshot['local_points']:
            p = point['payload']
            if p.get('source') in ('team_resources','google_sheets') or p.get('source_kind')=='pr_issues':
                continue
            if p.get('team') in ('IT', '임베딩 된 구글시트', None):
                continue
            pid = str(p.get('page_id','')).replace('-','')
            if not re.fullmatch('[a-fA-F0-9]{32}',pid):
                matches = re.findall('[a-fA-F0-9]{32}',str(p.get('page_url','')).replace('-',''))
                if not matches or pid.startswith('inline:'):
                    continue
                pid=matches[-1]
            v = targets.setdefault(pid,{'page_id':pid,'teams':set(),'block':False})
            v['teams'].add(p.get('team'))
            if p.get('last_edited_time')=='block':
                v['block']=True
        jobs = json.loads(Path(__file__).with_name('notion_jobs.json').read_text(encoding='utf-8'))
        for p in jobs['unshared']['pages']:
            if p['team']=='IT':
                continue
            v=targets.setdefault(p['page_id'],{'page_id':p['page_id'],'teams':set(),'block':False})
            v['teams'].add(p['team'])
        targets = [{**v,'teams':sorted(v['teams'])} for _,v in sorted(targets.items())]
        index=int(sys.argv[2])
        batch=targets[index*28:(index+1)*28]
        code=SOURCES.replace('TARGETS_PLACEHOLDER',repr(batch))
        action+='_'+str(index)
    else:
        code = {'jobs': JOBS, 'snapshot': SNAPSHOT, 'runtime': RUNTIME, 'cards': CARDS, 'hub': HUB}[action]
    result = remote(code)
    path = Path(__file__).with_name('notion_' + action + '.json')
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    if action == 'snapshot':
        points = result.pop('cloud_points')
        result.pop('local_points')
        from collections import Counter
        result['cloud_teams'] = dict(Counter(p['payload'].get('team') for p in points))
        result['cloud_sources'] = dict(Counter(p['payload'].get('source') for p in points))
    if action.startswith('sources_'):
        from collections import Counter
        results=result.pop('results')
        result['http_statuses']=dict(Counter(str(v.get('http_status',v.get('error_type'))) for v in results))
    if action == 'cards':
        from collections import Counter
        cards=result.pop('cards')
        result['by_team']=dict(Counter(c['team'] for c in cards))
    print(json.dumps(result,ensure_ascii=True,default=str))

if __name__ == '__main__':
    main()
