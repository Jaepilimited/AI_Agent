# SKIN1004 Enterprise AI System

## Product Requirements Document

**Hybrid AI System: Text-to-SQL + Agentic RAG**
**BigQuery & Open WebUI & LangGraph**

**Version 5.0**
**2026.02.11**
**DB Team / Data Analytics**

---

# 1. Project Overview

본 프로젝트는 SKIN1004의 글로벌 세일즈 데이터와 사내 문서를 통합 관리하는 엔터프라이즈 AI 시스템을 구축하는 것을 목표로 한다. 약 200명의 임직원이 자연어로 매출 데이터를 조회하고, 사내 문서에서 필요한 정보를 즉시 검색할 수 있는 환경을 제공한다.

> **핵심 설계 원칙**: 구조화된 데이터(매출)에는 Text-to-SQL, 비구조화 데이터(문서)에는 RAG를 적용하는 하이브리드 접근. 질문 유형에 따라 자동 라우팅하여 정확도와 속도를 동시에 확보한다.

## 1.1 프로젝트 배경

- Shopee, Lazada, TikTok Shop, Amazon 등 다국적 플랫폼 매출 데이터가 BigQuery에 통합 관리중
- 매출 데이터 조회 시 SQL 작성이 필요하여 비기술 직원의 데이터 접근성이 제한됨
- 사내 문서(정책, 매뉴얼, 제품 정보 등)가 분산 관리되어 정보 검색에 시간 소요
- 200명 규모의 임직원이 동시에 활용할 수 있는 가성비 높은 AI 솔루션 필요

## 1.2 프로젝트 목표

| 목표 | 설명 | KPI |
|------|------|-----|
| 데이터 민주화 | 비기술 직원도 자연어로 매출 데이터 조회 | SQL 작성 없이 데이터 접근율 90%+ |
| 정보 검색 효율화 | 사내 문서 검색 시간 단축 | 평균 검색 시간 30초 이내 |
| 비용 최적화 | 200명 동시 사용 기준 월 운영비 최소화 | 월 $500 이하 (AI API 비용) |
| 정확도 확보 | 매출 수치 오류 제로 목표 | Text-to-SQL 정확도 95%+ |

---

# 2. System Architecture

사용자 질문은 Query Analyzer를 통해 유형이 분류되고, 각 유형에 최적화된 처리 경로로 라우팅된다.

## 2.1 하이브리드 라우팅 아키텍처

```
User Query → Query Analyzer → [Text-to-SQL Agent | RAG Agent | Direct LLM] → Response
```

| 질문 유형 | 처리 경로 | 예시 |
|----------|----------|------|
| 매출/데이터 질문 | Text-to-SQL Agent | "태국 쇼피 1월 매출 합계?" |
| 문서/정책 질문 | RAG Agent (Vector Search) | "쇼피 반품 프로세스 어떻게 돼?" |
| 일반/간단한 질문 | Direct LLM Response | "SKU가 뭐야?" |
| 복합 질문 | Multi-Agent (SQL + RAG) | "지난달 매출 하락 원인 분석해줘" |

## 2.2 Text-to-SQL이 핵심인 이유

SKIN1004의 매출 데이터는 BigQuery에 구조화된 테이블로 관리된다. 구조화된 데이터에 RAG를 적용할 경우 다음과 같은 문제가 발생한다:

| 항목 | Text-to-SQL | RAG |
|------|-------------|-----|
| 정확도 | 정확한 숫자 반환 (SQL 직접 실행) | 요약/근사치 (환각 위험) |
| 속도 | 빠름 (SQL 1회 실행) | 느림 (임베딩 → 검색 → 생성) |
| 실시간성 | 항상 최신 데이터 | 인덱싱 주기에 의존 |
| 집계 연산 | SUM, AVG, GROUP BY 정확 | 숫자 계산에 구조적 한계 |
| 적합 데이터 | 매출, 재고, 주문 등 정형 데이터 | 정책, 매뉴얼 등 비정형 문서 |

---

# 3. Tech Stack

## 3.1 AI 모델 선정: Gemini 2.0 Flash

200명 규모의 동시 사용, 가성비, 정확도, 속도를 종합적으로 고려하여 Gemini 2.0 Flash를 메인 LLM으로 선정한다.

| 항목 | Gemini 2.0 Flash | GPT-4o (비교) |
|------|------------------|---------------|
| Input 가격 (100만 토큰) | ~$0.10 | ~$2.50 |
| Output 가격 (100만 토큰) | ~$0.40 | ~$10.00 |
| 속도 | 매우 빠름 | 보통 |
| 한국어 성능 | 우수 | 우수 |
| 멀티모달 | PDF/이미지 네이티브 지원 | 지원 |
| BigQuery 연동 | GCP 네이티브 (레이턴시 최소) | 별도 설정 필요 |
| 월 예상 비용 (200명) | 약 $100-300 | 약 $2,000-5,000 |

