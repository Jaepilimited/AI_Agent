# SKIN1004 Enterprise AI System

## Product Requirements Document

**Hybrid AI System: Text-to-SQL + Notion + Google Workspace + Multi-Agent**
**BigQuery & Open WebUI & Orchestrator-Worker Architecture**

**Version 6.2.0**
**2026.02.12**
**DB Team / Data Analytics**

---

# 1. Project Overview

본 프로젝트는 SKIN1004의 글로벌 세일즈 데이터, 사내 Notion 문서, Google Workspace를 통합 관리하는 엔터프라이즈 AI 시스템을 구축하는 것을 목표로 한다. 약 200명의 임직원이 자연어로 매출 데이터를 조회하고, Notion 문서를 검색하며, Google Calendar/Gmail/Drive에 접근할 수 있는 환경을 제공한다.

> **핵심 설계 원칙**: Orchestrator-Worker 멀티 에이전트 구조. 매출 데이터에는 Text-to-SQL, 사내 문서에는 Notion Direct API, 개인 업무에는 Google Workspace OAuth2를 적용. 키워드 우선 분류 + LLM 라우팅으로 질문 유형을 자동 판별하여 최적 경로로 처리한다.

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

## 2.1 Orchestrator-Worker 멀티 에이전트 아키텍처

```
                            ┌─────────────────┐
                            │   Open WebUI     │  (Frontend, Docker port 3000)
                            │  Google SSO Auth │
                            └────────┬────────┘
                                     │ HTTP POST /v1/chat/completions
                            ┌────────▼────────┐
                            │    FastAPI       │  (port 8100)
                            │   Middleware     │  (user_email 추출, CORS)
                            └────────┬────────┘
                                     │
                     ┌───────────────▼───────────────┐
                     │        Orchestrator            │
                     │  (키워드 우선 분류 + LLM 라우팅) │
                     └───┬───┬───┬───┬───┬───────────┘
                         │   │   │   │   │
           ┌─────────────┤   │   │   │   └──────────────┐
           │             │   │   │   │                   │
     ┌─────▼─────┐ ┌────▼───▼┐ │ ┌─▼──────────┐ ┌──────▼──────┐
     │ BigQuery   │ │ Notion  │ │ │   GWS      │ │   Direct    │
     │ SQL Agent  │ │ Agent   │ │ │   Agent    │ │   LLM       │
     │            │ │ (v6.2)  │ │ │ (OAuth2)   │ │             │
     └─────┬─────┘ └────┬────┘ │ └─────┬──────┘ └──────┬──────┘
           │             │      │       │                │
     ┌─────▼─────┐ ┌────▼────┐ │ ┌─────▼──────┐        │
     │ BigQuery   │ │ Notion  │ │ │ Gmail/Cal/ │   ┌────▼────┐
     │ (SQL실행)  │ │ API     │ │ │ Drive API  │   │ Gemini/ │
     │ + Chart    │ │ +Sheets │ │ │ (per-user) │   │ Claude  │
     └───────────┘ └─────────┘ │ └────────────┘   └─────────┘
                               │
                        ┌──────▼──────┐
                        │   Multi     │
                        │ (BQ+Search) │
                        └─────────────┘
```

| 라우트 | 처리 경로 | 예시 |
|--------|----------|------|
| bigquery | Text-to-SQL Agent → BigQuery 실행 + 차트 | "태국 쇼피 1월 매출 합계?" |
| notion | Notion Agent → 허용 목록 검색 + Sheets | "해외 출장 가이드북 보여줘" |
| gws | GWS Agent → Gmail/Calendar/Drive (개별 OAuth2) | "오늘 일정 알려줘" |
| multi | Google Search + BigQuery → 합성 답변 | "지난달 매출 하락 원인 분석해줘" |
| direct | LLM 직접 응답 (Gemini/Claude) | "SKU가 뭐야?" |

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

## 3.1 AI 모델 선정: Dual LLM + Flash 3계층 구조

200명 규모의 동시 사용을 고려하여 3계층 LLM 구조를 채택. Open WebUI에서 사용자가 모델을 선택하면 해당 LLM이 메인 응답을 생성하고, 경량 작업은 Flash가 전담.

```
┌─────────────────────────────────────────────────────────┐
│                    LLM 3계층 구조                         │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  [Tier 1] 메인 응답 LLM (사용자 선택)                     │
│  ┌──────────────────┐  ┌──────────────────┐             │
│  │ Gemini 3 Pro     │  │ Claude Opus 4.6  │             │
│  │ (skin1004-Search)│  │ (skin1004-       │             │
│  │                  │  │  Analysis)       │             │
│  │ • Google Search  │  │ • 심층 분석       │             │
│  │   grounding      │  │ • 복잡한 추론     │             │
│  │ • 범용 응답      │  │ • 구조화된 답변   │             │
│  └──────────────────┘  └──────────────────┘             │
│                                                         │
│  [Tier 2] 경량 작업 전용 — Gemini 2.5 Flash              │
│  ┌──────────────────────────────────────────┐           │
│  │ • SQL 생성          • 차트 설정 JSON     │           │
│  │ • 라우팅 분류        • 답변 포맷팅        │           │
│  │ • Notion 페이지 선택  • 쿼리 리라이팅     │           │
│  └──────────────────────────────────────────┘           │
│                                                         │
│  [Tier 3] 키워드 분류 (LLM 미사용, 0ms)                  │
│  ┌──────────────────────────────────────────┐           │
│  │ • GWS 키워드: 일정,메일,드라이브 → gws   │           │
│  │ • Notion 키워드: 노션,notion → notion     │           │
│  │ • 매출 키워드: 매출,판매,순위 → bigquery  │           │
│  └──────────────────────────────────────────┘           │
└─────────────────────────────────────────────────────────┘
```

| 모델 | 역할 | 선정 이유 |
|------|------|----------|
| Gemini 3 Pro Preview | 메인 응답 (Search) | Google Search grounding, 추론 능력 향상, 1M 토큰 컨텍스트 |
| Claude Opus 4.6 | 메인 응답 (Analysis) | 심층 분석, 복잡한 추론, 구조화된 답변 |
| Gemini 2.5 Flash | 경량 작업 전용 | SQL 생성, 라우팅 (속도+안정성 최우선) |

