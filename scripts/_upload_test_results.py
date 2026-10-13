"""메타데이터 테스트 50개 결과를 AI 사람만들기 로그 2nd에 업로드."""
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

# paragraph "새로운 업데이트가 위에 추가됩니다." 이후 첫 번째 토글 앞에 삽입
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
        'rich_text': rt('📊 QA Report — 2026-05-19 (메타데이터 테스트 50개 · QUALITY_GATE 0.57 적용)'),
        'children': [
            h3('📋 테스트 개요'),
            bullet('총 50개 쿼리 | QUALITY_GATE=0.57 | SCORE_THRESHOLD=0.45', children=[
                bullet('팀 필터 적용 쿼리: 43개 (PEOPLE, Craver, B2B2, DB, [GM]EAST, [GM]WEST, BCM, IT, CS)'),
                bullet('팀 필터 없는 전체 검색: 5개'),
                bullet('무관한 질문 차단 테스트: 5개 (날씨·치킨·파이썬·로또·주식)'),
                bullet('추가 B2B2 2개 (RP 정보, 소모품 코드 생성)'),
            ]),

            h3('✅ 최종 결과'),
            bullet('PASS: 50/50 (100.0%)', bold=True),
            bullet('FAIL / WARNING / ERROR: 0개', bold=True),
            bullet('QUALITY_GATE 0.5→0.57 상향 효과: 무관 질문 5개 전부 차단'),

            h3('📊 팀별 점수 분포'),
            bullet('PEOPLE    — 최저 0.638 / 최고 0.774  ✅ 전 문항 통과'),
            bullet('Craver    — 최저 0.575 / 최고 0.757  ✅ 전 문항 통과'),
            bullet('B2B2      — 최저 0.608 / 최고 0.850  ✅ 전 문항 통과'),
            bullet('DB        — 최저 0.719 / 최고 0.880  ✅ 전 문항 통과'),
            bullet('[GM]EAST  — 최저 0.640 / 최고 0.749  ✅ 전 문항 통과'),
            bullet('[GM]WEST  — 최저 0.775 / 최고 0.817  ✅ 전 문항 통과'),
            bullet('BCM       — 0.678  ✅ 통과'),
            bullet('IT        — 0.593  ✅ 통과'),
            bullet('CS        — 최저 0.641 / 최고 0.723  ✅ 전 문항 통과'),
            bullet('무관 질문 — 0.501~0.560 (전부 0.57 미만 → 차단 ✅)'),

            h3('🗂 메타데이터 필드 현황'),
            bullet('B2B2 / CS / BCM', bold=True, children=[
                bullet('page_title, breadcrumb, chunk_index, last_edited, page_url 모두 정상'),
                bullet('예: B2B2 > 출장 가이드라인_2026.04 | chunk 1 | 2026-04-28'),
            ]),
            bullet('PEOPLE (CRAVER 지식in)', bold=True, children=[
                bullet('section_path: 🤵🏻 피플팀 주요 업무 문의 ✅'),
                bullet('breadcrumb·chunk_index 비어있음 — embed_craver_jisikIn.py 수동 삽입 경로'),
                bullet('last_edited: 2026-05-18T00:00:00.000Z (하드코딩)'),
            ]),
            bullet('Craver / DB / [GM]EAST / [GM]WEST', bold=True, children=[
                bullet('last_edited: "block" — 팀 토글 블록 자체가 is_block:True로 등록됨'),
                bullet('page_url: DB-HUB URL (실제 페이지 URL 아님)'),
                bullet('[GM]EAST/WEST 검색 시 1위 hit가 토글 목차 텍스트 (실 내용은 2~3위)'),
                bullet('→ 토글 블록이 하위 페이지 이름 키워드를 모두 포함하기 때문'),
            ]),

            h3('⚠️ 경미한 이슈'),
            bullet('[42] "출장 가이드라인 B2B2" — 1위: ERP 신규 거래처 등록(0.733) / 2위: 출장 가이드라인(0.731)', children=[
                bullet('0.002 점수 차이로 순위 역전 — 실사용 영향 미미'),
            ]),
            bullet('[GM]EAST/WEST 검색 시 1위 결과 text_preview가 링크 목록 텍스트로 출력됨', children=[
                bullet('LLM이 실제 내용(2~3위)을 context로 받아 정상 답변은 가능'),
                bullet('개선 방법: 토글 목차 블록을 별도 chunk로 분리하거나 가중치 조정'),
            ]),

            h3('🔧 적용된 변경'),
            bullet('QUALITY_GATE: 0.5 → 0.57 (qdrant_agent.py) — 배포 완료'),
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
