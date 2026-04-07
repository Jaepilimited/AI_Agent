# Notion Hub → Qdrant RAG 설계 문서

## 문서 목적
이 문서는 **Notion Hub 페이지를 출발점으로 삼아 실제 Notion 페이지 본문을 수집하고**, 그 본문을 **Qdrant에 임베딩/색인**해서, LLM이 **링크만 던지는 방식이 아니라 실제 내용을 요약·설명**하도록 만드는 아키텍처를 정리한 문서다.

이 문서는 사람이 읽어도 이해되게 쓰되, **Codex가 바로 작업 단위를 쪼개서 구현할 수 있도록** 디렉터리 구조, 데이터 모델, 작업 순서, 주의사항까지 포함한다.

---

## 1. 결론부터

### 맞는 방향
- **맞다.**
- 다만 **Hub 페이지 하나만 임베딩하는 것은 부족**하다.
- **Hub는 discovery(목록/지도)** 용도로 쓰고, 실제 답변용 지식은 **각 Notion 페이지 본문 chunk**를 임베딩해야 한다.

### 왜 기존 RAG가 링크만 던졌나
기존 방식은 대체로 아래 둘 중 하나다.
1. 페이지 제목 / 링크 / 속성만 검색된다.
2. 페이지는 찾지만, 본문이 chunk 단위로 색인되어 있지 않다.

이 경우 LLM은
- “관련 페이지는 이겁니다”
- “자세한 내용은 링크 참고”
처럼 답하기 쉽다.

### 해결책
- Hub에서 대상 페이지들을 발견한다.
- 각 페이지의 **본문 markdown** 또는 **block tree**를 가져온다.
- 본문을 **섹션 단위로 chunking** 한다.
- chunk를 **Qdrant** 에 넣는다.
- 질의 시 Qdrant에서 관련 chunk를 검색해서 LLM 컨텍스트로 넣는다.
- 최종 답변에는 **본문 요약 + 출처 링크**를 같이 제공한다.

---

## 2. 핵심 원칙

1. **Hub = 인벤토리**
   - Hub 자체는 모든 팀 페이지를 모아둔 진입점이다.
   - 답변의 주 재료가 아니라, **무슨 페이지를 수집할지 정하는 목록**이다.

2. **검색 단위는 링크가 아니라 chunk**
   - RAG 품질은 “무엇을 검색하느냐”에 크게 좌우된다.
   - page-level 검색보다 **section/chunk-level 검색**이 훨씬 낫다.

3. **MCP는 인터랙티브 액세스, 색인은 Data API 중심**
   - Notion MCP는 AI 툴이 Notion에 접근하도록 돕는 연결 레이어에 가깝다.
   - 배치 수집/색인/동기화는 **Notion Data API + Webhook** 으로 설계하는 편이 더 예측 가능하고 운영하기 쉽다.

4. **링크는 버리지 말고 출처로 남긴다**
   - 링크는 나쁜 게 아니다.
   - 다만 링크가 **답변의 본문**이면 안 되고, **근거/원문 이동 수단**이어야 한다.

---

## 3. 추천 아키텍처

```text
                +--------------------+
                |    Notion Hub      |
                |  (페이지 목록/지도) |
                +---------+----------+
                          |
                          v
                +--------------------+
                | Discovery Worker    |
                | - Hub 파싱          |
                | - 페이지 목록 수집  |
                +---------+----------+
                          |
                          v
                +--------------------+
                | Ingestion Worker    |
                | - markdown fetch    |
                | - normalize         |
                | - chunk             |
                | - embed             |
                +---------+----------+
                          |
                          v
                +--------------------+
                | Qdrant              |
                | collection:         |
                | notion_chunks       |
                +---------+----------+
                          ^
                          |
                +---------+----------+
                | FastAPI Query API   |
                | - /search           |
                | - /ask              |
                | - /admin/reindex    |
                +---------+----------+
                          |
                          v
                +--------------------+
                | LLM Answer Layer    |
                | - retrieved chunks  |
                | - answer + citations|
                +--------------------+

                +--------------------+
                | Notion Webhook      |
                | page.created        |
                | page.content_updated|
                | page.deleted        |
                +---------+----------+
                          |
                          v
                +--------------------+
                | Sync Worker         |
                | delta reindex       |
                +--------------------+
```

