import httpx, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT / '.env')
TOKEN = os.getenv('NOTION_MCP_TOKEN', '')
VER = '2022-06-28'
H = {'Authorization': f'Bearer {TOKEN}', 'Notion-Version': VER}
PAGE_ID = '3252b4283b00802aaff3f33f63ec91de'

url = f'https://api.notion.com/v1/blocks/{PAGE_ID}/children?page_size=100'
while url:
    r = httpx.get(url, headers=H, timeout=20)
    data = r.json()
    for b in data.get('results', []):
        btype = b.get('type', '')
        bid = b.get('id', '')
        body = b.get(btype, {})
        if isinstance(body, dict):
            rt = body.get('rich_text', [])
            text = ''.join(x.get('plain_text', '') for x in rt)
            title_rt = body.get('title', [])
            title = ''.join(x.get('plain_text', '') for x in title_rt)
        else:
            text = ''
            title = ''
        print(f'[{btype}] [{bid[:8]}] {text or title or "(empty)"}')
    if data.get('has_more') and data.get('next_cursor'):
        url = f'https://api.notion.com/v1/blocks/{PAGE_ID}/children?page_size=100&start_cursor={data["next_cursor"]}'
    else:
        url = None
