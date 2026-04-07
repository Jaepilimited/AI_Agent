# Notion Hub -> Qdrant RAG 실행 계획서

## 문서 목적
- 월요일 출근 후 이 문서만 읽고 바로 구현에 들어갈 수 있게 한다.
- 현재 `db` 디렉토리의 기존 구조를 유지할지, 갈아엎을지에 대한 판단을 끝낸 상태로 정리한다.
- 링크형 Notion Hub 페이지를 기준으로 대상 페이지를 발견하고, 각 페이지 본문을 임베딩해서 Qdrant에 적재하는 MVP를 우선 완성한다.

---

## 최종 판단

### 결론
- 현재 방향은 맞다.
- 기존 `DATABASE_TARGETS` 중심 구조는 이번 요구사항과 맞지 않으므로 과감히 정리해도 된다.
- 핵심은 `Hub 페이지 자체를 임베딩하는 것`이 아니라 `Hub에서 링크된 실제 페이지 본문을 수집해서 chunk 단위로 임베딩하는 것`이다.

### 이유
- 현재 코드의 수집 흐름은 DB ID를 직접 넣는 구조다.
- 현재 크롤러는 `child_page`, `child_database` 재귀 중심이라 링크형 Hub를 제대로 처리하지 못한다.
- 지금 필요한 것은 "하위 페이지 트리 순회"가 아니라 "Hub 본문에 있는 Notion 페이지 링크를 파싱해서 대상 page_id를 수집"하는 discovery 계층이다.

### 따라서
- 기존 코드 일부는 참고만 하고, 구조는 재편한다.
- 우선순위는 다음 순서로 둔다.
1. 링크형 Hub discovery
2. 개별 페이지 markdown 수집
3. heading-aware chunking
4. embedding + Qdrant 적재
5. 검색 API
6. 답변 API
7. webhook 기반 증분 동기화

---

## 이번 작업의 목표

### 목표
- Notion Hub 페이지를 읽어서 대상 페이지 목록을 자동 수집한다.
- 각 대상 페이지의 실제 본문을 가져와 chunk 단위로 임베딩한다.
- 임베딩 결과를 Qdrant 단일 컬렉션 `notion_chunks`에 적재한다.
- `/search`, `/ask` API를 제공한다.
- 이후 Notion webhook으로 변경 페이지만 재색인할 수 있게 확장 가능하게 만든다.

### MVP 범위
- Dense embedding only
- Qdrant single collection only
- Hub 1개 기준 backfill
- `/search`, `/ask`
- 수동 reindex CLI

### MVP에서 제외
- Hybrid retrieval
- Reranker
- 다중 컬렉션 전략
- 복잡한 권한 시스템
- 운영 배포 자동화
- 캐시 계층

---

## 현재 코드베이스 처리 방침

### 유지
- `requirements.txt`는 베이스로 참고 가능
- OpenAI embedding, Qdrant 연동 코드는 일부 재사용 가능

### 폐기 또는 대폭 축소
- `reload_data.py`
- `notion/crawler.py`
- `notion_rag.py`
- DB ID 하드코딩 구조

### 이유
- 기존 구조는 "DB 직접 수집"에 맞춰져 있고, "Hub 링크 기반 discovery"에는 맞지 않는다.
- 앞으로의 기준 엔티티는 `database_id`가 아니라 `hub_id`, `page_id`다.

---

## 권장 디렉토리 구조

```text
db/
  app/
    api/
      main.py
      routes/
        search.py
        ask.py
        admin.py
        webhook_notion.py
    core/
      config.py
      logging.py
    notion/
      client.py
      discovery.py
      markdown.py
      models.py
    chunking/
      chunker.py
      models.py
    embeddings/
      client.py
    qdrant/
      client.py
      payloads.py
      store.py
    rag/
      retrieve.py
      answer.py
      prompt.py
    services/
      ingest_page.py
      backfill_hub.py
      sync_page.py
    utils/
      hashes.py
      ids.py
  scripts/
    backfill_hub.py
    reindex_page.py
    bootstrap_qdrant.py
  tests/
    test_discovery.py
    test_chunker.py
    test_ingest_page.py
    test_search.py
    test_webhook.py
  docs/
    notion_qdrant_rag_blueprint.md
    notion_qdrant_rag_execution_plan.md
  .env.example
  requirements.txt
  README.md
```