> **속도 최적화**: 키워드 우선 분류(Tier 3) → 매칭 실패 시에만 Flash 라우팅(Tier 2) → 최종 답변은 메인 LLM(Tier 1). 응답 속도 38-42초 → 11-13초로 개선.

## 3.2 전체 기술 스택

| 레이어 | 기술 | 선정 이유 |
|--------|------|----------|
| Frontend | Open WebUI (Docker) | 자체 호스팅, Google SSO, 모델 선택 UI |
| API Server | FastAPI (port 8100) | OpenAI API 규격 에뮬레이션, 비동기 처리 |
| Orchestration | Orchestrator-Worker | 키워드 우선 분류 + LLM 라우팅 |
| Main LLM | Gemini 3 Pro Preview / Claude Opus 4.6 | Dual 모델 선택 (Open WebUI 모델 피커) |
| Fast LLM | Gemini 2.5 Flash | SQL 생성, 라우팅 등 경량 작업 |
| Database | BigQuery | 매출 데이터 (SALES_ALL_Backup) + Product |
| Document | Notion API (Direct) | 허용 목록 기반 실시간 페이지 접근 (v6.0) |
| Workspace | Google Workspace API | Gmail, Calendar, Drive (개별 OAuth2) |
| Search | Google Search (grounding) | Gemini 네이티브 grounding, API 키 불필요 |
| Auth | Google OAuth 2.0 | Open WebUI SSO + GWS 스코프 통합 |
| Chart | Plotly (ChatGPT style) | 30색 팔레트, 레전드, 가독성 가드, y축 자동 보정 |

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

## 4.2 Notion Agent (사내 문서 검색, v6.0)

**검색 대상**: 허용 목록에 등록된 Notion 페이지/DB 10개 (관리자 설정)

**동작 흐름**
```
사용자 질문 → 검색어 추출 → 키워드 매칭 (허용 목록)
                                  ├─ 매칭 성공 → 페이지 읽기
                                  └─ 매칭 실패 → Flash LLM 페이지 선택
                              → 블록 텍스트 추출 (15,000자 예산)
                              → Google Sheets 자동 감지/조회 (최대 2개)
                              → LLM 답변 생성
```

**핵심 기능**
- 허용 목록 기반 검색: 10개 지정 페이지/DB만 대상 (워밍업 ~3초)
- LLM 폴백: 키워드 매칭 실패 시 Gemini Flash가 관련 페이지 자동 선택
- Google Sheets 자동 읽기: 블록 내 Sheets URL 감지 → API 조회 → 데이터 포함 답변
- UUID 자동 변환: compact 32자 → 8-4-4-4-12 형식
- 타입 폴백: page/database 타입 자동 감지 (선언 타입 실패 시 반대 타입 재시도)

## 4.3 Google Workspace Agent (개인 업무)

**접근 서비스**: Gmail (읽기), Calendar (읽기), Drive (읽기)

**동작 흐름**
1. Open WebUI Google SSO 로그인 시 GWS 스코프 함께 요청
2. `access_type=offline`으로 refresh_token 획득 및 Fernet 암호화 저장
3. FastAPI가 Open WebUI SQLite DB에서 토큰 복호화 → GWS Agent에 전달
4. ReAct 패턴으로 Gmail/Calendar/Drive API 직접 호출
5. 사용자별 개인 데이터만 접근 (OAuth2 per-user)

**키워드 라우팅**
- 일정, 캘린더, 회의, 스케줄 → Calendar
- 메일, 이메일, 받은편지함 → Gmail
- 드라이브, 파일, 문서, 공유 → Drive

## 4.4 Multi-Source Agent (복합 질문)

**동작 흐름**
1. 사용자 질문을 데이터 전용 BigQuery 쿼리로 분리 리라이팅
2. Google Search grounding (외부 정보) + BigQuery (내부 데이터) 병렬 실행
3. 두 결과를 합성하여 종합 답변 생성

## 4.5 프론트엔드 (Open WebUI)

- FastAPI를 통한 OpenAI API 규격 에뮬레이션으로 Open WebUI와 완벽 연동
- SKIN1004 브랜딩: 로고/파비콘 교체, cravercorp 벤치마킹 로그인 CSS
- Dual 모델 선택: `skin1004-Search` (Gemini) / `skin1004-Analysis` (Claude)
- Google Workspace SSO 연동 (200명 인증 관리, GWS 스코프 통합)
- 차트 시각화: ChatGPT 스타일 라이트 테마, 30색 팔레트, 자동 타입 선택

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

# 6. Agent & Module Design

## 6.1 Orchestrator (라우팅 엔진)

```
사용자 질문 → _keyword_classify()  ──매칭→ route_type 결정
                   │
                   └─미매칭→ Flash LLM classify() → route_type 결정
                                                        │
                   ┌────────────────────────────────────┘
                   │
          ┌────────▼────────┐
          │  route_type      │
          ├─────────────────┤
          │ bigquery → run_sql_agent()          │
          │ notion   → notion_agent.run()       │
          │ gws      → gws_agent.run()          │
          │ multi    → _handle_multi_source()    │
          │ direct   → main_llm.generate()       │
          └─────────────────────────────────────┘
```

## 6.2 에이전트별 처리 흐름

| 에이전트 | 주요 모듈 | 처리 흐름 | 사용 LLM |
|---------|----------|----------|---------|
| SQL Agent | sql_agent.py | generate_sql → validate_sql → execute_sql → format_answer (+chart) | Flash (SQL+답변), Main (차트) |
| Notion Agent (v6.2) | notion_agent.py | search_pages → read_blocks∥sheets (병렬) → generate_answer | Flash (검색), Main (답변) |
| GWS Agent (v4.2) | gws_agent.py | classify_service → call_api (OAuth2, recursion_limit=10) → format | Flash (분류), Main (답변) |
| Multi Agent | orchestrator.py | rewrite_query → [Google Search ∥ BigQuery] → synthesize | Flash (리라이트), Main (합성) |
| Direct | orchestrator.py | main_llm.generate() 직접 호출 | Main |

## 6.3 핵심 모듈

