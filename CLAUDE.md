# CLAUDE.md — SKIN1004 Enterprise AI System

## Project Identity

- **Project**: SKIN1004 Enterprise AI (Text-to-SQL + Agentic RAG)
- **Language**: Python 3.11+
- **Working Directory**: `C:\Users\DB_PC\Desktop\python_bcj\AI_Agent`
- **GCP Project**: `skin1004-319714`

## Architecture Overview

Hybrid AI system with 4 routing paths:
1. **Text-to-SQL Agent** → 매출/데이터 질문 → BigQuery 직접 실행
2. **RAG Agent** → 문서/정책 질문 → BigQuery Vector Search
3. **Direct LLM** → 일반/간단한 질문 → 즉시 응답
4. **Multi-Agent** → 복합 질문 → SQL + RAG 결합

```
User Query → FastAPI → LangGraph Router → [SQL | RAG | LLM | Multi] → Response
                                    ↕
                              Open WebUI (Frontend)
```

## Tech Stack (Mandatory)

| Layer | Technology | Notes |
|-------|-----------|-------|
| LLM | Gemini 2.0 Flash | `google-genai` SDK, NOT openai |
| Embedding | BGE-M3 (768-dim) | `FlagEmbedding` or `sentence-transformers` |
| Orchestration | LangGraph | `langgraph`, stateful workflows |
| API Server | FastAPI | OpenAI-compatible `/v1/chat/completions` |
| Database | BigQuery | Sales data + Vector Search + QA logs |
| Doc Parser | Docling | PDF/HWP/PPT → markdown |
| Web Search | Tavily API | CRAG fallback |
| Frontend | Open WebUI | Docker, connects to FastAPI |

## Project Structure

```
AI_Agent/
├── CLAUDE.md                    # ← 이 파일
├── .env                         # 환경변수 (API keys)
├── requirements.txt
├── pyproject.toml
├── README.md
│
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI 진입점
│   ├── config.py                # 설정 관리 (pydantic-settings)
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── routes.py            # OpenAI-compatible endpoints
│   │   └── middleware.py        # CORS, auth, logging
│   │
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── router.py            # Query Analyzer (intent detection)
│   │   ├── sql_agent.py         # Text-to-SQL Agent
│   │   ├── rag_agent.py         # Agentic RAG Agent
│   │   └── graph.py             # LangGraph workflow definition
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── llm.py               # Gemini 2.0 Flash client
│   │   ├── embeddings.py        # BGE-M3 embedding
│   │   ├── bigquery.py          # BigQuery client (query + vector search)
│   │   └── security.py          # SQL validation, whitelist
│   │
│   ├── rag/
│   │   ├── __init__.py
│   │   ├── parser.py            # Docling document parser
│   │   ├── chunker.py           # Hybrid chunking (semantic + hierarchical)
│   │   ├── indexer.py           # BigQuery vector indexing
│   │   └── retriever.py         # Vector search + reranking
│   │
│   └── models/
│       ├── __init__.py
│       ├── schemas.py           # Pydantic request/response models
│       └── state.py             # LangGraph state definitions
│
├── scripts/
│   ├── setup_bigquery.py        # BigQuery 테이블 생성 스크립트
│   ├── index_documents.py       # 문서 인덱싱 배치
│   └── test_sql_agent.py        # SQL agent 테스트
│
├── prompts/
│   ├── query_analyzer.txt       # 질문 분류 프롬프트
│   ├── sql_generator.txt        # SQL 생성 프롬프트
│   ├── rag_generator.txt        # RAG 답변 생성 프롬프트
│   └── sql_validator.txt        # SQL 검증 프롬프트
│
├── tests/
│   ├── test_sql_agent.py
│   ├── test_rag_agent.py
│   └── test_router.py
│
└── docker/
    ├── Dockerfile               # FastAPI server
    └── docker-compose.yml       # FastAPI + Open WebUI
```

## Environment Variables (.env)

