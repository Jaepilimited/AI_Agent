import datetime as dt
import json
from pathlib import Path
import sys
import urllib.parse
import urllib.request
sys.stdout.reconfigure(encoding='utf-8')
out = []
for region, endpoint in [('domestic','/api/dashboard/summary'),('overseas','/api/overseas/dashboard/summary')]:
    params = {'startDate':'2026-09-17','endDate':'2026-09-18','scope':'all','channel':'all','claimType':'all','status':'all'}
    url = 'http://34.64.99.254:8061' + endpoint + '?' + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=15) as response:
        data = json.load(response)
    out.append({'region':region,'url':url,'source':data.get('source'),'period':data.get('period'),
                'lastUpdated':data.get('lastUpdated'),'total':data.get('total'),
                'dailyTrend':data.get('charts',{}).get('dailyTrend')})
result = {'checked_at':dt.datetime.now(dt.timezone.utc).isoformat(),'results':out}
Path(__file__).with_name('cs_recent_days.json').write_text(json.dumps(result, ensure_ascii=False, indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False,indent=2))