| 모듈 | 파일 | 설명 |
|------|------|------|
| Orchestrator | app/agents/orchestrator.py | 라우팅 + 에이전트 실행 관리 |
| SQL Agent | app/agents/sql_agent.py | Text-to-SQL + 차트 생성 |
| Notion Agent | app/agents/notion_agent.py | 허용 목록 검색 + 병렬 Sheets 읽기 (v6.2) |
| GWS Agent | app/agents/gws_agent.py | Gmail/Calendar/Drive OAuth2, recursion_limit=10 (v4.2) |
| Router | app/agents/router.py | 키워드 + Flash 라우팅 |
| LLM Client | app/core/llm.py | Dual LLM (Gemini/Claude) + Flash |
| BigQuery | app/core/bigquery.py | SQL 실행 + 스키마 캐싱 |
| Security | app/core/security.py | SQL 검증 (SELECT ONLY, whitelist) |
| Google Auth | app/core/google_auth.py | OAuth2 토큰 관리 + DB 읽기 |
| Chart | app/core/chart.py | ChatGPT 스타일 시각화 |

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
| Gemini 3 Pro Preview API | $100-250 | 메인 응답 (skin1004-Search) |
| Gemini 2.5 Flash API | $30-80 | SQL 생성, 라우팅 등 경량 작업 |
| Claude Opus 4.6 API | $50-200 | 메인 응답 (skin1004-Analysis) |
| BigQuery 스토리지 | $50-100 | 기존 인프라 활용 |
| BigQuery 쿼리 비용 | $50-150 | 온디맨드 과금 |
| Cloud Run (FastAPI) | $30-80 | Auto-scaling |
| Open WebUI (Docker) | $0 | 자체 호스팅 |
| Notion API | $0 | 무료 (Internal Integration) |
| Google Workspace API | $0 | 기존 Workspace 라이선스 포함 |
| **합계** | **$310-810/월** | GPT-4o 단일 모델 대비 1/3~1/5 수준 |

---

# 10. Expected Benefits

- **데이터 민주화**: SQL을 모르는 직원도 자연어로 매출 데이터 즉시 조회 + 자동 차트 시각화
- **정확도 극대화**: 매출 수치는 Text-to-SQL로 정확한 숫자 반환 (환각 Zero)
- **사내 문서 즉시 접근**: Notion 허용 목록 기반 검색 (~3초 워밍업, Google Sheets 자동 포함)
- **개인 업무 통합**: 단일 Google 로그인으로 AI가 내 일정/메일/드라이브까지 접근
- **Dual 모델 선택**: 상황에 맞는 LLM 선택 (Gemini=검색+속도, Claude=분석+정확도)
- **비용 효율**: GPT-4o 단일 모델 대비 1/3~1/5 수준의 API 비용
- **속도**: 키워드 우선 분류 + Flash 분리 + 병렬 처리로 BQ 18s, Notion 37s (v6.2)
- **확장성**: Orchestrator-Worker 구조로 에이전트 추가/로직 변경 용이

---

# 11. Environment Configuration

| 항목 | 값 |
|------|-----|
| GCP Project ID | skin1004-319714 |
| 메인 테이블 | skin1004-319714.Sales_Integration.SALES_ALL_Backup |
| RAG 데이터셋 | skin1004-319714.AI_RAG |
| JSON Key (개발용) | C:/json_key/skin1004-319714-60527c477460.json |
| 사용자 수 | 약 200명 |
| Main LLM (Search) | Gemini 3 Pro Preview (`gemini-3-pro-preview`, google-genai SDK) |
| Main LLM (Analysis) | Claude Opus 4.6 (`claude-opus-4-6`, langchain-anthropic SDK) |
| Fast LLM | Gemini 2.5 Flash (`gemini-2.5-flash`, 경량 작업 전용) |
| Notion Integration | Internal Integration (허용 목록 10개 페이지) |
| Google OAuth | SSO + GWS 스코프 통합 (gmail, calendar, drive readonly) |
| FastAPI Server | port 8100 |
| Open WebUI | Docker port 3000 |

---

# 12. Update Log

이 섹션은 시스템 업데이트 내역을 증분(incremental) 형태로 기록합니다. 최신 업데이트가 상단에 위치합니다.

---

## v6.2.0 - 2026.02.12

### 19.0 성능 기준 정의

QA 테스트 및 운영 품질 관리를 위한 응답 속도 기준을 정의.

| 분류 | 기준 | 조치 |
|------|------|------|
| **OK** (정상) | < 100초 | 정상 운영 |
| **WARN** (위험군) | >= 100초 | 원인 분석 후 개선 필요 |
| **FAIL** (실패) | >= 200초 | 반드시 수정 |

### 19.1 속도 최적화 — 전 도메인 병렬화 + Flash 전환

112개 QA 테스트에서 확인된 응답 속도 병목(BigQuery 평균 50s, Notion 평균 110s, GWS 평균 90s)을 8개 최적화로 해결.

#### 최적화 목록

| # | 최적화 | 적용 파일 | 상세 |
|---|--------|----------|------|
| 1 | Notion 3페이지 병렬 읽기 | notion_agent.py | `asyncio.gather()` — 3페이지 동시 읽기 |
| 2 | Notion Google Sheets 병렬 읽기 | notion_agent.py | 최대 2개 시트 동시 조회 |
| 3 | BQ 답변 포맷팅 Flash 전환 | sql_agent.py | `get_llm_client(Pro)` → `get_flash_client()` |
| 4 | GWS ReAct 반복 제한 | gws_agent.py | `recursion_limit=10` + GraphRecursionError 처리 |
| 5 | Notion 워밍업 병렬화 | notion_agent.py | 10개 타이틀 fetch를 `asyncio.gather()`로 동시 수행 |
| 6 | BQ 스키마 프리로드 | main.py | 서버 시작 시 BigQuery 스키마 미리 캐시 |
| 7 | BQ 차트 생성 Flash 전환 | sql_agent.py | 차트 JSON 설정 생성을 Pro → Flash로 전환 |
| 8 | "시장" 라우팅 오분류 수정 | orchestrator.py | `_EXTERNAL_KEYWORDS`에서 "시장" 제거 — 순수 데이터 쿼리의 false multi-routing 방지 |

#### 속도 개선 결과

| 도메인 | v6.1 평균 | v6.2 결과 | 개선율 |
|--------|----------|----------|--------|
| **BigQuery** | 50.0s | **18.5s** | **+63%** |
| **Notion** | 110.3s | **36.7s** | **+67%** |

#### 느린 쿼리 최종 재테스트 (기존 100~300초 쿼리 10개)