---

## 4. 구현 방침: v1 / v2

### v1 (반드시 이렇게 시작)
- 단일 컬렉션: `notion_chunks`
- dense embedding만 사용
- Hub → 실제 페이지 본문 수집 → chunk → Qdrant upsert
- 답변 시 상위 5~8개 chunk를 LLM에 넣음
- 링크는 출처로만 표시

### v1.5
- page summary를 별도 생성해서 recall 개선
- metadata filter 강화 (`team`, `hub_id`, `page_id`, `tags`, `last_edited_time`)
- 삭제/이동/권한 누락 처리 보강

### v2
- dense + sparse hybrid retrieval
- reranker 추가
- page-level collection + chunk-level collection 2단계 검색
- summary cache / answer cache

**중요:** 처음부터 하이브리드, 멀티 컬렉션, 리랭커까지 다 넣으면 복잡도만 커진다. **v1은 dense + single collection으로 충분**하다.

---

## 5. 왜 Hub 하나만 임베딩하면 안 되나

예를 들어 Hub 페이지가 이런 식이라고 하자.

- 마케팅팀 노션
- 데이터팀 노션
- 운영팀 노션
- 캠페인 회고 페이지
- 분기 OKR 페이지

Hub는 이 페이지들이 있다는 사실은 알려주지만,
- 각 페이지 안의 상세 정책
- 회고 내용
- 의사결정 배경
- 실행 항목
- 회의 결론

같은 실제 지식은 대부분 **개별 페이지 본문** 안에 있다.

즉 Hub만 임베딩하면 검색 결과가
- 페이지 제목
- 링크
- 간단 메타데이터
중심이 되고,
LLM은 다시 링크 안내형 답변을 하기 쉽다.

---

## 6. Notion 수집 전략

### 추천
- **Hub에서 page URL / page ID / team / 분류 정보 수집**
- 각 page ID에 대해 **Retrieve Page Markdown** 우선 사용
- 필요시 block API fallback

### 왜 markdown 우선인가
Notion은 페이지 본문을 **enhanced markdown** 으로 가져오는 API를 제공한다. 이 경로가 chunking과 embedding에 훨씬 유리하다.

### 예외 처리
1. **권한 부족**
   - integration이 공유받지 못한 child page는 읽지 못할 수 있다.
2. **큰 페이지 truncation**
   - 매우 큰 페이지는 잘릴 수 있다.
3. **unsupported block type**
   - 일부 블록은 markdown만으로 완전 복원되지 않을 수 있다.

### 실전 규칙
- 1차: `GET /v1/pages/{page_id}/markdown`
- 2차: 응답에 `unknown_block_ids` / truncation 이 있으면 보강 fetch
- 3차: 특정 블록 타입이 중요하면 block API로 보충

---

## 7. Chunking 전략

### 나쁜 예
- 2000자씩 무지성 분할

문제:
- 섹션 경계 깨짐
- 표/리스트/결론 분리됨
- retrieval 결과가 산만해짐

### 좋은 예
- **heading-aware chunking**
- 기준:
  - 페이지 제목
  - breadcrumb
  - H1/H2/H3
  - 본문 문단/리스트

### 추천 규칙
- 목표 크기: **400~800 tokens**
- overlap: **50~100 tokens**
- chunk payload에 반드시 포함:
  - `page_title`
  - `section_path`
  - `breadcrumb`
  - `page_url`
  - `page_id`
  - `team`
  - `hub_id`
  - `text`

### chunk 예시
```text
page_title: "2026 Q2 Campaign Review"
section_path: "Findings > Reddit feedback"
breadcrumb: "Hub / Marketing / Campaigns / Q2 Review"
text: "Users repeatedly complained that ..."
```

---

## 8. Qdrant 설계

### 컬렉션 전략
#### v1 추천
- 컬렉션 하나만 사용: `notion_chunks`

