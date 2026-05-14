# Notion 벡터 파이프라인

**스크립트**: `scripts/notion_qdrant_pipeline.py` (v3.0)
**커맨드**: `/notion-sync`
**자동 실행**: 매일 02:00 (APScheduler `qdrant_pipeline_daily`)

## 전체 흐름

```
DB-HUB (단일 진입점)
  └─ 팀 토글 (Craver, DB, KBT, JBT, EAST, WEST, BCM, PEOPLE, IT, CS, B2B1, B2B2)
       └─ 멘션/child_page/child_database
            ↓ Notion API 블록 텍스트 추출
       800자 청킹 (overlap 100자)
            ↓ Gemini embedding-001 (1536d, 50개 배치)
       data/notion_vectors_gemini.json (로컬 소스 오브 트루스)
            ↓ app.agents.qdrant_agent.reload_vectors()
       인메모리 numpy 벡터 스토어 (검색에 사용)
```

## DB-HUB

| 항목 | 값 |
|------|-----|
| URL | https://www.notion.so/skin1004/DB-HUB-2e12b4283b008011ae32e39bf73b7f7b |
| Page ID | 2e12b4283b008011ae32e39bf73b7f7b |
| 역할 | 모든 팀 페이지의 단일 등록 허브 |

새 페이지를 학습시키려면 → **DB-HUB의 해당 팀 토글에 페이지 멘션 추가** → 다음 sync에 자동 반영.

## 로컬 JSON 구조

```json
[
  {
    "id": "uuid-v5",
    "vector": [0.123, "..."],
    "payload": {
      "source": "{team}-hub",
      "team": "DB",
      "page_id": "...",
      "page_title": "...",
      "page_url": "https://notion.so/...",
      "breadcrumb": "DB > 페이지명",
      "chunk_index": 0,
      "last_edited_time": "2026-05-07T...",
      "content_sha256": "...",
      "text": "실제 청크 텍스트"
    }
  }
]
```

## 검색 흐름 (런타임)

1. 사용자 질문 → `qdrant_agent.run(query, team_key)`
2. Gemini embedding-001으로 질문 임베딩
3. 인메모리 numpy 배열과 코사인 유사도 계산
4. 상위 8개 (score ≥ 0.3) 추출
5. Gemini Flash로 답변 생성

## 운영 명령

```bash
# 현황 확인
python -X utf8 scripts/notion_qdrant_pipeline.py --status

# 증분 sync (변경 페이지만)
python -X utf8 scripts/notion_qdrant_pipeline.py

# 전체 재동기화
python -X utf8 scripts/notion_qdrant_pipeline.py --full

# Qdrant Cloud 백업 (선택)
python -X utf8 scripts/notion_qdrant_pipeline.py --upload-cloud
```

## 관련 파일

- `app/agents/qdrant_agent.py` — 인메모리 벡터 스토어 및 검색
- `data/notion_vectors_gemini.json` — 로컬 벡터 데이터 (~수십 MB)
- `.claude/commands/notion-sync.md` — Claude Code 커맨드