### 메모
- 기존 파일을 완전히 즉시 삭제할 필요는 없다.
- 먼저 새 구조를 만들고, 구현이 올라온 뒤 기존 파일을 정리하는 순서가 안전하다.

---

## 데이터 흐름

```text
Notion Hub Page
  -> discovery: page mention / link_to_page / notion URL 파싱
  -> unique page_id 목록 생성
  -> page metadata 조회
  -> page markdown 조회
  -> markdown normalize
  -> heading-aware chunking
  -> embedding 생성
  -> Qdrant upsert
  -> /search, /ask 에서 retrieval
```

---

## 핵심 설계 결정

### 1. Hub는 discovery 전용
- Hub 페이지 자체는 문답의 근거 문서가 아니다.
- Hub는 "무슨 페이지를 수집할지 알려주는 인덱스 페이지"로만 본다.

### 2. 임베딩 대상은 page 본문 chunk
- page title만 넣지 않는다.
- 링크만 넣지 않는다.
- 각 page의 markdown 본문을 section-aware chunk로 나눠 임베딩한다.

### 3. collection은 하나로 시작
- 컬렉션명: `notion_chunks`
- payload 필드로 `hub_id`, `team`, `page_id`, `status` 등을 구분한다.
- 처음부터 multi-collection으로 가지 않는다.

### 4. page_id를 엔티티 기준키로 사용
- 제목, URL, breadcrumb는 변할 수 있다.
- 삭제/이동/제목 수정이 있어도 기준 식별자는 `page_id`로 유지한다.

### 5. full reindex 단위를 page로 고정
- 부분 patch보다 `page 전체 재색인`이 단순하고 운영 안정성이 높다.
- `page.content_updated` 이벤트가 오면 해당 page의 기존 chunk를 제거하고 다시 적재한다.

---

## 환경 변수 설계

`.env.example`에 아래 값을 정의한다.

```bash
NOTION_TOKEN=
NOTION_VERSION=2025-09-03
NOTION_HUB_PAGE_ID=
NOTION_HUB_ID=hub_main
DEFAULT_TEAM=

OPENAI_API_KEY=
EMBEDDING_MODEL=text-embedding-3-small
LLM_MODEL=gpt-4o-mini

QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=
QDRANT_COLLECTION=notion_chunks

CHUNK_TARGET_TOKENS=600
CHUNK_OVERLAP_TOKENS=80
SEARCH_TOP_K=8
```

### 메모
- 실제 Notion API 버전은 구현 시점의 공식 문서 기준으로 한 곳에서 관리한다.
- 현재 `.env`에 민감정보가 이미 있으므로, 커밋 전 노출 여부를 반드시 점검하고 필요하면 키를 교체한다.

---

## Qdrant 설계

### collection
- 이름: `notion_chunks`
- distance: `Cosine`
- embedding dimension: `text-embedding-3-small` 기준 1536

### payload 기본 필드
```json
{
  "source": "notion",
  "hub_id": "hub_main",
  "team": "marketing",
  "status": "active",
  "page_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "page_url": "https://www.notion.so/...",
  "page_title": "2026 Q2 Campaign Review",
  "breadcrumb": "Hub / Marketing / Campaigns / Q2 Review",
  "section_path": "Findings > Reddit feedback",
  "chunk_index": 3,
  "last_edited_time": "2026-04-03T01:23:45.000Z",
  "content_sha256": "....",
  "text": "chunk content"
}
```

### payload index
- `source`
- `hub_id`
- `team`
- `page_id`
- `status`

### point id 생성 규칙
- `uuid5(namespace, f"{hub_id}:{page_id}:{chunk_index}:{content_sha256}")`
- 이유: 동일 page 재색인 시 중복 방지와 추적이 쉽다.

---

## Notion 수집 전략

### 1. Discovery
Hub 페이지에서 아래 세 가지를 모두 수집 대상으로 본다.
- `link_to_page`
- page mention
- 본문 텍스트/markdown 안의 `notion.so` 또는 `www.notion.so` URL

### 2. Discovery 결과 정규화
- URL에서 page_id 파싱
- UUID 형식 정규화
- 중복 제거
- 자기 자신인 Hub 페이지는 제외