> **GCP 생태계 안에서 완결**: Gemini API → BigQuery Vector Search → 결과 반환까지 전부 내부 네트워크 처리로 레이턴시 최소화

## 3.2 전체 기술 스택

| 레이어 | 기술 | 선정 이유 |
|--------|------|----------|
| Frontend | Open WebUI (Docker) | 자체 호스팅, 커스터마이징 가능, 채팅 UI |
| API Server | FastAPI | OpenAI API 규격 에뮬레이션, 비동기 처리 |
| Orchestration | LangGraph | Stateful 워크플로우, 라우팅/분기 로직 |
| LLM | Gemini 2.0 Flash | 가성비, 속도, GCP 네이티브 |
| Database | BigQuery | 기존 매출 데이터 + Vector Search |
| Embedding | BGE-M3 | 다국어(한/영/동남아), 768차원 |
| Document Parser | Docling | 표/차트 마크다운 변환 |
| Web Search | Tavily API | CRAG 보완 검색용 |

---

# 4. Core Features

## 4.1 Text-to-SQL Agent

**메인 테이블**
메인 데이터 소스: `skin1004-319714.Sales_Integration.SALES_ALL_Backup`

**동작 흐름**
1. 사용자 자연어 질문 수신
2. Query Analyzer가 매출/데이터 관련 질문으로 판별
3. 테이블 스키마 참조하여 BigQuery SQL 자동 생성
4. SQL Validation (문법 검증 + 보안 검사)
5. BigQuery 실행 후 결과 반환
6. LLM이 결과를 자연어로 요약하여 사용자에게 전달

**안전장치**
- READ-ONLY: SELECT 문만 허용, INSERT/UPDATE/DELETE 차단
- 테이블 화이트리스트: 허용된 테이블만 접근 가능
- 쿼리 타임아웃: 최대 30초, 과도한 스캔 방지
- 결과 행 제한: 최대 10,000행 반환 (v4.0 업데이트)

## 4.2 Agentic RAG (문서 검색)

**대상 문서**
- 사내 정책 문서, 매뉴얼
- 제품 성분 및 스펙 문서
- 마케팅 가이드라인
- 플랫폼별 운영 정책 (Shopee, Lazada, TikTok Shop 등)

**에이전틱 RAG 워크플로우 (LangGraph)**
- Adaptive RAG: 질문 난이도에 따라 직접 답변, 단순 검색, 심층 검색으로 라우팅
- Corrective RAG (CRAG): 검색 결과 관련성 평가 후 부족하면 웹 검색으로 보완
- Self-Reflective RAG: 생성된 답변의 환각 여부와 질문 적합성을 자체 검토하여 재시도
- Multi-Query Fusion: 질문을 여러 관점으로 재작성하여 검색 성능 극대화

## 4.3 데이터 인덱싱 파이프라인

- 복잡 문서 처리: PDF, HWP, PPT 내 표/차트를 마크다운으로 변환 (Docling)
- 하이브리드 청킹: Semantic Chunking + Hierarchical Chunking(Parent-Child) 병행
- BigQuery 벡터 저장: 텍스트, 메타데이터, 임베딩 벡터를 BigQuery 테이블에 저장
- 자동 인덱싱: 신규 문서 업로드 시 자동 파싱 및 임베딩

## 4.4 프론트엔드 (Open WebUI)

- FastAPI를 통한 OpenAI API 규격 에뮬레이션으로 Open WebUI와 완벽 연동
- Grounding 시각화: 답변 근거가 된 원문 링크 및 참조 표시
- 사용자 피드백(좋아요/싫어요) 수집
- Google Workspace SSO 연동 (200명 인증 관리)

---

# 5. Data Schema

## 5.1 RAG 임베딩 테이블

```sql
CREATE TABLE skin1004-319714.AI_RAG.rag_embeddings (
  id STRING,
  content STRING,           -- 마크다운 형태의 텍스트
  metadata JSON,            -- 파일명, 페이지 번호, 생성일 등
  embedding VECTOR(768),    -- BGE-M3 임베딩 벡터
  source_type STRING        -- PDF, HWP, PPT 등
);
```

## 5.2 질문-답변 로그 테이블

```sql
CREATE TABLE skin1004-319714.AI_RAG.qa_logs (
  id STRING,
  user_id STRING,
  query STRING,             -- 사용자 원본 질문
  route_type STRING,        -- text_to_sql | rag | direct_llm
  generated_sql STRING,     -- Text-to-SQL인 경우 생성된 SQL
  answer STRING,            -- 최종 답변
  feedback STRING,          -- thumbs_up | thumbs_down | null
  created_at TIMESTAMP
);
```

