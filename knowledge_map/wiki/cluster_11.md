# Cluster 11

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 7

## Purpose
이 클러스터는 Text-to-SQL 쿼리 실행 엔진, CS 주문 조회 및 데이터 적재 검증 로직, 그리고 RAG 인덱싱 파이프라인의 핵심 구성 요소를 다룹니다. 안전하고 결정론적인 SQL 생성과 CS 데이터 조회를 지원하며, BigQuery 기반의 벡터 검색을 위한 청킹 및 인덱싱 기능을 제공합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/agents/sql_agent.py` — LangGraph 기반 Text-to-SQL Agent (`generate_sql` → `validate_sql` → `execute_sql` → `format_answer` 워크플로우).
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/agents/sql_tool_agent.py` — `BQ_TOOL_LOOP=1` 환경변수로 활성화되는 실험적 single-session tool-use BigQuery agent.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/cs_freshness.py` — CS 자료 적재 현황 및 데이터 최신성(freshness) 공시 모듈.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/cs_order_details.py` — LLM 추론이나 캐시 없이 단일 외부 주문 번호의 처리 내역을 조회하는 결정론적(deterministic) 국내 CS 주문 상세 조회 모듈.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/security.py` — 생성된 SQL이 실행되기 전 통과해야 하는 SQL safety validation 모듈.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/rag/chunker.py` — RAG를 위한 Semantic 및 Hierarchical 하이브리드 청킹 모듈.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/rag/indexer.py` — BigQuery 벡터 인덱싱 및 임베딩 적재 파이프라인.

## Key Concepts
- **Text-to-SQL Pipeline**: 자연어 질문을 SQL로 변환 후 안전성 검증을 거쳐 실행하는 파이프라인 (`sql_agent.py`, `sql_tool_agent.py`).
- **SQL Safety Validation**: 실행 전 악의적이거나 위험한 쿼리를 차단하는 보안 검증 레이어 (`security.py`).
- **Deterministic CS Routing**: 모델 추론 없이 외부 주문 번호 매칭으로 정확한 세부 내역 테이블을 즉시 반환하는 무모델 조회 방식 (`cs_order_details.py`).
- **CS Freshness Monitoring**: 실제 적재된 CS 데이터의 시점을 추적하여 대시보드 및 리포트 수치 불일치를 방지하는 적재 공시 (`cs_freshness.py`).
- **Hybrid Chunking & Vector Indexing**: Semantic/Hierarchical 청킹 기법과 BigQuery Vector Search 인덱싱 결합 (`chunker.py`, `indexer.py`).

## How It Fits In
- **Cluster 25**: `sql_agent.py`는 Cluster 25의 `concept:langgraph_sql_agent`를 구현하며, `indexer.py`는 `concept:rag_pipeline`을 구현합니다.
- **Cluster 04**: `cs_order_details.py`는 Cluster 04의 `concept:deterministic_routing` 원칙을 구현하여 LLM hallucination 없는 안전한 CS 주문 조회를 보장합니다.

## Common Questions This Page Answers
- LangGraph 기반 Text-to-SQL 파이프라인의 구체적인 단계(`generate` → `validate` → `execute` → `format`)는 어떻게 구성되어 있는가?
- 실험적 기능인 `sql_tool_agent.py`는 어떤 플래그(`BQ_TOOL_LOOP=1`)로 활성화되는가?
- 생성된 SQL 쿼리는 실행 전에 어떤 모듈(`security.py`)을 거쳐 보안 검증을 받는가?
- 모델 추론 없이 외부 주문 번호로만 단일 CS 주문 상세 내역을 조회하는 로직은 어디에 있는가?
- RAG 문서 임베딩을 BigQuery에 인덱싱하고 하이브리드로 청킹하는 파일은 무엇인가?