| ID | 도메인 | 원래 시간 | v6.2 최종 | 등급 |
|----|--------|----------|----------|------|
| NT-01 | Notion | 213.1s | **41.0s** | OK |
| NT-04 | Notion | 269.7s | **31.1s** | OK |
| R2-NT-02 | Notion | 205.6s | **32.8s** | OK |
| R2-NT-03 | Notion | 174.5s | **18.6s** | OK |
| R2-NT-12 | Notion | 127.6s | **45.0s** | OK |
| GWS-06 | GWS | 176.8s | **9.6s** | OK |
| GWS-03 | GWS | 169.9s | **23.1s** | OK |
| BQ-17 | BigQuery | 171.1s | **16.5s** | OK |
| R2-BQ-05 | BigQuery | 157.7s | **29.8s** | OK |
| BQ-16 | BigQuery | 130.2s | **27.5s** | OK |

**결과: 10/10 OK, 0 WARN, 0 FAIL** — 모든 쿼리 50초 이내

#### 변경 파일

| 파일 | 변경 내용 |
|------|----------|
| `app/agents/notion_agent.py` | v6.1 → v6.2: 병렬 page/sheet 읽기, 병렬 warmup |
| `app/agents/sql_agent.py` | format_answer() + chart generation Flash 전환 |
| `app/agents/gws_agent.py` | v4.1 → v4.2: recursion_limit=10, GraphRecursionError 처리 |
| `app/agents/orchestrator.py` | "시장" false multi-routing 수정 |
| `app/main.py` | BQ 스키마 프리로드 추가 |

### 19.2 종합 QA 테스트 (112개 쿼리, 4개 도메인)

3개 라운드 107개 질문 + 회귀 테스트 5개 = 총 112개 테스트 수행.

| 라운드 | BigQuery | Notion | GWS | Edge | 합계 | 성공률 |
|--------|----------|--------|-----|------|------|--------|
| **Round 1** (기본 기능) | 19/20 | 18/20 | 13/15 | - | 50/55 | 90.9% |
| **Round 2** (심화 분석) | 15/15 | 10/12 | 8/10 | - | 33/37 | 89.2% |
| **Round 3** (엣지케이스) | - | - | - | 15/15 | 15/15 | 100.0% |
| **회귀 테스트** | - | 3/3 | 1/1 | 1/1 | 5/5 | 100.0% |
| **최종 합계** | **34/35** | **31/35** | **22/26** | **16/16** | **103/112** | **92.0%** |

**실질적 버그: 5건 발견 → v6.1/v4.1에서 전수 수정 → 0건**

### 19.5 Round 4 다양성 테스트 (55개 쿼리, 새로운 변수)

기존 R1~R3(112개)과 겹치지 않는 새로운 변수 기반 테스트. 미사용 제품 라인, 국가, 메트릭, 팀, 시간 패턴 등을 집중 검증.

#### 테스트 변수 커버리지

| 도메인 | 수량 | 테스트 변수 |
|--------|------|------------|
| **BigQuery** | 25개 | 미테스트 제품라인(Hyalucica, Probiocica, Teatrica, Tone_Brightening, Poremizing), 미테스트 국가(태국, 베트남, 유럽, 중동, 대만, 싱가포르), 수량/FOC/주문건수 메트릭, 팀별(GM_EAST1/2, KBT, JBT, B2B1/2), MoM/QoQ 시간패턴, B2B 거래처, 제품타입(토너, 클렌징, 마스크, 패드), 교차분석 |
| **Notion** | 15개 | 세부 토픽 필터(구글애즈, 말레이시아, BigQuery), 크로스페이지(광고, 인도네시아), 모호한 질문(온보딩, 물류), 포맷 요청(테이블, 매트릭스) |
| **GWS** | 15개 | 읽지않은메일, 인보이스 검색, 첨부파일 필터, 반복일정, 이미지파일, 예산 검색, 크로스서비스(Shopee 전체), 다음달 일정 |

#### 테스트 결과

| 도메인 | OK | WARN | FAIL | 평균 응답 |
|--------|-----|------|------|----------|
| **BigQuery** | 25/25 | 0 | 0 | **21.3s** |
| **Notion** | 15/15 | 0 | 0 | **28.4s** |
| **GWS** | 15/15 | 0 | 0 | **19.0s** |
| **합계** | **55/55** | **0** | **0** | **22.5s** |

**100% 성공률, 전 쿼리 100초 미만**

### 19.3 Notion Agent v6.1 → v6.2 버그 수정 + 최적화

| 버전 | 수정 내용 |
|------|----------|
| v6.1 | (1) Sheet 읽기 30s timeout + max_rows=50, (2) httpx client 재생성 로직, (3) 검색어 구두점 제거 |
| v6.2 | (4) 병렬 page 읽기, (5) 병렬 sheet 읽기, (6) 병렬 warmup |

### 19.4 GWS Agent v4.1 → v4.2

| 버전 | 수정 내용 |
|------|----------|
| v4.1 | asyncio.wait_for(120s) — ReAct 타임아웃 방지 |
| v4.2 | recursion_limit=10 — 도구 호출 무한 반복 방지 + GraphRecursionError 우아한 처리 |

---

## v6.0.2 - 2026.02.12

### 18.1 SQL CTE 구문 오류 수정

#### 문제
Gemini Flash가 CTE(`WITH ... AS`)를 사용하는 SQL을 생성할 경우, `sanitize_sql()`이 `WITH` 절을 인식하지 못하고 제거하여 BigQuery에서 "Expected end of input but got ')'" 구문 오류 발생.

#### 해결
| 파일 | 수정 내용 |
|------|----------|
| `app/core/security.py` | `validate_sql()`에 `WITH` 시작 허용, `sanitize_sql()`에 CTE 추출 로직 추가 |
| `prompts/sql_generator.txt` | Rule 12 추가: CTE(WITH절) 사용 금지 — 서브쿼리/단일 SELECT 유도 |

### 18.2 제품 조회 라우팅 수정

#### 문제
"제품 리스트 알려줘", "전체 제품 목록 보여줘" 등 제품 목록 조회 쿼리가 BigQuery 대신 Direct LLM으로 라우팅되어 실제 데이터 대신 일반 답변 반환.

