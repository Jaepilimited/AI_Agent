# Cluster 18

> Auto-generated 2026-09-09T03:00:59.448358+09:00 · Files: 5

## Purpose
본 클러스터는 SKIN1004 AI Agent 프로젝트에서 BigQuery 기반의 Text-to-SQL 변환, 실행 및 결과 검증을 담당하는 핵심 엔진과 데이터 품질 관리 로직을 포함합니다. 자연어 질의를 SQL로 변환하는 에이전트 워크플로우와 함께, 실제 데이터 조회 시 발생할 수 있는 이상치(Outlier) 및 0건(Zero-row) 결과에 대한 실측 검증 로직을 제공하여 답변의 신뢰성을 극대화합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/agents/sql_agent.py` — LangGraph 기반의 정형화된 Text-to-SQL 에이전트 (generate → validate → execute → format 파이프라인 구현)
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/agents/sql_tool_agent.py` — `BQ_TOOL_LOOP=1` 환경변수로 활성화되는 실험용 단일 세션 도구 사용(Tool-use) BigQuery 에이전트
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/logistics_quality.py` — 수출 물류(`Export_control.export_logistics`) 데이터의 수량 이상치를 감지하고 공시하는 품질 관리 모듈
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/zero_row.py` — 쿼리 결과가 0행일 때 LLM의 환각(Hallucination)을 방지하기 위해 어떤 필터 조건이 원인인지 실측하는 모듈
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/superpowers/plans/2026-07-09-performance-optimization-findings.md` — BigQuery 쿼리 성능 최적화 및 감사 결과 기록 문서

## Key Concepts
- **Zero-row Verification (0행 실측)**: 쿼리 결과가 0건일 때 LLM이 임의로 원인을 지어내지 않도록, `zero_row.py`를 통해 실제 어떤 필터(예: 국가명, 바이어명 등)가 존재하지 않거나 잘못되었는지 데이터베이스 수준에서 직접 검증합니다.
- **Logistics Outlier Detection (물류 이상치 공시)**: 프롬프트 지시만으로는 제어하기 어려운 수출 물류 데이터의 비정상적 수량(예: 억 단위 이상치)을 `logistics_quality.py` 코드 레벨에서 직접 판별하여 답변에 강제로 경고를 포함시킵니다.
- **SQL Agent Workflow**: LangGraph를 활용하여 SQL 생성(`generate_sql`), 문법 및 보안 검증(`validate_sql`), 실행(`execute_sql`), 최종 답변 작성(`format_answer`) 단계를 체계적으로 제어합니다.

## How It Fits In
- **Cluster 38 (SQL Generation & LangGraph)**: `sql_agent.py`는 Cluster 38의 `text_to_sql` 및 `langgraph_sql_agent` 개념을 구체적으로 구현한 실체입니다.
- **Cluster 06 (SQL Sanitization)**: SQL 실행 전 안정성을 확보하기 위해 Cluster 06의 `sql_sanitization` 규칙을 적용하여 유효성을 검증합니다.
- **Cluster 12 (Partition Filter Bypass)**: 성능 최적화 문서(`2026-07-09-performance-optimization-findings.md`)는 BigQuery 파티션 필터 우회(`partition_filter_bypass`) 문제를 해결하고 쿼리 비용을 최적화하는 방안을 제시하며 Cluster 12와 연결됩니다.

## Common Questions This Page Answers
- **Q1. 자연어 질의 결과가 0건일 때 LLM이 거짓 원인을 지어내는 문제를 어떻게 방지하나요?**
  - `zero_row.py` 모듈이 작동하여, 쿼리에 사용된 필터 조건을 쪼개어 실제 DB에 매칭되는 값이 있는지 단계별로 실측하고 정확한 원인을 리턴합니다.
- **Q2. 수출 물류 데이터 조회 시 비정상적으로 큰 수량이 조회되는 이상치 문제는 어떻게 처리하나요?**
  - `logistics_quality.py`에서 코드 레벨로 최대 실측치(예: 한 선적당 최대 1,332,166개)를 초과하는 억 단위 수량이 감지되면 답변에 이상치 공시를 강제 삽입합니다.
- **Q3. `sql_tool_agent.py`는 언제 사용되나요?**
  - 개발 환경에서 `BQ_TOOL_LOOP=1` 설정 시 활성화되며, 기존의 고정된 파이프라인 대신 LLM이 도구를 직접 호출하며 루프를 도는 실험적 에이전트 역할을 수행합니다.