---

# 6. LangGraph Node Design

| Node | Input | Output | 설명 |
|------|-------|--------|------|
| Analyze Query | 사용자 질문 | route_type | 질문 의도 파악 및 라우팅 결정 |
| Generate SQL | 자연어 질문 + 스키마 | SQL 쿼리 | BigQuery SQL 자동 생성 |
| Validate SQL | SQL 쿼리 | 검증 결과 | 문법 검증 + 보안 검사 (SELECT ONLY) |
| Execute SQL | 검증된 SQL | 쿼리 결과 | BigQuery 실행 후 결과 반환 |
| Retrieve | 질문 임베딩 | 관련 문서 | BigQuery Vector Search 실행 |
| Grade Documents | 검색 결과 | 관련성 점수 | 문서 관련성 평가 (yes/no) |
| Rewrite Query | 원본 질문 | 재작성 질문 | 관련성 낮을 시 질문 재작성 |
| Generate Answer | 질문 + 컨텍스트 | 최종 답변 | LLM 기반 답변 생성 |
| Reflect | 생성된 답변 | 검증 결과 | 환각 체크 및 품질 검증 |

---

# 7. Implementation Roadmap

## Phase 1: 인프라 및 환경 설정 (1주차)
1. Google Cloud SDK 및 BigQuery 클라이언트 설정
2. Gemini 2.0 Flash API 연동 테스트
3. BGE-M3 임베딩 모델 로드 및 벡터 생성 테스트
4. BigQuery AI_RAG 데이터셋 및 테이블 생성

## Phase 2: Text-to-SQL Agent 구축 (2-3주차)
1. SALES_ALL_Backup 테이블 스키마 분석 및 메타데이터 정리
2. LangGraph 기반 SQL 생성-검증-실행 파이프라인 구현
3. SQL 안전장치 구현 (READ-ONLY, 화이트리스트, 타임아웃)
4. 자연어 → SQL 정확도 테스트 및 프롬프트 튜닝

## Phase 3: RAG 파이프라인 구축 (4-5주차)
1. Docling 기반 문서 파서 구현 (PDF, HWP, PPT)
2. 하이브리드 청킹 로직 구현 (Semantic + Hierarchical)
3. BigQuery 벡터 인덱싱 및 VECTOR_SEARCH 구현
4. Adaptive/Corrective/Self-Reflective RAG 워크플로우 구현

## Phase 4: API 서버 및 프론트엔드 연동 (6-7주차)
1. FastAPI 서버 구현 (OpenAI API 규격 에뮬레이션)
2. 하이브리드 라우팅 로직 통합 (SQL + RAG + Direct)
3. Open WebUI 설치 및 외부 모델 등록
4. Google Workspace SSO 연동

## Phase 5: 테스트 및 최적화 (8주차)
1. RAGAS 평가 프레임워크 적용 (Retrieval + Generation 품질)
2. 200명 동시 접속 부하 테스트
3. QA 로그 수집 및 파인튜닝 데이터 파이프라인 구축
4. 사용자 피드백 루프 검증 및 개선

---

# 8. Security & Authentication

| 항목 | 방안 |
|------|------|
| 인증 | Google Workspace SSO 연동 (OAuth 2.0) |
| API Key 관리 | GCP Secret Manager (하드코딩 금지) |
| 데이터 접근 | BigQuery IAM 역할 기반 접근 제어 |
| SQL 보안 | SELECT ONLY + 테이블 화이트리스트 |
| 네트워크 | VPC 내부 통신, Cloud Run 활용 |
| 감사 로그 | 모든 질문-답변 BigQuery에 기록 |

> **주의**: JSON_KEY_PATH 하드코딩은 개발 환경에서만 사용. 프로덕션에서는 반드시 GCP Secret Manager 또는 Workload Identity Federation으로 전환할 것.

---

# 9. Cost Estimation

200명 기준, 1인당 하루 평균 20회 질문 가정 (월 약 12만 건)

| 항목 | 월 예상 비용 | 비고 |
|------|-------------|------|
| Gemini 2.0 Flash API | $100-300 | Input/Output 토큰 기반 |
| BigQuery 스토리지 | $50-100 | 기존 인프라 활용 |
| BigQuery 쿼리 비용 | $50-150 | 온디맨드 과금 |
| Cloud Run (FastAPI) | $30-80 | Auto-scaling |
| Open WebUI (Docker) | $0 | 자체 호스팅 |
| BGE-M3 Embedding | $20-50 | 문서 인덱싱 시 |
| **합계** | **$250-680/월** | GPT-4o 대비 1/5~1/10 수준 |

---

# 10. Expected Benefits