```env
# GCP
GCP_PROJECT_ID=skin1004-319714
GOOGLE_APPLICATION_CREDENTIALS=C:/json_key/skin1004-319714-60527c477460.json

# Gemini
GEMINI_MODEL=gemini-2.0-flash
GEMINI_API_KEY=<your-gemini-api-key>

# BigQuery
BQ_DATASET_SALES=Sales_Integration
BQ_TABLE_SALES=SALES_ALL_Backup
BQ_DATASET_RAG=AI_RAG
BQ_TABLE_EMBEDDINGS=rag_embeddings
BQ_TABLE_QA_LOGS=qa_logs

# Embedding
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DIM=768

# Tavily (CRAG fallback)
TAVILY_API_KEY=<your-tavily-api-key>

# Server
HOST=0.0.0.0
PORT=8000
```

## BigQuery Schemas

### Sales Table (기존)
- **Full path**: `skin1004-319714.Sales_Integration.SALES_ALL_Backup`
- Contains: 다국적 플랫폼(Shopee, Lazada, TikTok Shop, Amazon) 매출 데이터
- Access: READ-ONLY (SELECT만 허용)

### RAG Embeddings Table (신규 생성)
```sql
CREATE TABLE IF NOT EXISTS `skin1004-319714.AI_RAG.rag_embeddings` (
  id STRING NOT NULL,
  content STRING,
  metadata JSON,
  embedding ARRAY<FLOAT64>,  -- BGE-M3 768-dim
  source_type STRING,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
);
```

### QA Logs Table (신규 생성)
```sql
CREATE TABLE IF NOT EXISTS `skin1004-319714.AI_RAG.qa_logs` (
  id STRING NOT NULL,
  user_id STRING,
  query STRING,
  route_type STRING,          -- text_to_sql | rag | direct_llm | multi_agent
  generated_sql STRING,
  retrieved_docs ARRAY<STRING>,
  answer STRING,
  feedback STRING,            -- thumbs_up | thumbs_down | null
  latency_ms INT64,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
);
```

## Implementation Phases (실행 순서)

### Phase 1: 프로젝트 초기화 및 인프라
```
Task: 프로젝트 스캐폴딩, 의존성 설치, GCP 연결 확인

Steps:
1. requirements.txt 생성 및 의존성 설치
2. app/config.py — pydantic-settings 기반 환경변수 관리
3. app/core/bigquery.py — BigQuery 클라이언트 (연결 테스트)
4. app/core/llm.py — Gemini 2.0 Flash 클라이언트 (응답 테스트)
5. app/core/embeddings.py — BGE-M3 로드 및 임베딩 테스트
6. scripts/setup_bigquery.py — AI_RAG 데이터셋/테이블 생성
```

### Phase 2: Text-to-SQL Agent
```
Task: 자연어 → BigQuery SQL 변환 및 실행 파이프라인

Steps:
1. app/core/security.py — SQL 안전장치
   - SELECT ONLY 검증 (INSERT/UPDATE/DELETE/DROP 차단)
   - 테이블 화이트리스트 검증
   - 쿼리 타임아웃 30초
   - 결과 행 제한 1,000행
2. prompts/sql_generator.txt — SQL 생성 프롬프트
   - SALES_ALL_Backup 스키마 포함
   - BigQuery SQL 방언 명시
   - 예시 질문-SQL 쌍 포함
3. prompts/sql_validator.txt — SQL 검증 프롬프트
4. app/agents/sql_agent.py — LangGraph 기반 SQL 에이전트
   - generate_sql → validate_sql → execute_sql → format_answer
5. tests/test_sql_agent.py — 테스트
```

### Phase 3: Agentic RAG Pipeline
```
Task: 문서 파싱, 임베딩, 벡터 검색, 에이전틱 RAG 워크플로우

Steps:
1. app/rag/parser.py — Docling 문서 파서 (PDF, HWP, PPT → markdown)
2. app/rag/chunker.py — 하이브리드 청킹
   - Semantic Chunking (의미 기반 분할)
   - Hierarchical Chunking (Parent-Child 관계 유지)
3. app/rag/indexer.py — BigQuery 벡터 인덱싱
4. app/rag/retriever.py — Vector Search + 관련성 평가
5. app/agents/rag_agent.py — LangGraph 기반 RAG 에이전트
   - Adaptive RAG (난이도별 라우팅)
   - Corrective RAG (관련성 낮으면 Tavily 웹검색)
   - Self-Reflective RAG (환각 체크 → 재시도)
6. scripts/index_documents.py — 문서 인덱싱 배치 스크립트
```