#### 이유
- 운영 단순
- 필터링 충분
- 팀/페이지/허브 구분은 payload로 해결 가능

### point payload 예시
```json
{
  "source": "notion",
  "doc_type": "chunk",
  "hub_id": "hub_main",
  "team": "marketing",
  "page_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "page_url": "https://www.notion.so/...",
  "page_title": "2026 Q2 Campaign Review",
  "breadcrumb": "Hub / Marketing / Campaigns / Q2 Review",
  "section_path": "Findings > Reddit feedback",
  "chunk_index": 3,
  "tags": ["reddit", "campaign", "feedback"],
  "status": "active",
  "last_edited_time": "2026-04-01T10:30:00Z",
  "content_sha256": "...",
  "text": "...chunk content..."
}
```

### payload index 추천 필드
- `source`
- `hub_id`
- `team`
- `page_id`
- `status`
- `last_edited_time` (필터가 많으면 고려)
- `tags` (필요 시)

### collection 이름
- `notion_chunks`

### vector distance
- 대부분 **Cosine** 으로 시작

---

## 9. 검색 흐름

### `/search`
1. 사용자 질문을 임베딩한다.
2. Qdrant에서 top-k chunk 검색
3. 필요시 `team`, `hub_id`, `tags` 필터 적용
4. 상위 결과 반환

### `/ask`
1. 질문 임베딩
2. Qdrant 검색
3. 중복/유사 chunk 정리
4. chunk들을 prompt에 넣음
5. LLM이 **답변은 본문 기반으로 생성**
6. 마지막에 출처 링크와 페이지 제목 표시

### LLM 시스템 규칙 예시
- 답변은 검색된 chunk에 근거해서만 작성할 것
- 근거가 약하면 모른다고 말할 것
- 링크만 던지지 말고 먼저 내용을 요약할 것
- 출처는 답변 끝에 page title + URL로 붙일 것

---

## 10. 동기화 전략

### 백필(backfill)
초기 1회 전체 색인
- Hub에서 모든 대상 페이지 수집
- 전체 page markdown fetch
- chunk + embed + upsert

### 증분 동기화(delta sync)
Notion webhook 사용
- `page.created`
- `page.content_updated`
- `page.properties_updated`
- `page.deleted`

### 처리 규칙
- `page.created` → 신규 색인
- `page.content_updated` → 해당 page 전체 재색인
- `page.properties_updated` → 제목/태그/메타데이터만 바뀌면 payload 갱신, 애매하면 재색인
- `page.deleted` → 해당 page의 모든 chunk `status=deleted` 또는 hard delete

### 왜 page 전체 재색인?
부분 패치보다 단순하고 안정적이다.
초기에는 운영 단순성이 더 중요하다.

---

## 11. 디렉터리 구조 추천

```text
notion-rag/
├─ apps/
│  ├─ api/
│  │  ├─ main.py
│  │  ├─ settings.py
│  │  ├─ deps.py
│  │  └─ routes/
│  │     ├─ ask.py
│  │     ├─ search.py
│  │     ├─ admin.py
│  │     └─ webhook_notion.py
│  └─ worker/
│     ├─ main.py
│     └─ jobs/
│        ├─ backfill_hub.py
│        ├─ ingest_page.py
│        ├─ sync_page.py
│        └─ delete_page.py
├─ core/
│  ├─ notion/
│  │  ├─ client.py
│  │  ├─ hub_discovery.py
│  │  ├─ markdown_fetcher.py
│  │  ├─ normalizer.py
│  │  └─ webhook_verify.py
│  ├─ chunking/
│  │  ├─ heading_chunker.py
│  │  └─ models.py
│  ├─ embeddings/
│  │  ├─ client.py
│  │  └─ batch_embed.py
│  ├─ qdrant/
│  │  ├─ client.py
│  │  ├─ collections.py
│  │  ├─ payloads.py
│  │  ├─ upsert.py
│  │  └─ search.py
│  ├─ rag/
│  │  ├─ retrieve.py
│  │  ├─ dedupe.py
│  │  ├─ prompt.py
│  │  └─ answer.py
│  └─ utils/
│     ├─ hashes.py
│     ├─ ids.py
│     └─ logging.py
├─ tests/
│  ├─ test_chunker.py
│  ├─ test_notion_fetcher.py
│  ├─ test_qdrant_search.py
│  └─ test_webhook_flow.py
├─ scripts/
│  ├─ bootstrap_collection.py
│  ├─ run_backfill.py
│  └─ reindex_page.py
├─ docs/
│  └─ notion_qdrant_rag_blueprint.md
├─ .env.example
├─ docker-compose.yml
├─ pyproject.toml
└─ README.md
```

