"""2026-05-15 업데이트 로그 → AI 사람만들기 로그 2nd 업로드"""
import os, httpx
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent.parent / ".env")
TOKEN = os.getenv("NOTION_MCP_TOKEN", "")
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Notion-Version": "2022-06-28", "Content-Type": "application/json"}

PAGE_ID  = "3252b428-3b00-802a-aff3-f33f63ec91de"
AFTER_ID = "3252b428-3b00-81ea-ac19-fb98086bf7af"

def rt(text, bold=False, code=False, color="default"):
    ann = {"bold": bold, "code": code, "color": color}
    return {"type": "text", "text": {"content": text}, "annotations": ann}

def h2(text):
    return {"object":"block","type":"heading_2","heading_2":{"rich_text":[rt(text)]}}

def h3(text):
    return {"object":"block","type":"heading_3","heading_3":{"rich_text":[rt(text)]}}

def bullet(*rts):
    return {"object":"block","type":"bulleted_list_item","bulleted_list_item":{"rich_text":list(rts)}}

def divider():
    return {"object":"block","type":"divider","divider":{}}

def code_block(text, lang="plain text"):
    return {"object":"block","type":"code","code":{"rich_text":[rt(text)],"language":lang}}

toggle_children = [
    h2("변경 사항"),

    h3("1. Qdrant 컬렉션 전환 — notion_hub_gemini → Craver"),
    bullet(rt("기존 "), rt("notion_hub_gemini", code=True), rt(" (60포인트) → "), rt("Craver", code=True), rt(" (1,262포인트) 컬렉션으로 전환")),
    bullet(rt("Craver 컬렉션: PEOPLE(15p), [GM]WEST(55p), JBT, KBT 등 구버전에 수집된 전체 데이터 포함")),
    bullet(rt("qdrant_agent.py / notion_qdrant_pipeline.py COLLECTION 상수 일괄 변경")),

    h3("2. 파이프라인 업로드 방식 변경 — delete+recreate → upsert"),
    bullet(rt("기존: 파이프라인 실행 시 컬렉션 전체 삭제 후 재생성 → 기존 데이터 소실")),
    bullet(rt("변경: 컬렉션 없으면 신규 생성, 있으면 upsert만 수행 → 기존 PEOPLE 등 데이터 보존")),
    bullet(rt("_upload_local_to_cloud() / upload_to_qdrant_cloud() 양쪽 동일 적용")),

    h3("3. System Status — Notion → Qdrant 동기화 버튼 추가"),
    bullet(rt("드로어 하단에 "), rt('"Notion → Qdrant"', bold=True), rt(" 동기화 바 상시 표시")),
    bullet(rt("마지막 동기화 시간 · 결과 통계(신규/업데이트/삭제/오류) 표시")),
    bullet(rt('"지금 동기화" 버튼 클릭 → 백그라운드 파이프라인 실행 (admin 전용)')),
    bullet(rt("진행 중: 스피너 + '동기화 중...' 비활성화, 완료 시 자동 갱신 (3초 폴링)")),
    bullet(rt("API: "), rt("POST /api/notion-sync", code=True), rt(" · "), rt("GET /api/notion-sync/status", code=True)),

    h3("4. System Status — 팀별 chunk 수 Qdrant 직접 연동"),
    bullet(rt("기존: notion_vectors_gemini.json 로컬 파일 읽기 → PEOPLE 등 누락")),
    bullet(rt("변경: Qdrant "), rt("Craver", code=True), rt(" 컬렉션 직접 scroll → 실제 저장 데이터 기준 표시")),
    bullet(rt("5분 캐시 유지, _qdrant_url() / _qdrant_api_key() qdrant_agent에서 재사용")),
    bullet(rt("결과: PEOPLE 37 chunks, [GM]WEST 681 chunks, B2B2 118 chunks 등 전 팀 정상 표시")),

    divider(),
    h2("수정 파일"),
    code_block(
        "app/agents/qdrant_agent.py        — COLLECTION 변경, upsert 방식\n"
        "scripts/notion_qdrant_pipeline.py — COLLECTION 변경, upload upsert\n"
        "app/api/routes.py                 — /api/notion-sync POST/GET 엔드포인트\n"
        "app/core/safety.py                — Qdrant 직접 읽기 + notion_sync 상태 포함\n"
        "app/frontend/chat.js v213         — 동기화 바 렌더링 + 폴링 로직\n"
        "app/static/style.css v145         — notion-sync-bar 스타일"
    ),
]

new_toggle = {
    "object": "block",
    "type": "toggle",
    "toggle": {
        "rich_text": [rt("📋 Update Log — 2026-05-15 (Qdrant Craver 컬렉션 전환 + System Status 동기화 버튼)", bold=True)],
        "children": toggle_children,
    }
}

resp = httpx.patch(
    f"https://api.notion.com/v1/blocks/{PAGE_ID}/children",
    headers=HEADERS,
    json={"after": AFTER_ID, "children": [new_toggle]},
    timeout=20,
)
print(resp.status_code)
if resp.status_code == 200:
    bid = resp.json().get("results", [{}])[0].get("id", "")
    print(f"완료: block_id={bid}")
    print(f"https://www.notion.so/skin1004/AI-2nd-{PAGE_ID.replace('-','')}")
else:
    print(resp.json().get("message", resp.text[:200]))
