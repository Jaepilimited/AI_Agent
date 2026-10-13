"""DB-HUB에서 마케팅 관련 팀(Craver, [GM]EAST, [GM]WEST, B2B1, B2B2) 구조 전체 출력."""
import httpx, os, sys, json
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT / '.env')
TOKEN = os.getenv('NOTION_MCP_TOKEN', '')
VER = '2022-06-28'
H = {'Authorization': f'Bearer {TOKEN}', 'Notion-Version': VER}
DB_HUB_ID = '2e12b4283b008011ae32e39bf73b7f7b'

MARKETING_TEAMS = ('Craver', '[GM]EAST', '[GM]WEST', 'B2B1', 'B2B2')

def gc(bid):
    r = httpx.get(f'https://api.notion.com/v1/blocks/{bid}/children?page_size=100', headers=H, timeout=20)
    return r.json().get('results', []) if r.status_code == 200 else []

def rich(body, key='rich_text'):
    return ''.join(x.get('plain_text', '') for x in body.get(key, []))

def show_block(b, indent=0):
    btype = b.get('type', '')
    bid = b.get('id', '')
    body = b.get(btype, {}) if isinstance(b.get(btype), dict) else {}
    text = rich(body)
    title = rich(body, 'title')
    label = text or title
    prefix = '  ' * indent
    print(f'{prefix}[{btype}] {bid[:20]} | {label[:70]}')

    # 멘션 출력
    for seg in body.get('rich_text', []):
        mn = seg.get('mention', {})
        if mn:
            mtype = mn.get('type', '')
            mid = (mn.get(mtype) or {}).get('id', '')
            print(f'{prefix}  → mention({mtype}): {mid}')

    return bid, btype

hub = gc(DB_HUB_ID)
for b in hub:
    if b.get('type') != 'toggle':
        continue
    toggle_body = b.get('toggle', {})
    team = rich(toggle_body)
    if '학습 현황' in team:
        continue
    if not any(t in team for t in MARKETING_TEAMS):
        continue

    print(f'\n{"="*60}')
    print(f'TEAM: {team}')
    print('='*60)
    children = gc(b['id'])
    for c in children:
        show_block(c, 1)
        # 한 단계 더 확인
        cbody = c.get(c.get('type',''), {})
        if isinstance(cbody, dict) and c.get('has_children') or c.get(c.get('type',''), {}).get('rich_text'):
            for k in gc(c['id']):
                show_block(k, 2)
