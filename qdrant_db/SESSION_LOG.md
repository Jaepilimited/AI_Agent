# Notion RAG 시스템 리팩토링 세션 기록

**날짜**: 2025-02-09
**작업 폴더**: `Craver-chatbot/db/`

---

## 1. 리팩토링 개요

기존 단일 파일(`notion_rag.py`, `main.py`)을 모듈화된 구조로 리팩토링.

### 새 디렉토리 구조

```
Craver-chatbot/db/
├── config/
│   ├── __init__.py
│   └── settings.py           # Pydantic Settings (환경변수 관리)
├── notion/
│   ├── __init__.py
│   ├── client.py             # Notion API 래퍼
│   └── crawler.py            # 재귀 크롤링 + 블록 파싱
├── embedding/
│   ├── __init__.py
│   ├── chunker.py            # RecursiveCharacterTextSplitter 청킹
│   └── embedder.py           # 배치 임베딩 (100개씩)
├── vector_store/
│   ├── __init__.py
│   └── qdrant_store.py       # Qdrant 연동
├── scripts/
│   └── reload_data.py        # 데이터 로드 CLI
├── main.py                   # RAG 챗봇
├── requirements.txt
└── _backup/                  # 기존 코드 백업
```

---

## 2. 주요 변경 사항

### 2.1 환경변수 (.env)

```env
# Notion
NOTION_TOKEN=ntn_xxx
ROOT_PAGE_ID=d86180c9236541d6b154dcb4c4143f23  # 페이지 ID 또는 데이터베이스 ID

# OpenAI
OPENAI_API_KEY=sk-xxx

# Qdrant
QDRANT_HOST=localhost
QDRANT_PORT=6333
QDRANT_COLLECTION=notion_db_JP  # 컬렉션명 여기서 변경
QDRANT_API_KEY=xxx
```

### 2.2 청킹 개선

| 항목 | Before | After |
|------|--------|-------|
| 방식 | 1000자 단순 슬라이싱 | RecursiveCharacterTextSplitter |
| 크기 | 1000자 | 800자 + 100자 오버랩 |
| 구분자 | 없음 | `["\n\n", "\n", ". ", " "]` |

### 2.3 임베딩 배치 처리

- Before: 청크당 개별 API 호출
- After: 100개씩 배치 처리 (비용/속도 최적화)

### 2.4 데이터베이스 ID 지원

크롤러가 **페이지 ID**와 **데이터베이스 ID** 모두 자동 감지:
- 페이지 ID → 페이지 크롤링
- 데이터베이스 ID → DB 내 항목 크롤링

---

## 3. 발생한 이슈 및 해결

### 3.1 notion-client 라이브러리 버그

**문제**: `client.databases.query()` 메서드 없음 (v2.7.0)

**해결**: `requests` 라이브러리로 직접 HTTP 요청
```python
# notion/client.py
url = f"{NOTION_API_BASE}/databases/{formatted_id}/query"
response = requests.post(url, headers=headers, json=body)
```

### 3.2 Qdrant 클라이언트 버전 차이

**문제**: `client.search()` → `client.query_points()`로 변경됨 (v1.16.2)

**해결**: `query_points` 메서드 사용
```python
response = self._client.query_points(
    collection_name=self._collection_name,
    query=query_vector,
    limit=top_k,
    ...
)
```

### 3.3 Windows 콘솔 이모지 인코딩

**문제**: `📄` 같은 이모지 출력 시 `cp949` 인코딩 에러

**해결**: 이모지를 텍스트로 대체 (`[PAGE]`, `[DB]`)

---

## 4. 사용법

### 데이터 수집 (Notion → Qdrant)

```bash
cd Craver-chatbot/db

# 전체 데이터 로드
python scripts/reload_data.py --reload

# 빠른 테스트 (3페이지, DB 건너뜀)
python scripts/reload_data.py --quick
```

### RAG 챗봇 실행

```bash
python main.py
```

- `.env`의 `QDRANT_COLLECTION` 값 기준으로 검색
- 질문 입력 → 벡터 검색 → GPT 답변 생성

---

## 5. 참고: Notion API 구조

```
/v1/blocks/{block_id}/children  → 첫 번째 레벨 자식만 반환 (재귀 필요)
/v1/databases/{database_id}/query  → DB 내 페이지 목록 조회
/v1/pages/{page_id}  → 페이지 정보 조회
```

**핵심**: Notion API는 한 번에 전체 하위 구조를 반환하지 않음. 재귀적 크롤링 필수.

---

## 6. 추가 의존성