### Phase 4: Query Router + FastAPI + Open WebUI
```
Task: 하이브리드 라우팅, API 서버, 프론트엔드 연동

Steps:
1. prompts/query_analyzer.txt — 질문 분류 프롬프트
   - 4가지 route_type: text_to_sql | rag | direct_llm | multi_agent
2. app/agents/router.py — Query Analyzer
3. app/agents/graph.py — LangGraph 통합 워크플로우
   - router → [sql_agent | rag_agent | llm | multi] → response
4. app/api/routes.py — OpenAI-compatible API endpoints
   - POST /v1/chat/completions (streaming support)
   - POST /v1/models
5. app/api/middleware.py — CORS, 인증, 로깅
6. app/main.py — FastAPI 앱 조립
7. docker/docker-compose.yml — FastAPI + Open WebUI
```

### Phase 5: 테스트 및 최적화
```
Task: 평가, 부하 테스트, 로깅 파이프라인

Steps:
1. RAGAS 평가 (Retrieval + Generation 품질)
2. 동시 접속 부하 테스트 (locust 활용)
3. QA 로그 → BigQuery 적재 파이프라인
4. 사용자 피드백 수집 및 분석
```

## Code Style & Conventions

- **Type hints**: 모든 함수에 타입 힌트 필수
- **Docstrings**: Google style docstrings
- **Async**: FastAPI 핸들러는 async, BigQuery 호출은 동기 (스레드풀)
- **Error handling**: 모든 외부 호출에 try-except, 사용자에게는 friendly 에러 메시지
- **Logging**: `structlog` 사용, JSON 포맷
- **Config**: 하드코딩 금지, 모두 .env 또는 config.py에서 관리
- **한국어**: 사용자 대면 메시지는 한국어, 코드/로그/변수명은 영어

## SQL Safety Rules (Critical)

Text-to-SQL Agent가 생성하는 모든 SQL은 실행 전 반드시 다음을 검증:

```python
ALLOWED_STATEMENTS = {"SELECT"}
BLOCKED_KEYWORDS = {"INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE", "MERGE"}
ALLOWED_TABLES = [
    "skin1004-319714.Sales_Integration.SALES_ALL_Backup",
    # 추가 허용 테이블은 여기에
]
MAX_TIMEOUT_SECONDS = 30
MAX_RESULT_ROWS = 1000
```

## LangGraph State Schema

```python
from typing import TypedDict, Optional, List, Literal

class AgentState(TypedDict):
    query: str                                          # 사용자 원본 질문
    route_type: Literal["text_to_sql", "rag", "direct_llm", "multi_agent"]
    
    # Text-to-SQL
    generated_sql: Optional[str]
    sql_valid: Optional[bool]
    sql_result: Optional[list]
    
    # RAG
    retrieved_docs: Optional[List[str]]
    doc_relevance: Optional[Literal["yes", "no"]]
    
    # Output
    answer: str
    needs_retry: bool
    retry_count: int
```

## LangGraph Nodes

| Node | Function | Description |
|------|----------|-------------|
| `analyze_query` | router.py | 질문 의도 분류 → route_type 결정 |
| `generate_sql` | sql_agent.py | 자연어 → BigQuery SQL 생성 |
| `validate_sql` | security.py | SQL 보안 검증 (SELECT ONLY, whitelist) |
| `execute_sql` | bigquery.py | BigQuery 실행, 결과 반환 |
| `retrieve_docs` | retriever.py | Vector Search 실행 |
| `grade_documents` | rag_agent.py | 검색 결과 관련성 평가 |
| `rewrite_query` | rag_agent.py | 질문 재작성 (관련성 낮을 시) |
| `web_search` | rag_agent.py | Tavily 웹검색 (CRAG fallback) |
| `generate_answer` | graph.py | 최종 답변 생성 |
| `reflect` | graph.py | 환각 체크, 품질 검증 |
| `log_qa` | graph.py | QA 로그 BigQuery 적재 |

## Key Dependencies (requirements.txt)

