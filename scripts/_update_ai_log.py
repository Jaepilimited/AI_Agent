"""AI 사람만들기 로그 2nd 페이지에 오늘 업데이트 로그 추가."""
import httpx, os, sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT / '.env')

TOKEN = os.getenv('NOTION_MCP_TOKEN', '')
VER = '2022-06-28'
H = {'Authorization': f'Bearer {TOKEN}', 'Notion-Version': VER, 'Content-Type': 'application/json'}
PAGE_ID = '3252b4283b00802aaff3f33f63ec91de'

def get_children(bid):
    r = httpx.get(f'https://api.notion.com/v1/blocks/{bid}/children?page_size=100', headers=H, timeout=20)
    return r.json().get('results', [])

# paragraph "새로운 업데이트가 위에 추가됩니다." 블록 ID 찾기
blocks = get_children(PAGE_ID)
after_block_id = None
for b in blocks:
    btype = b.get('type', '')
    body = b.get(btype, {})
    rt = body.get('rich_text', []) if isinstance(body, dict) else []
    text = ''.join(x.get('plain_text', '') for x in rt)
    print(f'[{btype}] {b["id"][:20]} | {text[:60]}')
    if btype == 'paragraph' and '새로운 업데이트' in text:
        after_block_id = b['id']
        print(f'  ↑ INSERT AFTER THIS')
        break

if not after_block_id:
    print('ERROR: 기준 블록을 찾지 못했습니다. 페이지 맨 앞에 추가합니다.')

# 오늘 업데이트 로그 블록 구성
new_toggle = {
    "type": "toggle",
    "toggle": {
        "rich_text": [{"type": "text", "text": {"content": "📋 Update Log — 2026-05-18 (Qdrant 할루시네이션 수정 + Notion DB 학습 지원 + CRAVER 지식in 임베딩)"}}],
        "children": [
            {
                "type": "heading_3",
                "heading_3": {"rich_text": [{"type": "text", "text": {"content": "🔧 주요 변경 사항"}}]}
            },
            {
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [{"type": "text", "text": {"content": "Qdrant 할루시네이션 방지 강화"}, "annotations": {"bold": True}}],
                    "children": [
                        {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "SCORE_THRESHOLD: 0.3 → 0.45 (벡터 검색 최소 유사도)"}}]}},
                        {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "QUALITY_GATE: 0.5 (상위 결과 0.5 미만이면 관련 자료 없음 처리)"}}]}},
                        {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "프롬프트 개선: 즉시 답변, '찾을 수 없습니다' 시작 금지 규칙 추가"}}]}},
                    ]
                }
            },
            {
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [{"type": "text", "text": {"content": "채팅 422 오류 수정 (chat.js v214)"}, "annotations": {"bold": True}}],
                    "children": [
                        {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "parseInt(null) → NaN → JSON null → Pydantic List[int] 거부 버그"}}]}},
                        {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "getEnabledTeamResPayload() + _rebuildTeamRes()에서 NaN 필터링 추가"}}]}},
                    ]
                }
            },
            {
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [{"type": "text", "text": {"content": "잘못 학습된 채용 페이지 7개 삭제 (Qdrant + 로컬 JSON)"}, "annotations": {"bold": True}}],
                    "children": [
                        {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "올바른 채용 페이지: 1156714bbf6880aa8189f1c5d64620fe 만 유지"}}]}},
                    ]
                }
            },
            {
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [{"type": "text", "text": {"content": "CRAVER 지식in 수동 임베딩 (scripts/embed_craver_jisikIn.py 신규)"}, "annotations": {"bold": True}}],
                    "children": [
                        {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "Notion 통합 권한 없는 페이지 → 내부 API(loadPageChunk) 사용"}}]}},
                        {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "PEOPLE팀 담당자 10명 + 회사정보 = 12 chunks 학습"}}]}},
                    ]
                }
            },
            {
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [{"type": "text", "text": {"content": "Notion Database 학습 지원 추가 (notion_qdrant_pipeline.py)"}, "annotations": {"bold": True}}],
                    "children": [
                        {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "mention.type=database 처리 추가 (기존: page 멘션만 지원)"}}]}},
                        {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "bulleted_list_item 하위 중첩 멘션 재귀 탐색 추가"}}]}},
                        {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "fetch_database_meta() 함수 추가 (/databases/{id} API)"}}]}},
                        {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "신규 학습: DB팀 인플루언서 DB(18ch), [GM]EAST 시딩캘린더(3ch), PKG x2(10ch), [GM]WEST x2(5ch)"}}]}},
                    ]
                }
            },
            {
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [{"type": "text", "text": {"content": "Qdrant ID 정규화 (qdrant_agent.py)"}, "annotations": {"bold": True}}],
                    "children": [
                        {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "비정상 ID(\"1\",\"6\" 등) → UUID 자동 변환 (_normalize_id 함수)"}}]}},
                        {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "107개 포인트 ID 정규화 완료"}}]}},
                    ]
                }
            },
            {
                "type": "heading_3",
                "heading_3": {"rich_text": [{"type": "text", "text": {"content": "📊 현황"}}]}
            },
            {
                "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "총 학습 포인트: 179 → 212 (+33)"}}]}
            },
            {
                "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "학습 페이지: 35개 (DB-HUB 기준 56개 중 20개 404 스킵)"}}]}
            },
            {
                "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "로컬 JSON: 4.5 MB"}}]}
            },
        ]
    }
}

# 블록 삽입
body = {"children": [new_toggle]}
if after_block_id:
    body["after"] = after_block_id

resp = httpx.patch(
    f'https://api.notion.com/v1/blocks/{PAGE_ID}/children',
    headers=H,
    json=body,
    timeout=30,
)
print(f'\nAPI 응답: {resp.status_code}')
if resp.status_code != 200:
    print(resp.text[:500])
else:
    print('✅ 업데이트 로그 추가 완료')
    print(f'   https://www.notion.so/AI-2nd-{PAGE_ID.replace("-","")}')