#### 해결
| 파일 | 수정 내용 |
|------|----------|
| `app/agents/orchestrator.py` | `_DATA_KEYWORDS`에 10개 제품 키워드 추가 ("제품 리스트", "제품 목록", "제품 종류", "전체 제품", "어떤 제품", "제품이 뭐", "제품 수", "몇 개 제품", "제품 현황", "제품 카테고리") |

### 18.3 차트 y축 문자열→숫자 변환 오류 수정

#### 문제
LLM이 차트 설정 시 `y_column`을 문자열 컬럼(제품명, 국가명, 팀명)으로 설정하여 `float()` 변환 시 에러 발생.

#### 해결
| 파일 | 수정 내용 |
|------|----------|
| `app/core/chart.py` | `_find_numeric_column()` 헬퍼 추가, `generate_chart()`에서 y_column 숫자 타입 검증 + 자동 보정 |

### 18.4 테스트 결과

10개 테스트 쿼리 **10/10 OK** (SQL-CTE 3, ROUTE 4, CHART 3)

| 카테고리 | 결과 | 비고 |
|----------|------|------|
| SQL-CTE | 3/3 OK | 분기별 매출비중, 대륙별 비중, 국가 상위 3개 |
| ROUTE | 4/4 OK | 제품 리스트/목록/종류/수 → BigQuery 라우팅 |
| CHART | 3/3 OK | 팀별/제품별/대륙별 차트 정상 생성 |

### 18.5 변경 파일 목록

| 파일 | 변경 유형 | 설명 |
|------|----------|------|
| app/core/security.py | 수정 | CTE 허용 + sanitize_sql CTE 추출 |
| app/core/chart.py | 수정 | y_column 숫자 검증 + 자동 보정 |
| app/agents/orchestrator.py | 수정 | 제품 키워드 10개 추가 |
| prompts/sql_generator.txt | 수정 | CTE 사용 금지 규칙 추가 |

---

## v6.0.1 - 2026.02.11

### 17.1 Notion API 연결 오류 수정 (Retry 로직)

#### 문제
8건 순차 테스트에서 3~5건 httpx ConnectError/ReadError/RemoteProtocolError 발생. 원인: 매 API 호출마다 새 `httpx.AsyncClient` 생성으로 연결 풀 고갈.

#### 해결
| 항목 | 구현 |
|------|------|
| 공유 클라이언트 | `run()` 단위로 `httpx.AsyncClient` 재사용 |
| 연결 풀링 | `max_connections=5, max_keepalive_connections=3` |
| 재시도 로직 | `_request_with_retry()`: 3회 재시도, 지수 백오프 (1s, 2s, 4s) |
| 리소스 정리 | `run()` finally 블록에서 `_close_client()` |

#### 재테스트 결과
- **성공률**: 8/8 (100%) — 이전 5/8 (62.5%)에서 개선
- **연결 오류**: 0건 (이전 3건)
- **retry 발생**: 0건 (공유 클라이언트 + 풀링만으로 해결)

### 17.2 LLM 모델 업그레이드 (Gemini 3 하이브리드)

#### 배경
Gemini 3 Pro/Flash Preview 출시에 따른 모델 업그레이드 테스트 수행. 3단계 비교 테스트 후 하이브리드 구성 확정.

#### 테스트 결과 요약

| 구성 | 평균 응답시간 | 정확도 | 품질 | 안정성 | 총평 |
|------|-------------|--------|------|--------|------|
| 2.5 Pro + Flash (기존) | 11.8s | 100% | ★★★★ | ★★★★★ | 빠르고 안정적 |
| 3 Pro + Flash (전면) | 60.9s | 60% | ★★★★ | ★★★ | Preview라 부적합 |
| **하이브리드 (최종)** | **17.6s** | **100%** | **★★★★★** | **★★★★★** | **최적 균형** |

#### 최종 확정 모델 구성

| 역할 | 모델 | Model ID |
|------|------|----------|
| 라우팅/SQL 생성/SQL 검증 | Gemini 2.5 Flash | `gemini-2.5-flash` |
| 답변 포맷팅 (Search) | Gemini 3 Pro Preview | `gemini-3-pro-preview` |
| 답변 포맷팅 (Analysis) | Claude Opus 4.6 | `claude-opus-4-6` |
| 차트 생성 | Gemini 3 Pro Preview | `gemini-3-pro-preview` |
| Search 최종답변 | Gemini 3 Pro Preview | `gemini-3-pro-preview` |
| Analysis 최종답변 | Claude Opus 4.6 | `claude-opus-4-6` |

#### 변경 파일

| 파일 | 변경 내용 |
|------|----------|
| `app/config.py` | `gemini_model` → `gemini-3-pro-preview` |
| `app/core/llm.py` | 독스트링 업데이트 |
| `app/agents/sql_agent.py` | `format_answer()` → `get_llm_client(model_type)`, 차트 → `get_llm_client(MODEL_GEMINI)` |

### 17.3 종합 QA 테스트 (56개 쿼리, 4개 도메인)

v6.0.0 + v6.0.1 적용 후 4개 도메인에 걸쳐 56개 테스트 쿼리를 수행.

| 도메인 | 쿼리 수 | 성공률 | 비고 |
|--------|---------|--------|------|
| Notion | 16 (8 pairs) | **8/8 성공 (v6.0.1)** | 연결 오류 해결, LLM 폴백 정상 |
| Sales (BigQuery) | 16 (8 pairs) | **16/16 성공** | 차트 자동 생성, 정확한 수치 |
| Product | 12 (6 pairs) | **8/12 양호** | 일부 direct LLM 폴백 → v6.0.2에서 수정 |
| GWS | 12 (6 pairs) | **12/12 성공** | OAuth2 정상, 실제 데이터 조회 |
| **합계** | **56** | **HTTP 100%** | 콘텐츠 품질 44/48 양호 |

#### 도메인별 평균 응답 시간

| 도메인 | 평균 응답 시간 |
|--------|--------------|
| GWS | 11.3s (최고) |
| Product | 24.3s |
| Sales (BigQuery) | 30.4s |
| Notion | 60.6s |

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

