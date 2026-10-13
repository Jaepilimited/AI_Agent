import concurrent.futures
import datetime as dt
import json
from pathlib import Path
import sys
import urllib.parse
import urllib.request
import urllib.error

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'qa_artifacts'))
BASE = 'http://34.64.99.254:8061'
OUT = Path(__file__).parent

PROBE = r'''
import concurrent.futures
import datetime as dt
import json
import urllib.parse
import urllib.request
import urllib.error
BASE = 'http://34.64.99.254:8061'
def check(region):
    endpoint = '/api/dashboard/summary' if region == 'domestic' else '/api/overseas/dashboard/summary'
    query = urllib.parse.urlencode({'startDate':'2000-01-01','endDate':'2026-09-18','scope':'all','channel':'all','claimType':'all','status':'all'})
    result = {'region':region, 'checked_at':dt.datetime.now(dt.timezone.utc).isoformat(), 'url':BASE + endpoint}
    try:
        with urllib.request.urlopen(BASE + endpoint + '?' + query, timeout=15) as r:
            result['http_status'] = r.status
            payload = json.load(r)
        result['keys'] = sorted(payload)
        result['source'] = payload.get('source')
        result['period'] = payload.get('period')
        result['total'] = payload.get('total')
        result['metrics'] = payload.get('metrics')
        result['timestamp_fields'] = {k:v for k,v in payload.items() if any(t in k.lower() for t in ('update','sync','fetch','cache','latest','refresh','timestamp')) and isinstance(v,(str,int,float,type(None)))}
        daily = payload.get('charts',{}).get('dailyTrend',[])
        active = [p for p in daily if isinstance(p,dict) and p.get('total',0)>0]
        result['nonzero_daily_first'] = active[0] if active else None
        result['nonzero_daily_last'] = active[-1] if active else None
        result['row_count'] = len(payload.get('rows',[]))
        result['row_field_names'] = sorted(payload['rows'][0]) if payload.get('rows') else []
    except urllib.error.HTTPError as e:
        result['http_status'] = e.code
        result['error'] = str(e)
        result['proxy_error'] = e.headers.get('X-Squid-Error')
    except Exception as e:
        result['error'] = type(e).__name__ + ': ' + str(e)
    return result
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    results = list(pool.map(check, ['domestic','overseas']))
print(json.dumps({'checked_at':dt.datetime.now(dt.timezone.utc).isoformat(), 'results':results}, ensure_ascii=False, default=str))
'''

if sys.argv[1] == 'local':
    scope = {}
    import contextlib, io
    capture = io.StringIO()
    with contextlib.redirect_stdout(capture):
        exec(PROBE, scope)
    result = json.loads(capture.getvalue())
else:
    import feedback_release_20260908 as access
    result = access.remote(PROBE)
result['location'] = sys.argv[1]
target = OUT / ('cs_' + sys.argv[1] + '.json')
target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False, indent=2))