---

## 12. FastAPI API 스펙 예시

### POST `/search`
입력:
```json
{
  "query": "마케팅팀에서 최근 레딧 관련 정책 정리해둔 페이지 있어?",
  "team": "marketing",
  "top_k": 8
}
```

출력:
```json
{
  "hits": [
    {
      "score": 0.83,
      "page_title": "2026 Reddit Monitoring Guide",
      "section_path": "Policy > Response rules",
      "page_url": "https://www.notion.so/...",
      "text": "When a negative brand mention appears ..."
    }
  ]
}
```

### POST `/ask`
입력:
```json
{
  "query": "레딧 부정 언급 대응 정책이 뭐야?",
  "team": "marketing",
  "top_k": 6
}
```

출력:
```json
{
  "answer": "마케팅팀 문서 기준으로, 부정 언급은 1차 분류 후 ...",
  "sources": [
    {
      "page_title": "2026 Reddit Monitoring Guide",
      "page_url": "https://www.notion.so/..."
    }
  ]
}
```

### POST `/webhooks/notion`
- Notion webhook 수신
- event type 보고 worker enqueue

### POST `/admin/reindex/page`
입력:
```json
{
  "page_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

---

## 13. 초기 구현 순서

### Step 1. Notion client 만들기
목표:
- page markdown 가져오기
- 허브에서 대상 페이지 목록 수집하기

필수 함수:
- `get_page_markdown(page_id)`
- `get_page_metadata(page_id)`
- `discover_pages_from_hub(hub_page_id)`

### Step 2. chunker 만들기
목표:
- markdown → section-aware chunk list

필수 함수:
- `chunk_markdown(page_title, breadcrumb, markdown) -> list[Chunk]`

### Step 3. embedding client 만들기
목표:
- chunk list → vectors

필수 함수:
- `embed_texts(texts)`

### Step 4. Qdrant collection bootstrap
목표:
- collection 생성
- payload index 생성

필수 함수:
- `ensure_collection()`
- `ensure_payload_indexes()`

### Step 5. page ingest pipeline 만들기
목표:
- page_id 하나를 끝까지 처리

필수 함수:
- `ingest_page(page_id, team, hub_id)`

### Step 6. hub backfill job 만들기
목표:
- 허브 전체 페이지를 순회하며 색인

필수 함수:
- `backfill_hub(hub_page_id)`

### Step 7. query API 만들기
목표:
- `/search`, `/ask`

### Step 8. webhook 동기화
목표:
- page 변경 시 해당 page 재색인

---

## 14. 핵심 함수 설계 예시

```python
from dataclasses import dataclass
from typing import List

@dataclass
class Chunk:
    page_id: str
    page_title: str
    page_url: str
    breadcrumb: str
    section_path: str
    chunk_index: int
    text: str


def ingest_page(page_id: str, team: str, hub_id: str) -> int:
    page = notion.get_page_metadata(page_id)
    markdown = notion.get_page_markdown(page_id)
    chunks = chunk_markdown(
        page_title=page.title,
        breadcrumb=page.breadcrumb,
        markdown=markdown,
    )
    vectors = embed_texts([c.text for c in chunks])
    points = build_qdrant_points(
        chunks=chunks,
        vectors=vectors,
        team=team,
        hub_id=hub_id,
        page=page,
    )
    qdrant.upsert(points)
    return len(points)
```

---

## 15. Qdrant bootstrap 예시

```python
from qdrant_client import QdrantClient, models

COLLECTION = "notion_chunks"
EMBED_DIM = 1536

client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)