> **참고**: v6.0.1에서 Gemini 3 Pro Preview + Claude Opus 4.6으로 업그레이드됨 (Section 17.2 참조).

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
| Phase 2+: 차트 시각화 | **DONE** | Plotly ChatGPT 라이트 테마, 30색, 레전드 정렬, y축 자동 보정 (v4.0→v6.0.2) |
| Phase 2+: 스키마 관리 | **DONE** | Excel 기반 26개 컬럼 화이트리스트 |
| Phase 2+: 데이터 제한 확대 | **DONE** | 1,000행 → 10,000행 (v4.0) |
| Phase 2+: 속도 최적화 | **DONE** | 38s → 11s, Flash 분리, 병렬 처리, 캐싱 (v5.0) |
| Phase 2+: SQL CTE/라우팅 수정 | **DONE** | CTE 파싱 수정, 제품 키워드 추가 (v6.0.2) |
| Phase 3: RAG 파이프라인 | TODO | Docling 파서, BGE-M3 임베딩, BigQuery 벡터 인덱스 |
| Phase 4: API + 프론트엔드 | **DONE** | FastAPI + Open WebUI + Google SSO 완료 (v5.0) |
| Phase 4+: Dual LLM | **DONE** | Gemini 3 Pro Preview + Claude Opus 4.6 (v5.0→v6.0.1 업그레이드) |
| Phase 4+: Google Search | **DONE** | Gemini 네이티브 grounding (v5.0) |
| Phase 4+: GWS 개별 OAuth + SSO | **DONE** | 단일 Google 로그인으로 Gmail/Drive/Calendar (v5.0) |
| Phase 4+: 브랜딩 커스텀 | **DONE** | 로고, 로그인 CSS, 모델명 (v5.0) |
| Phase 4+: Notion v6.0 | **DONE** | 허용 목록 기반 검색, LLM 폴백, Sheets 자동 읽기 (v6.0) |
| Phase 4+: Notion v6.0.1 | **DONE** | 공유 httpx 클라이언트 + 연결 풀링 + 재시도 (v6.0.1) |
| Phase 5: 테스트 및 최적화 | **진행중** | v5.0 QA 13/13, v6.0.1 QA 56건, v6.0.2 QA 10/10 완료. RAGAS/부하 테스트 미완 |

---

# 13. QA Test Results

시스템 업데이트 시마다 수행된 QA 테스트 결과를 통합 정리한다. 최신 테스트가 상단에 위치한다.

---

## 13.1 v6.0.2 통합 수정 검증 (2026.02.12)

**테스트 목적**: SQL CTE 구문 오류, 제품 라우팅, 차트 y축 오류 수정 검증
**테스트 쿼리**: 10개 (SQL-CTE 3, ROUTE 4, CHART 3)

### 결과 요약

| 카테고리 | 쿼리 수 | 성공 | 실패 | 비고 |
|----------|---------|------|------|------|
| SQL-CTE | 3 | 3 | 0 | 분기별 매출비중, 대륙별 비중, 국가 상위3 |
| ROUTE | 4 | 4 | 0 | 제품 리스트/목록/종류/수 → BigQuery 라우팅 |
| CHART | 3 | 3 | 0 | 팀별/제품별/대륙별 차트 정상 생성 |
| **합계** | **10** | **10** | **0** | **성공률 100%** |

### 테스트 상세

| # | 태그 | 질문 | 결과 | 비고 |
|---|------|------|------|------|
| 1 | SQL-CTE-1 | 각 플랫폼 분기별 매출비중은 얼마야? | OK | CTE 없이 서브쿼리로 생성 |
| 2 | SQL-CTE-2 | 2025년 대륙별 매출 비중을 보여줘 | OK | 정확한 비중 계산 |
| 3 | SQL-CTE-3 | 올해 각 국가 매출에서 상위 3개 국가의 주요 판매 플랫폼은? | OK | 복합 쿼리 정상 |
| 4 | ROUTE-1 | 제품 리스트 알려줘 | OK | BigQuery 라우팅 |
| 5 | ROUTE-2 | 전체 제품 목록 보여줘 | OK | BigQuery 라우팅 |
| 6 | ROUTE-3 | 어떤 제품이 있어? | OK | BigQuery 라우팅 |
| 7 | ROUTE-4 | 제품 종류가 몇 개야? | OK | BigQuery 라우팅 |
| 8 | CHART-1 | 2025년 팀별 매출 비교 차트 그려줘 | OK | bar 차트 생성 |
| 9 | CHART-2 | 인도네시아 제품별 매출 top 5 보여줘 | OK | 차트 정상 |
| 10 | CHART-3 | 2025년 대륙별 매출 차트로 보여줘 | OK | 차트 정상 |

---

## 13.2 v6.0.1 종합 QA 테스트 (2026.02.11)

**테스트 목적**: Notion v6.0 + v6.0.1 retry 로직 적용 후 4개 도메인 전체 검증
**테스트 쿼리**: 56개 (28 pairs x 2 모델)
**테스트 환경**: FastAPI 8100, skin1004-Search (Gemini 2.5 Pro)

### 결과 요약

| 도메인 | 쿼리 수 | HTTP 성공률 | 콘텐츠 품질 | 비고 |
|--------|---------|-----------|------------|------|
| Notion | 16 (8 pairs) | 100% | **8/8 성공** | v6.0.1 retry로 연결 오류 해결 |
| Sales (BigQuery) | 16 (8 pairs) | 100% | **16/16 우수** | 차트 자동 생성, 정확한 수치 |
| Product | 12 (6 pairs) | 100% | 8/12 양호 | 일부 direct LLM 폴백 (v6.0.2에서 수정) |
| GWS | 12 (6 pairs) | 100% | **12/12 우수** | OAuth2 정상, 실제 데이터 조회 |
| **합계** | **56** | **100%** | **44/48** | 전체 HTTP 성공, 콘텐츠 92% 양호 |

### Notion 도메인 상세 (v6.0 + v6.0.1)

| # | 카테고리 | Q1 결과 | Q1 시간 | 콘텐츠 |
|---|---------|---------|---------|--------|
| 1 | 해외 출장 가이드 | OK | 85.0s | 에러 메시지 (v6.0.1 이전) |
| 2 | 틱톡샵 접속 | OK | 89.1s | 에러 메시지 (v6.0.1 이전) |
| 3 | 법인 태블릿 | OK | 80.1s | 에러 메시지 (v6.0.1 이전) |
| 4 | EAST 2026 업무파악 | **OK (Notion)** | 122.9s | 팀별 담당 업무 상세 |
| 5 | 광고 입력 업무 | **OK (Notion)** | 87.9s | 네이버 광고 입력 절차 |
| 6 | 데이터 분석 파트 | **OK (Notion)** | 49.9s | VM 접속 방법 |
| 7 | WEST 대시보드 | **OK (Notion+Sheets)** | 63.2s | 제품 목록 + Sheets 79개 항목 |
| 8 | LLM 폴백 (zombiepack) | **OK (Notion+Sheets)** | 84.9s | 번들 할인 정보 |