```
# Core
fastapi>=0.115.0
uvicorn[standard]>=0.34.0
pydantic-settings>=2.0

# LLM & AI
google-genai>=1.0.0
langgraph>=0.2.0
langchain-core>=0.3.0
langchain-google-genai>=2.0.0

# Embedding
sentence-transformers>=3.0.0
FlagEmbedding>=1.2.0

# BigQuery
google-cloud-bigquery>=3.25.0
google-cloud-bigquery-storage>=2.25.0

# RAG
docling>=2.0.0
tavily-python>=0.5.0

# Utilities
structlog>=24.0.0
python-dotenv>=1.0.0
httpx>=0.27.0

# Testing
pytest>=8.0.0
pytest-asyncio>=0.24.0
locust>=2.30.0
```

## Prompts Reference

### Query Analyzer Prompt (요약)
```
당신은 SKIN1004 AI 시스템의 질문 분류기입니다.
사용자 질문을 분석하여 다음 4가지 중 하나로 분류하세요:

1. text_to_sql — 매출, 수량, 금액, 순위, 비교 등 숫자/데이터 관련
2. rag — 정책, 프로세스, 매뉴얼, 가이드라인 등 문서 관련  
3. direct_llm — 일반 상식, 용어 설명, 인사말 등
4. multi_agent — 데이터 + 문서 분석이 모두 필요한 복합 질문

JSON으로 응답: {"route_type": "...", "reasoning": "..."}
```

### SQL Generator Prompt (요약)
```
당신은 BigQuery SQL 전문가입니다.
사용자의 자연어 질문을 BigQuery SQL로 변환하세요.

[SCHEMA]
테이블: skin1004-319714.Sales_Integration.SALES_ALL_Backup
컬럼: (SALES_ALL_Backup 스키마 삽입)

[RULES]
- SELECT 문만 사용
- BigQuery 문법 준수 (SAFE_DIVIDE, FORMAT_DATE 등)
- 날짜 필터 시 PARSE_DATE/DATE 함수 사용
- 결과는 1000행 이내로 LIMIT
- 한국어 컬럼값 매핑 테이블 참조

SQL만 반환, 설명 불필요.
```

## Useful Commands for Claude Code

```bash
# Phase 1 시작
claude "Phase 1을 시작해줘. requirements.txt 생성하고 config.py, bigquery.py, llm.py, embeddings.py 구현해줘"

# Phase 2 시작  
claude "Phase 2를 시작해줘. SQL 안전장치, SQL 생성 프롬프트, sql_agent.py를 LangGraph로 구현해줘"

# Phase 3 시작
claude "Phase 3를 시작해줘. Docling 파서, 하이브리드 청커, 벡터 인덱서, RAG 에이전트를 구현해줘"

# Phase 4 시작
claude "Phase 4를 시작해줘. Query Router, LangGraph 통합 그래프, FastAPI 서버, docker-compose 구현해줘"

# 특정 파일 수정
claude "app/agents/sql_agent.py에서 SQL 생성 프롬프트를 개선해줘. 날짜 필터링 예시를 추가해"

# 테스트 실행
claude "tests/ 폴더의 모든 테스트를 실행하고 결과를 알려줘"

# 디버깅
claude "Text-to-SQL Agent에서 '태국 쇼피 1월 매출'이라고 물어보면 에러가 나. 원인 찾아줘"
```

## Important Notes

- **절대 금지**: BigQuery에 INSERT/UPDATE/DELETE 실행 금지 (Text-to-SQL은 READ-ONLY)
- **JSON Key**: 개발용 `C:/json_key/...` 경로는 로컬 전용. 프로덕션은 Secret Manager 사용
- **SALES_ALL_Backup 스키마**: 실제 스키마를 아직 공유받지 못함. Phase 2 시작 전에 스키마 분석 필요
- **Open WebUI**: Docker로 별도 실행, FastAPI의 `/v1/chat/completions`에 연결
- **비용 목표**: 월 $500 이하 (Gemini API + BigQuery + Cloud Run)

## Design System
Always read DESIGN.md before making any visual or UI decisions.
All font choices, colors, spacing, and aesthetic direction are defined there.
Do not deviate without explicit user approval.
In QA mode, flag any code that doesn't match DESIGN.md.