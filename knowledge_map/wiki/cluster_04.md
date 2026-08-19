# Cluster 04

> Auto-generated 2026-08-19T03:00:36.499205+09:00 · Files: 9

## Purpose
이 클러스터는 SKIN1004 AI Agent의 핵심 데이터 처리, 상태 관리 및 RAG(Retrieval-Augmented Generation) 파이프라인을 담당합니다. 사용자의 질문에 대해 정확한 데이터를 조회하고, 대화의 맥락(Turn State)을 유지하며, 조회 결과가 없을 때 원인을 분석하고(Zero-row), 최종적으로 사용자에게 구조화된 판정 결과와 시각화 차트를 제공하는 핵심 비즈니스 로직을 포함하고 있습니다.

## Key Files
- `app/core/chart.py` — 서버 부하가 큰 PNG 렌더링 대신 프론트엔드에서 대화형으로 렌더링할 수 있는 Chart.js 설정 JSON 생성기
- `app/core/embeddings.py` — 벡터 검색 및 RAG를 위한 BGE-M3 임베딩 모델 인터페이스
- `app/core/turn_state.py` — 대화 턴 간의 조회 상태를 구조적으로 유지하여, 이전 쿼리 조건 상속 및 다중 턴 맥락 유지를 가능하게 하는 상태 관리 모듈
- `app/core/zero_row.py` — 데이터 조회 결과가 0건일 때, LLM의 환각(Hallucination)을 방지하기 위해 어떤 필터 조건이 원인인지 실제로 측정하고 분석하는 모듈
- `app/models/schemas.py` — OpenAI 호환 API 규격을 위한 Pydantic 요청/응답 스키마 정의
- `app/models/state.py` — LangGraph 에이전트 워크플로우에서 사용되는 전역 상태(State) 정의
- `app/rag/chunker.py` — RAG 성능 극대화를 위한 Semantic 및 Hierarchical 하이브리드 청킹 모듈
- `app/rag/parser.py` — Docling을 활용하여 PDF, HWP, PPT 등 다양한 문서를 마크다운으로 변환하는 파서
- `app/reports/judge.py` — 단순 표 출력을 넘어 데이터분석파트의 보고서 스타일을 차용하여 명확한 결론(Key Message)을 도출하는 판정 계층

## Key Concepts
- **Turn State (조회 상태)** — 이전 대화의 SQL 앵커나 단순 텍스트 뭉치에 의존하지 않고, 사용자가 거쳐온 조회 조건과 맥락을 구조화된 상태로 들고 가며 후속 질문에 대응합니다.
- **Zero-row 실측** — 데이터가 0건 조회되었을 때 LLM이 거짓 원인을 지어내지 않도록, 시스템이 직접 필터 조건을 역추적하여 "어느 필터가 범인인지" 명확하게 판정합니다.
- **판정 계층 (Judge Layer)** — 사용자가 표를 직접 읽고 해석하게 만드는 대신, 시스템이 데이터를 분석하여 "이 장에서 무엇이 결론인가"에 대한 Key Message를 선제적으로 제시합니다.

## How It Fits In
이 클러스터는 에이전트의 '두뇌'와 '데이터 파이프라인' 역할을 동시에 수행합니다. `app/models/state.py`의 LangGraph 상태를 기반으로 전체 워크플로우가 구동되며, `app/rag` 패키지의 파서와 청커가 지식 베이스를 구축하면 `app/core/embeddings.py`가 이를 벡터화합니다. 데이터 조회 시에는 `app/core/turn_state.py`와 `app/core/zero_row.py`가 대화의 맥락과 예외 상황을 통제하고, 최종 출력 단계에서 `app/reports/judge.py`와 `app/core/chart.py`가 결합되어 시각적이고 직관적인 분석 보고서를 완성합니다.

## Common Questions This Page Answers
- 데이터 조회 결과가 0건(Zero-row)일 때 LLM의 환각 답변을 어떻게 방지하나요?
- 이전 대화 턴의 복잡한 SQL 조회 조건을 다음 질문에서도 유지하려면 어떻게 해야 하나요?
- 보고서 출력 시 단순 데이터 나열을 넘어 분석적 결론(Key Message)을 어떻게 도출하나요?