> **v6.0.1 retry 적용 후 재테스트**: 8/8 전체 성공 (이전 5/8). 연결 오류 0건, retry 발생 0건.

### Notion v6.0 기능 검증

| 기능 | 상태 | 비고 |
|------|------|------|
| 허용 목록 워밍업 | PASS | 10개 ID 타이틀 ~3초 로드 |
| 키워드 매칭 | PASS | "해외 출장", "틱톡샵", "법인 태블릿" 정확 매칭 |
| LLM 폴백 (Flash) | PASS | "zombiepack" → WEST 대시보드 자동 선택 |
| Google Sheets 자동 읽기 | PASS | 틱톡샵US 제품 마스터 79개 항목 조회 |
| UUID 변환 | PASS | 32자 → 8-4-4-4-12 자동 변환 |
| 타입 폴백 | PASS | database → page 자동 전환 |
| 접근 불가 페이지 필터링 | PASS | KBT, 네이버 (404) LLM 후보에서 제외 |

### Sales (BigQuery) 도메인 상세

| # | 카테고리 | Q1 결과 | Q1 시간 | 차트 |
|---|---------|---------|---------|------|
| 1 | 월별 매출 추이 | OK | 26.1s | line, bar |
| 2 | 플랫폼 비교 (아마존 vs 쇼피) | OK | 24.5s | bar |
| 3 | 국가별 분석 (동남아) | OK | 29.6s | bar |
| 4 | 제품 분석 (TOP 5) | OK | 29.0s | - |
| 5 | 팀별 실적 | OK | 29.9s | bar |
| 6 | 틱톡샵 US 월별 매출 | OK | 36.9s | line |
| 7 | 대륙별 매출 비중 | OK | 26.0s | pie |
| 8 | 전년 대비 (2024 vs 2025) | OK | 22.2s | bar |

**주요 데이터 검증**:
- 2025년 하반기 최고 매출: 11월 약 878억 원 (블랙프라이데이 시즌)
- 쇼피 vs 아마존: 쇼피 약 359억 (인도네시아 78%) > 아마존 약 22억
- 팀별 매출 1위: B2B1 (약 1,708억 원)
- 틱톡샵 US 2025년 총 매출: 약 76억 원 (11월 최고 37.5억)
- 차트 자동 생성: 8개 테스트 중 6개 차트 생성 (line, bar, pie)

### GWS 도메인 상세

| # | 카테고리 | Q1 결과 | Q1 시간 | 서비스 |
|---|---------|---------|---------|--------|
| 1 | 오늘 일정 | OK | 8.4s | Calendar |
| 2 | 내일 회의 | OK | 7.5s | Calendar |
| 3 | 최근 중요 메일 | OK | 18.5s | Gmail |
| 4 | 드라이브 최근 파일 | OK | 18.8s | Drive |
| 5 | 메일 검색 | OK | 20.9s | Gmail |
| 6 | 다음 주 일정 | OK | 7.9s | Calendar |

- **전체 12/12 성공** (100%)
- OAuth2 per-user 토큰 정상 작동
- 실제 사용자 메일/일정/파일 정확 조회

### 도메인별 평균 응답 시간

| 도메인 | Q1 평균 | Q2 평균 | Pair 평균 |
|--------|---------|---------|----------|
| GWS | 13.3s | 9.3s | 22.6s |
| Product | 17.1s | 31.4s | 48.5s |
| Sales (BigQuery) | 28.0s | 32.8s | 60.8s |
| Notion | 82.9s | 38.2s | 121.1s |
| **전체** | **35.3s** | **27.9s** | **65.9s** |

---

## 13.3 모델 업그레이드 테스트 (2026.02.11)

**테스트 목적**: Gemini 2.5 → Gemini 3 모델 업그레이드 영향 분석 및 최적 구성 도출
**테스트 쿼리**: 6개 (동일 쿼리를 3개 구성으로 비교)

### 3단계 비교 구성

| 구성 | 메인 LLM | Flash LLM | 답변 포맷팅 |
|------|---------|-----------|-----------|
| 기존 (2.5 전체) | Gemini 2.5 Pro | Gemini 2.5 Flash | Flash |
| Round 1 (3 전면) | Gemini 3 Pro | Gemini 3 Flash | Flash 3 |
| Round 2 (하이브리드) | Gemini 3 Pro | Gemini 2.5 Flash | Pro 3 |

### 응답 시간 비교 (초)

| 테스트 | 기존 (2.5) | Round 1 (3 전면) | Round 2 (하이브리드) |
|--------|-----------|-----------------|-------------------|
| Search BQ 전체매출 | 12.1 | 171.6 | 22.4 |
| Search BQ 분기별 | 17.8 | 64.6 | 26.5 |
| Search Direct | ~6.0 | 33.9 | 13.0 |
| Search GWS Calendar | 10.6 | 8.1 | 8.3 |
| Analysis BQ B2B/B2C | 17.6 | 39.1 | 19.1 |
| Analysis Direct | 6.5 | 47.9 | 16.5 |
| **평균** | **11.8** | **60.9** | **17.6** |

### 정확도 비교

| 테스트 | 기존 (2.5) | Round 1 (3 전면) | Round 2 (하이브리드) |
|--------|-----------|-----------------|-------------------|
| 전체 매출 합계 | 417.8억 (정확) | 271.5억 (상위20만) | 417.8억 (정확) |
| 분기별 매출 | 4분기 전체 | 1분기만 반환 | 4분기 전체 (정확) |
| 리스트 컴프리헨션 | 정상 | 정상 | 정상 |
| 이번주 일정 | 정상 | 정상 | 정상 |
| B2B vs B2C | 72.1% vs 27.9% | 72.1% vs 27.9% | 72.1% vs 27.9% (정확) |
| 차트 생성 | 정상 | 일부 생성 | 정상 |

### Round 1 발견 문제 (Gemini 3 전면)