- **데이터 민주화**: SQL을 모르는 직원도 자연어로 매출 데이터 즉시 조회
- **정확도 극대화**: 매출 수치는 Text-to-SQL로 정확한 숫자 반환 (환각 Zero)
- **정보 검색 시간 90% 단축**: 사내 문서 검색 평균 30초 이내
- **비용 효율**: GPT-4o 대비 1/10 수준의 API 비용으로 동일 품질 제공
- **데이터 자산화**: 모든 QA 로그가 BigQuery에 축적되어 파인튜닝 골든 데이터셋으로 활용
- **확장성**: LangGraph 기반으로 에이전트 추가/로직 변경이 용이

---

# 11. Environment Configuration

| 항목 | 값 |
|------|-----|
| GCP Project ID | skin1004-319714 |
| 메인 테이블 | skin1004-319714.Sales_Integration.SALES_ALL_Backup |
| RAG 데이터셋 | skin1004-319714.AI_RAG |
| JSON Key (개발용) | C:/json_key/skin1004-319714-60527c477460.json |
| 사용자 수 | 약 200명 |
| LLM | Gemini 2.0 Flash |
| Embedding | BGE-M3 (768차원) |

---

# 12. Update Log

이 섹션은 시스템 업데이트 내역을 증분(incremental) 형태로 기록합니다. 최신 업데이트가 상단에 위치합니다.

---

## v6.0.0 - 2026.02.11

### 16.1 Notion Agent v6.0 — 허용 목록 기반 검색 리팩토링

#### 전체 워크스페이스 크롤링 → 허용 목록 기반 검색
기존 Notion Agent v5.0은 전체 워크스페이스를 재귀 크롤링하여 페이지 인덱스를 구축(7분+). v6.0에서는 사용자가 지정한 10개 페이지/DB ID만 대상으로 검색하도록 전면 리팩토링.

| 항목 | v5.0 (이전) | v6.0 (현재) |
|------|------------|------------|
| 검색 범위 | 전체 워크스페이스 | 허용 목록 10개 페이지/DB |
| 워밍업 시간 | 7분+ (전체 크롤링) | ~3초 (10개 타이틀 fetch) |
| 검색 방식 | 재귀 인덱스 + Notion Search API | 키워드 매칭 + LLM 폴백 |
| API 호출 | 수백 회 (크롤링) | 10회 (워밍업) + 1~2회 (조회) |

#### 허용 목록 (_ALLOWED_PAGES)
```python
_ALLOWED_PAGES = [
    {"id": "2532b4283b00...", "description": "법인 태블릿", "type": "database"},
    {"id": "1602b4283b00...", "description": "데이터 분석 파트", "type": "database"},
    {"id": "2e62b4283b00...", "description": "EAST 2팀 가이드 아카이브", "type": "database"},
    {"id": "2e12b4283b00...", "description": "EAST 2026 업무파악", "type": "database"},
    {"id": "19d2b4283b00...", "description": "EAST 틱톡샵 접속 방법", "type": "page"},
    {"id": "1982b4283b00...", "description": "EAST 해외 출장 가이드북", "type": "page"},
    {"id": "22e2b4283b00...", "description": "WEST 틱톡샵US 대시보드", "type": "database"},
    {"id": "c058d9e89e8a...", "description": "KBT 스스 운영방법", "type": "page"},
    {"id": "1fb2b4283b00...", "description": "네이버 스스 업무 공유", "type": "page"},
    {"id": "1dc2b4283b00...", "description": "DB daily 광고 입력 업무", "type": "page"},
]
```

> **참고**: KBT 스스 운영방법, 네이버 스스 업무 공유는 Notion Integration 미연결로 404 반환. 나머지 8개 페이지 정상 접근 확인.

#### UUID 포맷 변환 및 타입 폴백
- Notion API는 8-4-4-4-12 UUID 형식 필수. `_format_uuid()` 함수로 compact 32자 ID 자동 변환
- 허용 목록의 "database" 타입 5개가 실제로는 "page"로 확인됨. `_fetch_title_with_fallback()`이 선언된 타입 실패 시 반대 타입으로 자동 재시도

### 16.2 LLM 폴백 검색 (Gemini Flash)

#### 키워드 매칭 실패 시 LLM 자동 선택
허용 목록 타이틀/설명에 키워드가 없는 경우(예: "zombiepack 번들 제품") Gemini Flash가 접근 가능한 페이지 목록에서 가장 관련성 높은 페이지를 자동 선택.

```
검색 흐름:
1. 키워드 매칭 (exact > partial > word match) → 매칭 시 즉시 반환
2. 실패 → _llm_select_pages(): Flash가 접근 가능 페이지 중 관련 페이지 선택
3. Flash 선택 결과로 페이지 읽기 → 답변 생성
```