### 3. Page fetch
- page metadata 조회
- page markdown 조회
- markdown만으로 부족한 경우 block API 보강 여지 남김

### 4. 예외 처리
- 권한 없는 페이지는 실패 목록으로 기록
- 삭제된 페이지는 skip 또는 `status=deleted`
- markdown fetch 실패 시 page metadata만 넣고 넘어가지 않는다

---

## Chunking 전략

### 목표
- section-aware chunk
- retrieval 품질을 위해 heading 경계를 최대한 유지

### 규칙
- H1/H2/H3 기준으로 `section_path`를 만든다.
- 큰 section만 splitter로 추가 분할한다.
- 목표 크기: 400~800 tokens
- overlap: 50~100 tokens

### chunk payload에 반드시 포함
- `page_title`
- `breadcrumb`
- `section_path`
- `page_url`
- `page_id`
- `text`

### 구현 메모
- markdown 파서를 과하게 무겁게 시작할 필요는 없다.
- 1차는 heading split + recursive text split으로 충분하다.
- 표, 코드블록, bullet list가 깨지지 않도록 normalize 규칙만 명확히 둔다.

---

## API 설계

### POST `/search`
역할:
- query embedding
- Qdrant top-k 검색
- 필요시 `team`, `hub_id` 필터 적용

응답:
- score
- page_title
- page_url
- section_path
- text preview

### POST `/ask`
역할:
- query embedding
- Qdrant 검색
- 상위 chunk dedupe
- retrieved chunk를 근거로 LLM 답변 생성

응답 규칙:
- 링크만 던지지 않는다.
- 먼저 내용을 설명한다.
- 마지막에 출처 목록을 붙인다.
- 근거 부족 시 부족하다고 명시한다.

### POST `/admin/reindex/page`
역할:
- 특정 page_id 전체 재색인

### POST `/webhooks/notion`
역할:
- Notion webhook 수신
- page.created / page.content_updated / page.properties_updated / page.deleted 처리

---

## 구현 순서

## Phase 1. 새 프로젝트 골격 생성
산출물:
- `app/` 기반 구조
- 공통 설정 모듈
- 로깅 초기화
- `.env.example`

완료 기준:
- FastAPI 앱이 뜬다.
- 설정 로딩이 된다.

## Phase 2. Notion client + discovery
산출물:
- `app/notion/client.py`
- `app/notion/discovery.py`
- `discover_pages_from_hub(hub_page_id)` 구현

완료 기준:
- 허브 페이지를 넣으면 대상 page_id 목록이 나온다.
- `link_to_page`, mention, notion URL 케이스를 모두 처리한다.

## Phase 3. markdown fetch + normalize
산출물:
- `app/notion/markdown.py`
- `get_page_markdown(page_id)`
- `get_page_metadata(page_id)`
- normalize 함수

완료 기준:
- page_id를 넣으면 정리된 markdown과 metadata가 나온다.

## Phase 4. chunker
산출물:
- `app/chunking/chunker.py`
- `chunk_markdown(page_title, breadcrumb, markdown)`

완료 기준:
- heading-aware chunk 목록이 나오고 `section_path`가 보존된다.

## Phase 5. embedding + Qdrant bootstrap
산출물:
- `app/embeddings/client.py`
- `app/qdrant/store.py`
- `ensure_collection()`
- `upsert_page_chunks()`

완료 기준:
- collection 생성
- payload index 생성
- 샘플 chunk upsert 가능

## Phase 6. ingest pipeline
산출물:
- `app/services/ingest_page.py`
- `app/services/backfill_hub.py`
- `scripts/backfill_hub.py`
- `scripts/reindex_page.py`

완료 기준:
- 허브 기준 전체 backfill 가능
- 특정 page 재색인 가능

## Phase 7. 검색 API
산출물:
- `/search`
- retrieval 로직

완료 기준:
- 질의 시 관련 chunk가 반환된다.

## Phase 8. 답변 API
산출물:
- `/ask`
- prompt policy
- source formatting

완료 기준:
- 근거 기반 답변과 출처가 함께 반환된다.

## Phase 9. webhook
산출물:
- `/webhooks/notion`
- 이벤트 검증
- page 단위 재색인 처리

완료 기준:
- 수정 이벤트 발생 시 해당 page만 재색인된다.

---

## 월요일 시작 체크리스트

