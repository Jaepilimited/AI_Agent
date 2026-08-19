# Cluster 11

> Auto-generated 2026-08-19T03:00:36.499205+09:00 · Files: 3

## Purpose
이 클러스터는 SKIN1004 프로젝트에서 BigQuery 데이터를 조회하고 활용하기 위한 핵심 데이터 접근 레이어를 제공합니다. 자연어 질의를 SQL로 변환하여 실행하는 Text-to-SQL 에이전트 파이프라인과, RAG(Retrieval-Augmented Generation) 검색 성능 향상을 위한 BigQuery 벡터 인덱싱 기능을 포함하고 있습니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/agents/sql_agent.py` — LangGraph 기반의 정형화된 Text-to-SQL 워크플로우(`generate_sql` → `validate_sql` → `execute_sql` → `format_answer`)를 수행하는 에이전트입니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/agents/sql_tool_agent.py` — `BQ_TOOL_LOOP=1` 환경 변수로 활성화되는 실험용 단일 세션 도구 사용(Tool-use) BigQuery 에이전트입니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/rag/indexer.py` — RAG 임베딩 데이터를 BigQuery에 저장하고 벡터 검색 인덱스를 생성 및 관리하는 인덱서입니다.

## Key Concepts
- **Text-to-SQL 파이프라인**: 사용자의 자연어 질문을 BigQuery 호환 SQL로 변환하고, 검증 및 실행을 거쳐 최종 답변을 포맷팅하는 다단계 LangGraph 워크플로우입니다.
- **실험적 Tool-use 에이전트**: 고정된 파이프라인 대신 LLM이 직접 BigQuery 도구를 반복 호출하며 문제를 해결하는 에이전트 루프 방식입니다.
- **BigQuery Vector Search**: RAG 시스템의 빠른 유사도 검색을 지원하기 위해 BigQuery 내에 벡터 인덱스를 구축하는 기술입니다.

## How It Fits In
이 클러스터는 SKIN1004 AI Agent의 데이터 조회 및 검색의 중추 역할을 합니다.
- **`cluster_30` (Text-to-SQL & Vector Search)**: `sql_agent.py`는 `concept:text_to_sql` 및 `concept:langgraph_sql_agent`를 구체적으로 구현하며, `indexer.py`는 `concept:bigquery_vector_search` 기술을 실현합니다.
- **`cluster_29` (SQL Cache & Partition Filter)**: SQL 실행 최적화를 위해 `concept:sql_cache`를 활용하고, `sql_tool_agent.py` 내부에서 BigQuery 비용 절감을 위한 `concept:partition_filter_enforcement` 규칙을 강제합니다.
- **`cluster_08` (BigQuery Client)**: BigQuery 데이터베이스와의 실제 연결 및 쿼리 전송을 위해 `concept:bigquery_client` 인프라를 공통으로 사용합니다.

## Common Questions This Page Answers
- **Q1: Text-to-SQL 에이전트의 전체 실행 단계는 어떻게 되나요?**
  - `sql_agent.py` 내에서 `generate_sql` (생성) → `validate_sql` (검증) → `execute_sql` (실행) → `format_answer` (답변 구성) 순서의 LangGraph 노드를 거쳐 처리됩니다.
- **Q2: 실험적인 BigQuery Tool-use 에이전트를 활성화하려면 어떻게 해야 하나요?**
  - 환경 변수 `BQ_TOOL_LOOP=1`을 설정하면 `run_sql_agent_stream` 호출 시 `sql_tool_agent.py` 기반의 루프가 활성화됩니다.
- **Q3: RAG용 벡터 데이터는 어떻게 인덱싱되나요?**
  - `indexer.py`를 통해 BigQuery 테이블에 임베딩 벡터가 업로드되고, 효율적인 근사 최근접 이웃(ANN) 검색을 위한 벡터 인덱스가 생성됩니다.