- 접근 불가 페이지(title == description, 즉 API에서 타이틀 미획득)는 LLM 선택 후보에서 자동 제외

### 16.3 Notion 내 Google Sheets 자동 읽기

#### 블록 내 Google Sheets URL 자동 감지 및 데이터 조회
Notion 페이지 블록의 rich_text href에 포함된 Google Sheets URL을 자동 감지하여 시트 데이터를 함께 조회. 페이지 본문 읽기(문자 수 예산) 완료 후 독립적으로 최대 2개 시트를 추가 읽기.

```
동작 흐름:
1. _read_page_blocks()에서 블록 텍스트 추출
2. _collect_sheet_urls()로 paragraph, bulleted_list_item, toggle, bookmark, embed 블록의 href 스캔
3. Google Sheets URL 패턴 매칭 → spreadsheet ID 추출
4. Google Sheets API로 시트 데이터 조회 (최대 2개, 문자 수 예산 무관)
5. 조회된 시트 데이터를 페이지 텍스트에 추가하여 LLM 답변 생성
```

> **제한사항**: 업로드된 .xlsx 파일은 Google Sheets API 미지원. "This operation is not supported for this document" 에러 발생 시 자동 스킵.

### 16.4 변경 파일 목록

| 파일 | 변경 유형 | 설명 |
|------|----------|------|
| app/agents/notion_agent.py | 전면 리팩토링 | v5.0 → v6.0: 허용 목록, UUID 변환, 타입 폴백, LLM 폴백, Sheets 자동 읽기 |
| app/main.py | 수정 | _warmup_notion_index() → _warmup_notion_titles(), _warm_up() 호출 |

### 16.5 제거된 코드

| 함수/변수 | 이유 |
|-----------|------|
| `_build_page_index()` | 전체 워크스페이스 크롤링 불필요 |
| `_index_children()` | 재귀 인덱싱 불필요 |
| `_find_in_page_index()` | 인덱스 기반 검색 불필요 |
| `_notion_search()` | Notion Search API 불필요 |
| `_page_index`, `_page_index_built`, `_page_index_building` | 글로벌 인덱스 변수 불필요 |

### 16.6 기술 상세

#### 워밍업 (_warm_up)
```python
async def _warm_up(self):
    """허용 목록 10개 ID의 타이틀을 Notion API에서 fetch하여 캐시"""
    for entry in _ALLOWED_PAGES:
        fid = _format_uuid(entry["id"])
        title = await _fetch_title_with_fallback(fid, entry["type"])
        _page_titles[entry["id"]] = title or entry["description"]
```

#### 키워드 검색 + LLM 폴백
```python
async def _search_pages(self, query: str) -> list:
    """1차: 키워드 매칭 (exact > partial > word), 2차: Flash LLM 선택"""
    # exact match → partial match → word match
    if not results:
        results = await self._llm_select_pages(query)
    return results
```

---

## v5.0.0 - 2026.02.10

### 15.1 Dual LLM 아키텍처 도입
Open WebUI 모델 선택에 따라 Gemini 2.5 Pro(skin1004-Search) 또는 Claude Sonnet 4.5(skin1004-Analysis)가 자동 선택되는 이중 모델 구조. Gemini 2.5 Flash는 SQL 생성, 차트 설정, 라우팅 등 경량 작업에 전용 배치. 응답 속도 38-42초에서 11-13초로 개선 (키워드 우선 분류, Flash 분리, 병렬 처리, 스키마 캐싱).

### 15.2 Google Search Grounding 통합
Gemini 2.5 Pro의 네이티브 Google Search grounding 기능 활용. Multi-source handler가 Google Search(외부) + BigQuery(내부) 결과를 합성하여 답변 생성. 별도 API 키 불필요.

### 15.3 Google Workspace 개별 사용자 OAuth2
MCP 서버 기반 단일 사용자 GWS 접근 → 개별 OAuth2 인증 + Google API 직접 호출로 전환. Gmail, Drive, Calendar를 사용자별 인증으로 접근. `app/core/google_auth.py`(토큰 관리), `app/core/google_workspace.py`(API 래퍼) 신규 작성.

### 15.4 Open WebUI 단일 Google 로그인으로 GWS 통합 (SSO)
Open WebUI의 Google OAuth 로그인 시 GWS 스코프(`gmail.readonly`, `calendar.readonly`, `drive.readonly`)를 함께 요청. `access_type=offline`으로 refresh_token 획득. FastAPI가 Open WebUI SQLite DB에서 Fernet 암호화 토큰을 복호화하여 GWS Agent에 전달. Docker bind mount(`C:/openwebui-data`)로 DB 공유.

