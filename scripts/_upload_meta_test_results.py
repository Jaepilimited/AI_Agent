"""Meta SQL 테스트 49개 결과 + Qdrant Google Search 폴백을 AI 사람만들기 로그 2nd에 업로드."""
import httpx, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT / '.env')

TOKEN = os.getenv('NOTION_MCP_TOKEN', '')
VER = '2022-06-28'
H = {'Authorization': f'Bearer {TOKEN}', 'Notion-Version': VER, 'Content-Type': 'application/json'}
PAGE_ID = '3252b4283b00802aaff3f33f63ec91de'

def gc(bid):
    r = httpx.get(f'https://api.notion.com/v1/blocks/{bid}/children?page_size=100', headers=H, timeout=20)
    return r.json().get('results', []) if r.status_code == 200 else []

def rt(text, bold=False, color='default'):
    ann = {'bold': bold, 'color': color}
    return [{'type': 'text', 'text': {'content': text}, 'annotations': ann}]

def bullet(text, bold=False, children=None):
    b = {'type': 'bulleted_list_item', 'bulleted_list_item': {'rich_text': rt(text, bold)}}
    if children:
        b['bulleted_list_item']['children'] = children
    return b

def h3(text):
    return {'type': 'heading_3', 'heading_3': {'rich_text': rt(text)}}

def para(text):
    return {'type': 'paragraph', 'paragraph': {'rich_text': rt(text)}}

def divider():
    return {'type': 'divider', 'divider': {}}

blocks = gc(PAGE_ID)
after_block_id = None
for b in blocks:
    btype = b.get('type', '')
    body = b.get(btype, {})
    text = ''.join(x.get('plain_text', '') for x in body.get('rich_text', []))
    if btype == 'paragraph' and '새로운 업데이트' in text:
        after_block_id = b['id']
        break

new_toggle = {
    'type': 'toggle',
    'toggle': {
        'rich_text': rt('🧪 QA Report — 2026-05-19 (Meta SQL 테스트 49개 · Qdrant Google Search 폴백 추가)'),
        'children': [
            h3('📋 변경 사항 요약'),
            bullet('Meta 광고 SQL Agent 테스트 49개 실행 (BigQuery 실제 실행)', bold=True),
            bullet('Qdrant 무관 질문 → Google Search 폴백 추가', bold=True),

            h3('🧪 Meta SQL Agent 테스트 결과'),
            bullet('총 49개 | ✅ PASS: 47개 (95.9%) | ⚠️ NO_ROWS: 2개 | ❌ ERROR: 0개', bold=True),
            bullet('테이블: skin1004-319714.ad_data.meta data_test', children=[
                bullet('brand = \'skin1004\' (소문자) ✅'),
                bullet('LOWER(publisher_platform) LIKE \'%facebook%\' 패턴 ✅'),
                bullet('start_date_formatted에 DATE() 래핑 ✅'),
                bullet('테이블명 공백 → 백틱 처리 ✅'),
            ]),

            h3('📊 카테고리별 결과'),
            bullet('SKIN1004 기본 조회 (10개) — 10/10 PASS ✅', children=[
                bullet('활성 광고, 페이스북, 인스타그램, Official/Partnership, 비디오/이미지, 페이지명'),
            ]),
            bullet('국가별 분석 (8개) — 8/8 PASS ✅', children=[
                bullet('한국, 미국, 베트남, 태국, 일본, 싱가포르, 전체 국가 분포'),
            ]),
            bullet('브랜드 비교 (8개) — 8/8 PASS ✅', children=[
                bullet('medicube, cosrx, beauty_of_joseon, anua, dr_melaxin, laneige 전 브랜드 정상'),
            ]),
            bullet('날짜/시간 분석 (8개) — 8/8 PASS ✅', children=[
                bullet('올해/최근 7일/90일/월별/오래된 광고 — 날짜 필터 모두 정상'),
            ]),
            bullet('플랫폼 분석 (8개) — 6/8 PASS, 2 NO_ROWS ⚠️', children=[
                bullet('[NO_ROWS] 인스타그램에만 집행된 SKIN1004 광고 — publisher_platform에 단독 \'instagram\' 행 없음'),
                bullet('[NO_ROWS] 메신저 광고 포함된 SKIN1004 광고 — 해당 기간 messenger 플랫폼 데이터 없음'),
                bullet('→ SQL 오류 아님, 실데이터 부재. SKIN1004는 항상 facebook,instagram 복합 집행'),
            ]),
            bullet('고급 분석 (7개) — 7/7 PASS ✅', children=[
                bullet('페이지별 최신 광고, 한국 파트너십, 전체 현황, 공식 vs 파트너십 비율 등'),
            ]),

            h3('🔧 Qdrant Google Search 폴백'),
            bullet('변경 파일: app/agents/qdrant_agent.py', bold=True),
            bullet('QUALITY_GATE(0.57) 미달 시 동작 변경', children=[
                bullet('이전: "관련 자료가 없습니다" 메시지 반환'),
                bullet('이후: Gemini Flash generate_with_search() 호출 → 구글 검색 기반 답변'),
                bullet('날씨, 치킨, 파이썬 문법, 로또 등 무관 질문 → 구글 검색으로 정상 답변'),
                bullet('폴백 실패 시 기존 "관련 자료 없음" 메시지로 graceful fallback'),
                bullet('응답 footer: *Google 검색 기반 답변 · 사내 문서와 무관* 자동 추가'),
            ]),
            bullet('팀 필터 설정 여부와 무관하게 동작 (people, b2b2 등 어느 팀에서든 무관 질문 처리)'),

            h3('📌 기존 Qdrant QA 변경사항 (2026-05-18)'),
            bullet('QUALITY_GATE 0.5 → 0.57 상향 (무관 질문 5개 전부 차단)'),
            bullet('Qdrant ID 비UUID 정규화 (_normalize_id 함수)'),
            bullet('DB-HUB mention.type=database 지원 (재귀 탐색)'),
        ]
    }
}

body = {'children': [new_toggle]}
if after_block_id:
    body['after'] = after_block_id

resp = httpx.patch(
    f'https://api.notion.com/v1/blocks/{PAGE_ID}/children',
    headers=H, json=body, timeout=30,
)
print(f'API 응답: {resp.status_code}')
if resp.status_code == 200:
    print('✅ QA Report 업로드 완료')
    print(f'   https://www.notion.so/AI-2nd-{PAGE_ID.replace("-","")}')
else:
    print(resp.text[:500])
