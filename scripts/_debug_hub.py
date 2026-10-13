import httpx, os, sys, json
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT / '.env')
TOKEN = os.getenv('NOTION_MCP_TOKEN', '')
VER = '2022-06-28'
H = {'Authorization': f'Bearer {TOKEN}', 'Notion-Version': VER}

def gc(bid):
    r = httpx.get(f'https://api.notion.com/v1/blocks/{bid}/children?page_size=100', headers=H, timeout=20)
    return r.json().get('results', []) if r.status_code == 200 else []

EAST_TOGGLE_FOUND = None
hub = gc('2e12b4283b008011ae32e39bf73b7f7b')
for b in hub:
    if b.get('type') != 'toggle':
        continue
    rt = b['toggle'].get('rich_text', [])
    team = ''.join(x.get('plain_text', '') for x in rt)
    if '[GM]EAST' not in team:
        continue
    print('TEAM:', team)
    for c in gc(b['id']):
        ctype = c.get('type', '')
        cid = c.get('id', '')
        print(f'  {ctype}: {cid}')
        body = c.get(ctype, {})
        if isinstance(body, dict):
            for seg in body.get('rich_text', []):
                mn = seg.get('mention', {})
                pt = seg.get('plain_text', '')
                if mn:
                    print('    mention:', json.dumps(mn))
                elif pt:
                    print('    text:', pt)
            if ctype == 'child_database':
                print('    db_title:', body.get('title', ''))
        # recurse into children
        kids = gc(cid)
        for k in kids:
            ktype = k.get('type', '')
            kid = k.get('id', '')
            kbody = k.get(ktype, {})
            print(f'    child {ktype}: {kid}')
            if isinstance(kbody, dict):
                for seg in kbody.get('rich_text', []):
                    mn = seg.get('mention', {})
                    pt = seg.get('plain_text', '')
                    if mn:
                        print('      mention:', json.dumps(mn))
                    elif pt:
                        print('      text:', pt)
                if ktype == 'child_database':
                    print('      db_title:', kbody.get('title', ''))