def ensure_collection() -> None:
    if client.collection_exists(COLLECTION):
        return

    client.create_collection(
        collection_name=COLLECTION,
        vectors_config=models.VectorParams(
            size=EMBED_DIM,
            distance=models.Distance.COSINE,
        ),
    )

    for field in ["source", "hub_id", "team", "page_id", "status"]:
        client.create_payload_index(
            collection_name=COLLECTION,
            field_name=field,
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
```

---

## 16. 질문 응답 프롬프트 설계 예시

### system prompt
```text
You answer only from retrieved Notion chunks.
Do not respond with only a link.
First explain the relevant content in plain language.
Then list sources with page title and URL.
If the retrieved evidence is insufficient, say so clearly.
```

### user context to model
```text
[Question]
레딧 부정 언급 대응 정책이 뭐야?

[Retrieved Chunks]
1) page_title=2026 Reddit Monitoring Guide
section_path=Policy > Escalation
text=...

2) page_title=Community Response Handbook
section_path=Brand Safety > Negative mentions
text=...
```

---

## 17. 운영상 주의사항

### 1) 권한
- internal integration이면 페이지를 수동 공유해야 한다.
- Hub가 보인다고 모든 child page를 integration이 읽을 수 있는 건 아니다.

### 2) 삭제 처리
- page.deleted 이벤트를 처리하지 않으면 오래된 지식이 남는다.
- 최소한 `status=deleted` 로 막아야 한다.

### 3) 페이지 이동/이름 변경
- URL, breadcrumb, title이 바뀔 수 있다.
- `page_id` 를 기본 식별자로 써야 한다.

### 4) 너무 큰 페이지
- 한 페이지에 문서가 지나치게 많으면 retrieval 품질이 떨어진다.
- 가능한 팀 문서를 적절히 나누는 편이 좋다.

### 5) 텍스트 정규화
- 토글, 콜아웃, 리스트, 표, 코드블록 처리 규칙을 통일해야 한다.

### 6) 보안
- Qdrant self-host면 인증/API key/TLS를 반드시 걸어야 한다.
- Notion integration token은 코드에 박지 말고 환경변수/secret manager로 관리해야 한다.

---

## 18. 이 방향이 틀리는 경우

아래에 해당하면 지금 설계를 수정해야 한다.

1. **질문 범위가 너무 구조화된 데이터 중심**
   - 예: DB row 조회, 상태값 필터, 집계
   - 이 경우는 vector 검색보다 **Notion DB 질의 + structured retrieval** 이 더 맞다.

2. **문서보다 최신성이 절대적으로 중요**
   - webhook 지연/재색인 지연이 허용되지 않으면 별도 저장 전략이 필요하다.

3. **문서 품질이 너무 낮음**
   - 제목 없음, 섹션 없음, 문서 구조 엉망이면 chunking 품질이 떨어진다.

하지만 일반적인 “팀별 문서 검색 + 설명형 답변” 목적이라면 현재 방향은 맞다.

---

## 19. 내가 권장하는 최종안

### 채택
- Hub는 유지
- Qdrant 도입
- 페이지 본문 chunk 임베딩
- FastAPI 기반 검색/질의 API
- Webhook 기반 증분 동기화

### 보류
- 처음부터 MCP 기반 ingestion
- 처음부터 hybrid + reranker + 멀티컬렉션
- 처음부터 요약 캐시/에이전트 워크플로우 과투자

### 가장 현실적인 MVP
1. Hub에서 page ID 목록 가져오기
2. page markdown 수집
3. heading-aware chunking
4. Qdrant single collection 업서트
5. `/search`, `/ask` 구현
6. 답변은 내용 먼저, 링크는 출처로만
7. webhook으로 변경분 재색인

---

## 20. Codex에게 바로 줄 작업 단위

### Task 1. Notion API client
- internal integration token 기반 client 작성
- retrieve page metadata
- retrieve page markdown
- hub page에서 child page / page mention / URL 파싱

### Task 2. Markdown normalizer
- 공백 정리
- excessive blank lines 정리
- code block / bullet / table 처리 규칙 정하기

### Task 3. Heading-aware chunker
- H1/H2/H3 기준 section path 유지
- 400~800 tokens target
- overlap 50~100

### Task 4. Qdrant bootstrap
- `notion_chunks` 컬렉션 생성
- payload index 생성
- upsert/search 함수 작성

### Task 5. Ingest page pipeline
- page_id 입력 → markdown → chunk → embed → qdrant
- content hash 비교로 no-op 최적화

### Task 6. Hub backfill job
- hub page 전체 순회
- 페이지별 ingest 실행
- 실패 page 기록

### Task 7. FastAPI query service
- `/search`
- `/ask`
- `/admin/reindex/page`

### Task 8. Webhook sync
- `/webhooks/notion`
- page.created / content_updated / properties_updated / deleted 처리

### Task 9. Prompt policy
- 링크-only 답변 금지
- 근거 부족 시 명시
- source 목록 항상 반환

### Task 10. Tests
- chunker test
- ingest pipeline test
- webhook event test
- retrieval relevance smoke test

---

## 21. Codex용 구현 지시문 예시

```text
Build a FastAPI-based Notion RAG service.