| 문제 | 상세 |
|------|------|
| Gmail 타임아웃 | GWS Gmail 검색 182초 → 180초 타임아웃 초과 |
| SQL 생성 품질 저하 | "전체 매출 합계" → 상위 20개 제품별로 해석 (잘못된 SQL) |
| 분기 데이터 누락 | 4분기 요청 → 1분기만 반환 |
| Direct 극단적 지연 | 단순 설명 질문에 34~60초 소요 |

### 분석 결론

| 구성 | 속도 | 정확도 | 품질 | 안정성 | 총평 |
|------|------|--------|------|--------|------|
| 2.5 Pro + Flash (기존) | 5점 | 5점 | 4점 | 5점 | 빠르고 안정적 |
| 3 Pro + Flash (전면) | 2점 | 3점 | 4점 | 3점 | Preview라 부적합 |
| **하이브리드 (최종)** | **4점** | **5점** | **5점** | **5점** | **최적 균형** |

> **결론**: Flash는 2.5 유지 (속도+SQL 정확도), 답변/차트는 Pro 3 사용 (품질 향상). 평균 +49% 속도 증가(11.8s→17.6s)이나 답변 품질 향상으로 상쇄.

---

## 13.4 v5.0.0 QA 테스트 (2026.02.10)

**테스트 목적**: Dual LLM + GWS OAuth2 + Google Search 통합 후 전체 검증
**테스트 쿼리**: 13개 (5개 경로)
**테스트 환경**: FastAPI 8100 + Open WebUI 3000

### 결과 요약

| 경로 | 테스트 수 | 성공 | 실패 | 비고 |
|------|-----------|------|------|------|
| BigQuery | 4 | 4 | 0 | 데이터 없는 경우 안내 메시지 |
| GWS (Calendar) | 1 | 1 | 0 | 실제 일정 조회 성공 |
| GWS (Gmail) | 1 | 1 | 0 | 실제 메일 10건 조회 |
| GWS (Drive) | 2 | 2 | 0 | 파일 검색 + 링크 제공 |
| Multi | 1 | 1 | 0 | 내부+외부 종합 분석 |
| Direct | 3 | 3 | 0 | 일반 질문 정상 응답 |
| Notion | 1 | 1 | 0 | MCP 연결 성공 |
| **합계** | **13** | **13** | **0** | **전체 성공률 100%** |

### 모델 동등성 테스트 (Search vs Analysis)

| 테스트 | skin1004-Search (Gemini) | skin1004-Analysis (Claude) |
|--------|-------------------------|---------------------------|
| Google Drive 검색 | 공매분석 시트 발견 | 공매분석 시트 발견 (동일) |
| 일반 질문 (날씨) | 지역 확인 요청 | 전국 날씨 상세 (더 상세) |
| BigQuery 매출 | 조회 결과 없음 안내 | 데이터 없음 친절 안내 |

### 경로별 상세 결과

| # | 경로 | 질문 | 모델 | 응답시간 | 결과 |
|---|------|------|------|---------|------|
| 1 | BigQuery | 2024년 태국 쇼피 월별 매출 추이 | Search | 10.8s | 데이터 없음 안내 |
| 2 | GWS Calendar | 이번주 일정 알려줘 | Analysis | 10.6s | 일정 5건 조회 |
| 3 | GWS Gmail | 최근 받은 메일 보여줘 | Search | 21.0s | 메일 10건 조회 |
| 4 | BigQuery | 인도네시아 쇼피 vs 베트남 쇼피 매출 비교 | Analysis | 18.2s | 204.9억 정확 비교 |
| 5 | GWS Drive | 마케팅 관련 파일 찾아줘 | Search | 19.5s | 파일 10개 검색 |
| 6 | Multi | 태국 매출 왜 떨어졌는지 분석 | Analysis | 143.6s | 내부+외부 종합 리포트 |
| 7 | Direct | 오늘 날씨 어때? | Search | 5.9s | 위치 확인 후 제공 |
| 8 | Notion | SKIN1004 제품 정보 찾아줘 | Analysis | 53.0s | Notion 연결 성공 |

### 평균 응답 시간

| 경로 | 평균 응답시간 |
|------|--------------|
| Direct | 5.9s |
| BigQuery | 14.5s |
| GWS | 17.0s |
| Notion | 53.0s |
| Multi | 143.6s |

---

## 13.5 QA 테스트 누적 요약

### 버전별 테스트 이력

| 버전 | 일자 | 테스트 수 | 성공률 | 주요 검증 항목 |
|------|------|-----------|--------|--------------|
| v5.0.0 | 2026.02.10 | 13 | **100%** | Dual LLM, GWS OAuth, 5경로 라우팅 |
| v6.0.1 | 2026.02.11 | 56 | **100% (HTTP)** | Notion v6.0, retry, 4개 도메인 종합 |
| 모델 업그레이드 | 2026.02.11 | 18 (6x3) | Round2: **100%** | Gemini 3 하이브리드 구성 검증 |
| v6.0.2 | 2026.02.12 | 10 | **100%** | SQL CTE, 라우팅, 차트 수정 검증 |
| **누적** | - | **97건** | **95%+** | 전체 시스템 안정성 확인 |

### 도메인별 누적 성공률

| 도메인 | 총 테스트 | 성공 | 성공률 |
|--------|----------|------|--------|
| BigQuery (Sales) | 34 | 34 | **100%** |
| GWS (Calendar/Gmail/Drive) | 19 | 19 | **100%** |
| Notion | 16 | 13 | **81%** (v6.0.1 이전 3건 오류, 이후 100%) |
| Product | 16 | 14 | **88%** (v6.0.2 이후 100%) |
| Direct | 6 | 6 | **100%** |
| Multi | 1 | 1 | **100%** |

### 현재 시스템 안정성 (v6.0.2 기준)

- BigQuery SQL 정확도: **100%** (CTE 오류 수정 완료)
- 차트 자동 생성: **정상** (y축 자동 보정 추가)
- Notion 연결 안정성: **100%** (retry 로직 적용)
- GWS OAuth2: **100%** (per-user 토큰 정상)
- 라우팅 정확도: **95%+** (제품 키워드 추가 완료)
- 평균 응답 시간: BigQuery 17-30s, GWS 8-20s, Notion 45-90s

---

**End of Document**
