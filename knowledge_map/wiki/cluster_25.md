# Cluster 25: Enterprise AI Architecture, Routing & Update History

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 47

## Purpose
SKIN1004 Enterprise AI Agent의 시스템 진화 과정(PRD, 업데이트 로그, QA 리포트)과 라우팅/상태 관리 코어를 다루는 문서 및 기반 모듈 클러스터입니다. 비즈니스 쿼리를 분석해 적절한 데이터 소스로 전달하는 라우터 판정 로직, 엔터프라이즈 프롬프트 포맷, 워크플로우 상태 정의, 그리고 BigQuery 성능 최적화와 보안 거버넌스 사양을 망라합니다.

## Key Files
- `app/models/state.py` — LangGraph 워크플로우 전반의 공통 agent state 정의
- `app/core/route_intent.py` — orchestrator 분리를 위한 1단계 라우팅 순수 함수 (`source gate` 판정)
- `app/core/prompt_fragments.py` — 다중 에이전트 전반에 재사용되는 엔터프라이즈 포맷 프롬프트 조각
- `docs/ROUTING_TRIGGERS.md` — 질문 의도에 따른 데이터 소스 라우팅 매핑 가이드
- `docs/FI_ACCESS_CONTROL.md` — 재무 손익(FI) 데이터 접근 제어 및 권한 통제 구현 계획
- `docs/SKIN1004_AI_Technical_Architecture.md` — 시스템 전반의 기술 아키텍처 사양
- `docs/SKIN1004_Enterprise_AI_PRD_v6.md` — 엔터프라이즈 AI 시스템 최신 제품 요구사항 명세서
- `docs/superpowers/specs/2026-04-20-bigquery-performance-design.md` — BigQuery 응답 속도 개선 설계
- `docs/superpowers/specs/2026-07-07-nightly-debug-system-design.md` — 야간 자동 디버깅 및 자가 개선 시스템 사양

## Key Concepts
- **Source Gate Router**: `orchestrator.py` 비대화를 방지하고, 사용자 질문이 외부/내부 데이터 소스를 필요로 하는지 1단계에서 순수 함수(`route_intent.py`)로 분기 처리하는 구조.
- **FI Access Control**: 재무 손익 데이터 접근 권한을 관리하여 엔터프라이즈 거버넌스를 준수하는 보안 체계.
- **Wide to Long Ad Migration**: 통합 마케팅 광고 테이블을 wide 형태에서 long 형태로 구조화하여 쿼리 효율을 높이는 작업 (`2026-04-17-integrated-ad-migration-design.md`).
- **Nightly Debug System**: 일과 후 자동 검증 및 이상 징후 분석을 수행하는 야간 디버깅 파이프라인.

## How It Fits In
- **Cluster 24 (Security & RBAC)**: `app/models/state.py` 내 사용자 context를 통해 역할 기반 접근 제어(`concept:role_based_access_control`)를 수행합니다.
- **Cluster 18 (Performance & Benchmarks)**: `2026-04-20-bigquery-performance-design.md`는 SSE 스트리밍(`concept:sse_streaming`)과 연계되며, QA 보고서는 벤치마크(`concept:qa_300_benchmark`) 지표를 추적합니다.
- **Cluster 29 (Routing Guards)** & **Cluster 31 (Anti-Hallucination)**: `docs/update_log_2026-03-16.md`에 기술된 라우팅 가드와 환각 방지 검증 체계와 연결되어 정확도를 보장합니다.

## Common Questions This Page Answers
- 사용자 질문이 어떤 기준으로 광고 마케팅, 재무, 일반 응답으로 분기되는가? (`docs/ROUTING_TRIGGERS.md`, `app/core/route_intent.py`)
- 재무 손익(FI) 민감 데이터의 열람 권한 및 LLM 외부 유출 방지는 어떻게 설계되어 있는가? (`docs/FI_ACCESS_CONTROL.md`, `docs/SECURITY_LLM_API_DATA_GOVERNANCE.md`)
- BigQuery 조회 지연을 줄이기 위한 구조 개선 및 성능 최적화 히스토리는 무엇인가? (`docs/superpowers/specs/2026-04-20-bigquery-performance-design.md`)