Requirements:
- Use Notion Data API, not MCP, for ingestion.
- Use a Hub page only for discovery of target pages.
- Fetch each page's actual markdown content.
- Chunk content with heading-aware chunking.
- Store chunk embeddings in a single Qdrant collection named notion_chunks.
- Store metadata in payload: source, hub_id, team, page_id, page_url, page_title, breadcrumb, section_path, chunk_index, status, last_edited_time, text.
- Add payload indexes for source, hub_id, team, page_id, status.
- Implement /search and /ask endpoints.
- /ask must answer from retrieved chunk content first, then provide source links.
- Implement Notion webhook handler for page.created, page.content_updated, page.properties_updated, page.deleted.
- Reindex a full page on content updates.
- Use environment variables for secrets.
- Add tests for chunking, ingestion, retrieval, and webhook processing.
```

---

## 22. 추천 환경변수 예시

```bash
NOTION_TOKEN=
NOTION_VERSION=2026-03-11
NOTION_HUB_PAGE_ID=
QDRANT_URL=
QDRANT_API_KEY=
QDRANT_COLLECTION=notion_chunks
EMBEDDING_MODEL=
OPENAI_API_KEY=
LLM_MODEL=
```

---

## 23. 마지막 정리

가장 중요한 문장만 남기면 이렇다.

- **Hub 페이지를 임베딩하는 것만으로는 부족하다.**
- **Hub는 수집 대상 목록이고, 실제 임베딩 대상은 각 페이지 본문 chunk다.**
- **LLM이 링크 대신 내용을 말하게 하려면, 검색 결과가 링크가 아니라 본문 chunk여야 한다.**
- **운영은 Notion Data API + Webhook + Qdrant single collection으로 시작하는 것이 가장 현실적이다.**

---

## References
- Notion Retrieve a page as markdown: https://developers.notion.com/reference/retrieve-page-markdown
- Notion Retrieve a page: https://developers.notion.com/reference/retrieve-a-page
- Notion Working with markdown content: https://developers.notion.com/guides/data-apis/working-with-markdown-content
- Notion Webhooks: https://developers.notion.com/reference/webhooks
- Notion Event types & delivery: https://developers.notion.com/reference/webhooks-events-delivery
- Notion Authorization: https://developers.notion.com/guides/get-started/authorization
- Notion MCP overview: https://developers.notion.com/guides/mcp/mcp
- Notion MCP supported tools: https://developers.notion.com/guides/mcp/mcp-supported-tools
- Qdrant Overview: https://qdrant.tech/documentation/overview/
- Qdrant Filtering: https://qdrant.tech/documentation/search/filtering/
- Qdrant Collections / multitenancy: https://qdrant.tech/documentation/manage-data/collections/
- Qdrant FAQ fundamentals: https://qdrant.tech/documentation/faq/qdrant-fundamentals/
- Qdrant Hybrid queries: https://qdrant.tech/documentation/search/hybrid-queries/
- Qdrant Security: https://qdrant.tech/documentation/operations/security/