```txt
langchain-text-splitters>=0.0.1
pydantic-settings>=2.0.0
```

---

## 7. 다음 작업 시 참고

1. `.env`에서 `ROOT_PAGE_ID`, `QDRANT_COLLECTION` 확인
2. 데이터 수집: `python scripts/reload_data.py --reload`
3. 챗봇 테스트: `python main.py`
4. Qdrant 대시보드: http://localhost:6333/dashboard

---

# 세션 2: 다중 소스 지원 및 단일 컬렉션 구조

**날짜**: 2025-02-11
**작업 폴더**: `Craver-chatbot/db/`

---

## 1. 변경 개요

기존 단일 데이터베이스 수집 방식에서 **다중 소스 지원 + 단일 컬렉션 구조**로 개선.

### 변경 이유
- 100개 이상의 Notion 데이터베이스를 개별 컬렉션으로 관리하면 검색 시 API 호출 과다
- 단일 컬렉션에 `source` 필드로 구분하여 효율적 검색 가능
- 개별 소스 단위로 독립적 수집/삭제/갱신 가능

---

## 2. 구조 변경

### 2.1 컬렉션 구조

```
┌─────────────────────────────────────────────┐
│         notion_skin1004 (단일 컬렉션)         │
│                                             │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐       │
│  │ DB-JP   │ │ EAST-*  │ │ KBT-*   │ ...   │
│  └─────────┘ └─────────┘ └─────────┘       │
│                                             │
│  payload.source 로 구분                      │
└─────────────────────────────────────────────┘
```

### 2.2 Payload 스키마

```python
{
    "source": "DB-JP",              # 소스 구분 (필수)
    "database_id": "d86180c9...",   # Notion DB ID
    "page_id": "...",
    "page_title": "...",
    "section_title": "...",
    "breadcrumb_path": "...",
    "text": "...",
    "text_preview": "...",
    "url": "...",
    "chunk_index": 0
}
```

---

## 3. 주요 변경 사항

### 3.1 설정 변경 (`config/settings.py`)

```python
# 기본 컬렉션명 변경
collection_name: str = Field(default="notion_skin1004", alias="QDRANT_COLLECTION")
```

### 3.2 수집 대상 정의 (`reload_data.py`)

```python
DATABASE_TARGETS = [
    {"source": "DB-JP", "database_id": "d86180c9...", "description": "재필님 개인 페이지"},
    {"source": "DB-tablet", "database_id": "2532b428...", "description": "법인 태블릿 사용법"},
    {"source": "EAST-guide-archive", "database_id": "2e62b428...", "description": "EAST 2팀 가이드"},
    # ...
]
```

### 3.3 Qdrant Store 확장 (`vector_store/qdrant_store.py`)

| 메서드 | 설명 |
|--------|------|
| `ensure_collection()` | 컬렉션 없으면 생성 (있으면 유지) |
| `delete_by_source(source)` | 특정 source 데이터만 삭제 |
| `count_by_source(source)` | 특정 source 포인트 수 조회 |
| `upsert_points_with_ids()` | UUID 지정하여 저장 |

### 3.4 OpenAI API 파라미터 수정 (`main.py`)

```python
# 최신 모델 호환성
max_tokens=1000  →  max_completion_tokens=1000
```

---

## 4. CLI 명령어

```bash
cd Craver-chatbot/db

# 소스 목록 + 포인트 수 확인
python reload_data.py --list

# 특정 소스 데이터 조회
python reload_data.py --show DB-JP

# 특정 소스만 수집 (기존 삭제 → 재수집)
python reload_data.py --source DB-JP

# 새 소스만 수집 (포인트 0인 것만)
python reload_data.py --new

# 전체 소스 수집
python reload_data.py --all

# 특정 소스 삭제
python reload_data.py --delete-source DB-JP

# LLM 챗봇 실행
python main.py
```

---

## 5. 수집 동작 방식

| 상황 | 동작 |
|------|------|
| 같은 source 재수집 | 기존 삭제 → 새로 수집 (교체) |
| 새 source 추가 | 기존 유지 + 새 데이터 추가 (누적) |
| `--new` 실행 | 포인트 0인 소스만 수집 |

---

## 6. 다음 작업 시 참고

1. 새 소스 추가: `reload_data.py`의 `DATABASE_TARGETS`에 추가
2. 데이터 수집: `python reload_data.py --new` (새 소스만) 또는 `--source XX`
3. 상태 확인: `python reload_data.py --list`
4. 챗봇 테스트: `python main.py`
5. Qdrant 대시보드: http://localhost:6333/dashboard
