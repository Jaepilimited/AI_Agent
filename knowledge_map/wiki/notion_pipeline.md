# Notion 벡터 파이프라인

**스크립트**: `scripts/notion_qdrant_pipeline.py` (v3.0)
**커맨드**: `/notion-sync`
**자동 실행**: 매일 05:00 (Qdrant 서버 직접 연결 + 업데이트)

> **2026-05-15 재정의**: Qdrant 서버에 매일 05:00에만 직접 연결해 데이터 전체를 갱신.
> 평상시에는 05:00에 업로드된 데이터로 운용. 로컬 JSON은 파이프라인 중간 산물.

## 핵심 원칙

| 항목 | 내용 |
|------|------|
| 연결 시점 | **매일 05:00 1회** (그 외에는 Qdrant 서버 연결 없음) |
| 운용 데이터 | 05:00 업데이트 완료 후 Qdrant 서버 데이터 직접 사용 |
| 업데이트 주기 | 매일 05:00 단 1회 |
| 검색 백엔드 | Qdrant 서버 직접 쿼리 |

## 전체 흐름

```
[매일 05:00]
DB-HUB (단일 진입점: 2e12b4283b008011ae32e39bf73b7f7b)
  └─ 팀 토글 (Craver, DB, KBT, JBT, EAST, WEST, BCM, PEOPLE, IT, CS, B2B1, B2B2)
       └─ 멘션/child_page/child_database
            ↓ Notion API 블록 텍스트 추출
       800자 청킹 (overlap 100자)
            ↓ Gemini embedding-001 (1536d, 50개 배치)
       data/notion_vectors_gemini.json (로컬 중간 산물)
            ↓ Qdrant 서버에 직접 업로드 (전체 재동기화)
       Qdrant 서버 (검색 백엔드, 05:00 이후 운용)

[05:00 이후 ~ 익일 05:00 전]
사용자 질문 → Qdrant 서버 직접 쿼리 → 답변
```

## DB-HUB

| 항목 | 값 |
|------|-----|
| URL | https://www.notion.so/skin1004/DB-HUB-2e12b4283b008011ae32e39bf73b7f7b |
| Page ID | 2e12b4283b008011ae32e39bf73b7f7b |
| 역할 | 모든 팀 페이지의 단일 등록 허브 |

새 페이지를 학습시키려면 → **DB-HUB의 해당 팀 토글에 페이지 멘션 추가** → 다음날 05:00 sync에 자동 반영.

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
3. **Qdrant 서버 직접 쿼리** (코사인 유사도)
4. 상위 8개 (score ≥ 0.3) 추출
5. Gemini Flash로 답변 생성

## 운영 명령

```bash
# 현황 확인
python -X utf8 scripts/notion_qdrant_pipeline.py --status

# 증분 sync (변경 페이지만)
python -X utf8 scripts/notion_qdrant_pipeline.py

# 전체 재동기화 (05:00 자동 실행과 동일)
python -X utf8 scripts/notion_qdrant_pipeline.py --full

# Qdrant 서버 업로드
python -X utf8 scripts/notion_qdrant_pipeline.py --upload-cloud
```

## 관련 파일

- `app/agents/qdrant_agent.py` — Qdrant 서버 직접 쿼리 및 검색
- `data/notion_vectors_gemini.json` — 로컬 중간 산물 (~수십 MB)
- `.claude/commands/notion-sync.md` — Claude Code 커맨드