### 15.5 Open WebUI 브랜딩 및 커스터마이징
- 전체 로고/파비콘을 SKIN1004 브랜딩으로 교체
- cravercorp.com 벤치마킹 로그인 페이지 CSS 애니메이션 (WHAT DO YOU CRAVE?)
- 앱 이름: `SKIN1004 AI` (Open WebUI 접미사 제거)
- 모델명: `skin1004-Search` / `skin1004-Analysis`
- 사용자 이메일 전달: `ENABLE_FORWARD_USER_INFO_HEADERS=true`

### 15.6 변경 파일 목록

| 파일 | 변경 유형 | 설명 |
|------|----------|------|
| app/core/llm.py | 전면 재작성 | Dual LLM (Gemini + Claude), resolve_model_type() |
| app/core/google_auth.py | 신규 | OAuth2 토큰 관리 + Open WebUI DB 읽기 |
| app/core/google_workspace.py | 신규 | Gmail/Drive/Calendar API 래퍼 |
| app/agents/orchestrator.py | 수정 | Dual LLM, Google Search, user_email 전달 |
| app/agents/gws_agent.py | 전면 재작성 | MCP → ReAct + 직접 API |
| app/agents/sql_agent.py | 수정 | Flash 분리, 병렬 차트, 속도 최적화 |
| app/agents/router.py | 수정 | 키워드 우선 분류, Flash 사용 |
| app/models/agent_models.py | 수정 | 전 에이전트 Claude Sonnet 4.5 통일 |
| app/api/routes.py | 수정 | 모델명 변경, user_email 전달 |
| app/api/middleware.py | 수정 | user_email 추출 (헤더 + body JSON) |
| app/api/auth_routes.py | 신규 | GWS OAuth 엔드포인트 |
| app/config.py | 수정 | OAuth, OpenWebUI DB 설정 추가 |
| custom_login.css | 신규 | cravercorp 벤치마킹 로그인 CSS |

---

## v4.0.0 - 2026.02.06

### 14.1 차트 시각화 ChatGPT 스타일 전환

#### ChatGPT 스타일 라이트 테마 적용
기존 Looker Studio 다크 테마에서 ChatGPT 벤치마킹 라이트 테마로 전면 교체. 배경 #FFFFFF(흰색), 깔끔한 가독성, 미니멀 디자인.

#### 30색 고유 컬러 팔레트 확장
기존 10색에서 30색으로 확장하여 다수의 데이터 시리즈에서도 색상 중복 없이 구분 가능.

```python
COLORS = [
    "#6366f1",  # Indigo
    "#f59e0b",  # Amber
    "#10b981",  # Emerald
    "#ef4444",  # Red
    "#8b5cf6",  # Violet
    "#06b6d4",  # Cyan
    "#f97316",  # Orange
    "#84cc16",  # Lime
    "#ec4899",  # Pink
    "#14b8a6",  # Teal
    # ... 총 30색
]
```

#### 데이터 레이블 표시 개선
- 모든 숫자: 소수점 없음 (1,234,567)
- 축약 표시: K(천), M(백만), B(십억)
- 퍼센트만: 소수점 1자리 (12.5%)

#### 레전드 개선
- **위치**: 차트 오른쪽 (겹침 방지)
- **정렬**: 매출 높은 순 (내림차순)
- **동적 이미지 크기**: 레전드 10개 이상 시 1300x700px

### 14.2 데이터 조회 제한 확대

#### SQL 결과 최대 행 수 확대
CSV 다운로드 시 전체 데이터 제공을 위해 결과 제한 확대.

| 설정 | 이전 | 변경 후 |
|------|------|---------|
| MAX_RESULT_ROWS | 1,000행 | **10,000행** |
| BigQuery max_rows | 1,000행 | **10,000행** |
| SQL Agent max_rows | 1,000행 | **10,000행** |

### 14.3 변경 파일 목록

| 파일 | 변경 유형 | 설명 |
|------|----------|------|
| app/core/chart.py | 전면 재작성 | ChatGPT 라이트 테마, 30색 팔레트, 레전드 정렬 |
| app/core/security.py | 수정 | MAX_RESULT_ROWS 10000 |
| app/core/bigquery.py | 수정 | 기본 max_rows 10000 |
| app/agents/sql_agent.py | 수정 | execute_query max_rows 10000 |

### 14.4 기술 상세

#### 숫자 포맷 함수
```python
def _format_short(val: float) -> str:
    if abs(val) >= 1e9:
        return f"{int(val / 1e9)}B"
    elif abs(val) >= 1e6:
        return f"{int(val / 1e6)}M"
    elif abs(val) >= 1e3:
        return f"{int(val / 1e3)}K"
    return str(int(val))
```

