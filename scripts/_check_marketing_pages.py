"""마케팅 관련 미접근 페이지 접근성 확인."""
import httpx, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT / '.env')
TOKEN = os.getenv('NOTION_MCP_TOKEN', '')
VER = '2022-06-28'
H = {'Authorization': f'Bearer {TOKEN}', 'Notion-Version': VER}

PAGES_TO_CHECK = [
    ('2e62b428-3b00-80f4-97db-d5d00d8d1ae7', '[GM]EAST EAST 1 Untitled'),
    ('fffe501b-c5c0-4964-9666-962435430429', '[GM]WEST GM WEST MKT Untitled'),
    ('2d92b428-3b00-80c8-a1d4-c0d9338e2df3', 'B2B2 마케팅'),
]

for pid, label in PAGES_TO_CHECK:
    r = httpx.get(f'https://api.notion.com/v1/pages/{pid}', headers=H, timeout=20)
    if r.status_code == 200:
        data = r.json()
        title = ''
        for prop in data.get('properties', {}).values():
            if prop.get('type') == 'title':
                title = ''.join(t.get('plain_text', '') for t in prop.get('title', []))
                break
        last = data.get('last_edited_time', '')
        url = data.get('url', '')
        print(f'✅ [{label}]')
        print(f'   제목: {title or "(없음)"}')
        print(f'   수정: {last}')
        print(f'   URL: {url}')
    else:
        print(f'❌ [{label}] → {r.status_code}')
    print()

# 현재 local JSON에서 B2B2 페이지 목록
import json
raw = json.loads((ROOT / 'data/notion_vectors_gemini.json').read_text(encoding='utf-8'))
b2b2 = {}
for p in raw:
    pl = p.get('payload', {})
    if pl.get('team') in ('B2B2',):
        pid = pl.get('page_id', '')
        if pid and pid not in b2b2:
            b2b2[pid] = pl.get('page_title', '')
print(f'\n현재 B2B2 학습 페이지 ({len(b2b2)}개):')
for pid, title in b2b2.items():
    print(f'  {pid[:20]} | {title}')