### 시작 직후 할 일
1. 새 구조를 만들지, 기존 폴더를 이동 보관할지 결정
2. `.env.example`부터 정리
3. `app/notion/client.py` 작성
4. `app/notion/discovery.py` 작성
5. 실제 Hub 페이지로 discovery 테스트

### 첫날 목표
- "허브에서 page_id 목록을 뽑는다"
- "개별 page markdown을 가져온다"
- "chunk 후 Qdrant에 올린다"

### 첫날 끝나기 전 확인할 것
- Qdrant `notion_chunks`에 실제 point가 들어갔는가
- 같은 page 재실행 시 중복이 쌓이지 않는가
- 권한 없는 page는 실패 로그에 남는가

---

## 테스트 전략

### 최소 테스트
- `test_discovery.py`
  - Hub 내 `link_to_page` 파싱
  - mention 파싱
  - notion URL 파싱
  - dedupe 검증

- `test_chunker.py`
  - heading split
  - section_path 생성
  - 긴 본문 분할

- `test_ingest_page.py`
  - page metadata + markdown -> chunks -> embeddings -> payload

- `test_search.py`
  - upsert 후 top-k 반환 smoke test

- `test_webhook.py`
  - `page.content_updated`
  - `page.deleted`

### 수동 검증
- 실제 Hub page로 backfill
- 특정 질의로 `/search`
- 같은 질의로 `/ask`

---

## 구현 시 주의사항

### 1. 권한
- Hub가 보여도 링크된 페이지가 integration에 공유되지 않았을 수 있다.
- discovery 성공과 ingest 성공은 별개다.

### 2. 삭제 처리
- page 삭제 시 이전 chunk가 남지 않게 해야 한다.
- 초기는 hard delete보다 `page_id` 기준 전체 삭제 후 재적재 패턴이 단순하다.

### 3. 제목/경로 변경
- 제목, URL, breadcrumb는 변한다.
- 저장과 삭제 기준은 반드시 `page_id`로 잡는다.

### 4. markdown 품질
- 표, 토글, 코드블록, 리스트가 markdown으로 완전히 예쁘게 오지 않을 수 있다.
- 처음부터 완벽 복원보다 retrieval 품질이 우선이다.

### 5. 과도한 범위 확장 금지
- 처음부터 multi-hub, multi-tenant, reranker까지 넣지 않는다.
- Hub 1개, single collection, dense retrieval로 닫는다.

---

## 권장 실행 커맨드 예시

```bash
# 의존성 설치
pip install -r requirements.txt

# Qdrant 컬렉션 초기화
python scripts/bootstrap_qdrant.py

# Hub 전체 backfill
python scripts/backfill_hub.py

# 특정 page 재색인
python scripts/reindex_page.py --page-id <NOTION_PAGE_ID>

# API 실행
uvicorn app.api.main:app --reload --port 8000
```

---

## 완료 기준

아래를 만족하면 MVP 완료로 본다.

1. Hub 페이지에서 대상 page_id를 자동 발견한다.
2. 각 page markdown을 가져와 chunk 단위로 임베딩한다.
3. Qdrant `notion_chunks`에 저장된다.
4. `/search`가 실제 본문 chunk를 반환한다.
5. `/ask`가 링크만 던지지 않고 본문 기반 답변을 준다.
6. 특정 page 재색인이 가능하다.
7. webhook 이벤트를 받을 수 있는 구조가 준비된다.

---

## 바로 실행할 작업 지시

월요일 구현 시작 시 아래 순서로 진행한다.

1. 기존 DB 중심 로더를 신규 구조로 대체할 새 폴더를 만든다.
2. `app/notion/client.py`와 `app/notion/discovery.py`를 먼저 구현한다.
3. Hub 실제 페이지로 discovery를 검증한다.
4. `get_page_markdown()`과 chunker를 구현한다.
5. Qdrant bootstrap과 ingest pipeline을 붙인다.
6. `scripts/backfill_hub.py`로 첫 full ingest를 돌린다.
7. 그 다음 `/search`, `/ask`를 붙인다.
8. 마지막으로 webhook을 붙인다.

이 순서를 바꾸지 않는 것이 좋다. 현재 목적은 "링크형 Hub -> 본문 임베딩 -> Qdrant 적재"를 가능한 한 빨리 안정화하는 것이다.