#### 레전드 정렬 로직
```python
# 그룹별 총합 계산 후 내림차순 정렬
group_totals = {g: sum(values) for g, values in data.items()}
groups = sorted(groups, key=lambda g: group_totals[g], reverse=True)
```

---

## v3.0.0 - 2026.02.05

### 13.1 아키텍처 전면 개편 (v2.0 -> v3.0)

#### Orchestrator-Worker 멀티 에이전트 전환
단일 LLM + Custom Router -> Orchestrator-Worker 멀티 에이전트 구조. Orchestrator(Opus 4.5)가 질문 의도를 파악하고 전문화된 Sub Agent에 위임.

#### MCP (Model Context Protocol) 도입
Custom API -> MCP 기반 표준화된 Tool 연결. BigQuery MCP(SQL 실행, 스키마 조회), Notion MCP(문서 검색), Google Workspace MCP(Drive, Gmail, Calendar).

#### LLM 모델 변경: Gemini -> Anthropic Multi-Model
Orchestrator/BigQuery: Opus 4.5 (Tool calling) / Query Verifier: Opus 4.5 (SQL 검증) / Notion/GWS: Sonnet 4 (비용 효율).

#### Query Verifier Agent 추가
SQL 생성 후 별도 Agent가 문법/스키마/보안 이중 검증. 검증 실패 시 오류 피드백과 SQL 재생성 (Self-Correction).

#### 문서 소스 확장
BigQuery 단일 -> BigQuery + Notion MCP + Google Workspace MCP. 임베딩 인덱싱 없이 실시간 문서 접근.

### 13.2 API 라우팅 Orchestrator 전환

#### run_agent() -> OrchestratorAgent.route_and_execute() 교체
app/api/routes.py에서 기존 LangGraph 직접 호출을 Orchestrator 경유로 변경. 모든 요청이 의도 분류 후 Sub Agent로 위임.

#### Non-streaming / Streaming 응답 모두 적용
Orchestrator 반환값 dict에서 answer 필드 추출. 기존 OpenAI-compatible 응답 포맷 유지.

### 13.3 Settings 모델 확장

#### app/config.py pydantic Settings 필드 추가
anthropic_api_key: Anthropic API 인증키 / notion_mcp_token: Notion MCP 연동 토큰. 기존 Gemini/BigQuery 설정과 공존.

### 13.4 변경 파일 목록

| 파일 | 유형 | 설명 |
|------|------|------|
| app/models/agent_models.py | 신규 | Multi-Model 설정 (Opus 4.5 / Sonnet 4) |
| app/mcp/__init__.py | 신규 | MCP 모듈 초기화 |
| app/mcp/bigquery_mcp.py | 신규 | BigQuery MCP Server 연결 |
| app/mcp/notion_mcp.py | 신규 | Notion MCP Server 연결 |
| app/mcp/gws_mcp.py | 신규 | Google Workspace MCP Server 연결 |
| app/agents/orchestrator.py | 신규 | Orchestrator Agent (Sub Agent 지휘) |
| app/agents/query_verifier.py | 신규 | Query Verifier Agent (SQL 이중 검증) |
| app/agents/notion_agent.py | 신규 | Notion Sub Agent (MCP + ReACT) |
| app/agents/gws_agent.py | 신규 | GWS Sub Agent (MCP + ReACT) |
| app/api/routes.py | 수정 | Orchestrator 라우팅 전환 |
| app/config.py | 수정 | anthropic_api_key, notion_mcp_token 추가 |
| app/main.py | 수정 | version 3.0.0 |

---

## v1.1.0 - 2026.02.03

### 12.1 차트 시스템 전면 개편

#### Base64 → 파일 기반 URL 서빙 전환
Open WebUI CSP 호환성을 위해 차트 이미지를 base64 data URI 대신 FastAPI StaticFiles 기반 PNG 파일로 제공. 경로: /static/charts/{uuid}.png

#### Looker Studio 다크 테마 적용
기존 matplotlib 기본 테마에서 Looker Studio 벤치마킹 다크 테마로 전면 교체. 배경 #1e1e1e, Google 컬러 팔레트(#4285F4, #EA4335, #34A853, #FBBC04), 도넛 차트, 미니멀 축선.

#### 차트 타입 자동 선택 로직
LLM이 쿼리 특성에 따라 최적 차트 타입 자동 판단: line(시계열), bar(카테고리 비교), horizontal_bar(다수 항목), pie/donut(비율), stacked_bar(누적), grouped_bar(다중 지표).

#### 레전드 표시 + 항목별 색상 구분
모든 차트 타입에 레전드 추가. bar/horizontal_bar에서 각 항목별 고유 색상과 매칭되는 Patch 레전드 표시.

#### 가독성 가드 (Readability Guard)
시각화 시 읽기 어려운 경우 차트 생성 자동 스킵. bar: 15개 초과, horizontal_bar: 20개 초과, pie: 10개 초과, line: 36포인트 초과. LLM 프롬프트 + 코드 레벨 이중 가드.

### 12.2 SQL 프롬프트 스키마 관리 체계 구축

#### Excel 기반 컬럼 화이트리스트 적용
데이터 학습.xlsx Sales_all 탭에서 한국어='X'인 항목 제외, 26개 유효 컬럼만 선별. 전체 79개 중 26개만 사용 허용.

#### 컬럼 매핑 규칙 강제
매출: Sales1_R 우선(Sales2_R 보조) / 수량: Total_Qty(Quantity 금지) / 제품명: `SET`(Product_Name 금지, 백틱 필수) / 대륙: Continent1 우선 / 팀: Team_NEW(Team 금지)

#### 1900년대 데이터 제외 필터
모든 쿼리에 Date >= '2000-01-01' 조건 필수. 1900년대 목표/플래너 데이터 자동 제외. 9개 예시 쿼리 모두 반영.

#### SET SQL 예약어 이슈 해결
SET은 SQL 예약어이므로 백틱(`) 이스케이프 필수 적용. 예: SELECT `SET` AS product FROM ...

#### Mall_Classification 매핑 확장
쇼피, 틱톡, 라자다, 아마존, 토코피디아, 자사몰, 라쿠텐, 큐텐, 티몰, 사방넷 등 10개 플랫폼 매핑 완료.

### 12.3 인프라 및 배포

#### FastAPI StaticFiles 마운트
/static/charts/ 경로로 차트 PNG 파일 정적 서빙. CORS 헤더 및 CSP 정책 호환 확인.

#### Docker 컨테이너 구성
skin1004-ai-agent(포트 8100) + skin1004-open-webui(포트 3000) 이중 컨테이너. Open WebUI 차트 렌더링 정상 확인.

#### 서버 Hot Reload
uvicorn --reload로 코드 변경 시 자동 재시작. 프롬프트 파일은 런타임 자동 반영.

### 12.4 변경 파일 목록

| 파일 | 변경 유형 | 설명 |
|------|----------|------|
| app/core/chart.py | 전면 재작성 | Looker Studio 다크 테마, 파일 기반 저장, 레전드, 가독성 가드 |
| app/agents/sql_agent.py | 수정 | _try_generate_chart() 파일 URL 방식 전환 |
| app/main.py | 수정 | StaticFiles 마운트 추가 |
| prompts/sql_generator.txt | 전면 재작성 | 26개 유효 컬럼, 1900년대 제외, SET 이스케이프, 9개 예시 |
| app/static/charts/ | 신규 | 차트 PNG 파일 저장 디렉토리 |

---

## 12.5 현재 구현 현황

| 항목 | 상태 | 비고 |
|------|------|------|
| Phase 1: 인프라 및 환경 구성 | **DONE** | BigQuery, Gemini, FastAPI, Docker |
| Phase 2: Text-to-SQL Agent | **DONE** | LangGraph 워크플로우, SQL 생성/검증/실행/포맷 |
| Phase 2+: 차트 시각화 | **DONE** | ChatGPT 라이트 테마, 30색, 레전드 정렬 (v4.0) |
| Phase 2+: 스키마 관리 | **DONE** | Excel 기반 26개 컬럼 화이트리스트 |
| Phase 2+: 데이터 제한 확대 | **DONE** | 1,000행 → 10,000행 (v4.0) |
| Phase 2+: 속도 최적화 | **DONE** | 38s → 11s, Flash 분리, 병렬 처리, 캐싱 (v5.0) |
| Phase 3: RAG 파이프라인 | TODO | Docling 파서, BGE-M3 임베딩, BigQuery 벡터 인덱스 |
| Phase 4: API + 프론트엔드 | **DONE** | FastAPI + Open WebUI + Google SSO 완료 (v5.0) |
| Phase 4+: Dual LLM | **DONE** | Gemini 2.5 Pro + Claude Sonnet 4.5 (v5.0) |
| Phase 4+: Google Search | **DONE** | Gemini 네이티브 grounding (v5.0) |
| Phase 4+: GWS 개별 OAuth + SSO | **DONE** | 단일 Google 로그인으로 Gmail/Drive/Calendar (v5.0) |
| Phase 4+: 브랜딩 커스텀 | **DONE** | 로고, 로그인 CSS, 모델명 (v5.0) |
| Phase 4+: Notion v6.0 | **DONE** | 허용 목록 기반 검색, LLM 폴백, Sheets 자동 읽기 (v6.0) |
| Phase 5: 테스트 및 최적화 | TODO | RAGAS 평가, 부하 테스트 |

---

**End of Document**
