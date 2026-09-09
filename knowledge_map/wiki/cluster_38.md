# Cluster 38

> Auto-generated 2026-09-09T03:00:59.448358+09:00 · Files: 19

## Purpose
본 클러스터는 SKIN1004 Enterprise AI Agent의 핵심 설계 사상, 보안 아키텍처, 제품 요구사항 정의서(PRD), 그리고 에이전트의 피드백 기반 학습 메커니즘을 다루는 핵심 문서 및 설정 파일들의 집합입니다. 시스템의 전반적인 기술 아키텍처와 업데이트 이력, 그리고 방문자 분석 및 재무 데이터 접근 제어 규칙을 정의합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/agents/skill_memory.py` — Hermes 스타일의 스킬 메모리 구현체로, 긍정/부정 피드백을 `agent_skills` 테이블에 저장하고 few-shot 및 피해야 할 패턴을 시스템 프롬프트에 주입하는 역할
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/flow/spec.py` — 실제 실행 함수와 레지스트리를 가리키는 노드 기반의 흐름(Flow) 선언 명세서
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/visitor_access.py` — 방문자 분석 뷰에 대한 좁은 범위의 공유 권한 규칙 정의
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/SKIN1004_Enterprise_AI_PRD_v6.md` — SKIN1004 Enterprise AI 시스템의 최신 제품 요구사항 정의서(PRD)
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/SKIN1004_Security_Architecture.md` — 시스템의 보안 및 인증 아키텍처 문서
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/FI_ACCESS_CONTROL.md` — 재무 손익(FI) 데이터 열람 권한 통제 및 구현 계획서

## Key Concepts
- **Hermes-style Skill Memory** — 사용자의 피드백(👍/👎)을 기반으로 에이전트의 행동을 동적으로 개선하는 메커니즘입니다. 우수 답변은 few-shot 예시로, 부정 피드백은 피해야 할 패턴으로 시스템 프롬프트에 실시간 반영됩니다.
- **접근 제어 (Access Control)** — 재무 손익(FI) 데이터 및 방문자 분석 데이터와 같은 민감한 정보에 대해 엄격한 권한 통제 규칙을 적용하여 보안을 강화합니다.
- **Flow Specification** — 하드코딩된 하위 단계 없이, 런타임에서 LangGraph 서브그래프를 추출하여 동적으로 실행 노드를 연결하는 흐름 정의 방식입니다.

## How It Fits In
본 클러스터는 시스템의 설계 뼈대와 보안 표준을 제공하며 타 클러스터와 다음과 같이 긴밀히 연결됩니다:
- `app/flow/spec.py`는 **cluster_27**의 `concept:flow_specification`을 구체적으로 구현합니다.
- 보안 아키텍처 문서(`Craver_Security_Architecture_2026-04-22.md`)는 **cluster_05**의 `concept:jwt_authentication` 및 **cluster_12**의 `concept:brand_filter` 설계 표준을 정의합니다.
- 업데이트 이력 문서(`SKIN1004_AI_Update_History.md`)는 **cluster_12**의 핵심 컴포넌트들(`concept:maintenance_manager`, `concept:circuit_breaker`, `concept:smart_preview`, `concept:cs_agent`)의 변경 사항을 추적합니다.
- 최신 PRD(`SKIN1004_Enterprise_AI_PRD_v6.md`)는 **cluster_12**의 `concept:orchestrator_worker_pattern` 아키텍처 수립의 기준이 됩니다.

## Common Questions This Page Answers
- 에이전트가 사용자의 긍정/부정 피드백을 받아 어떻게 스스로 답변 품질을 개선하나요? (`app/agents/skill_memory.py`)
- 재무 손익(FI) 데이터나 방문자 분석 데이터에 대한 접근 권한 통제는 어떻게 설계되어 있나요? (`docs/FI_ACCESS_CONTROL.md`, `app/core/visitor_access.py`)
- LangGraph 기반의 흐름 제어 시 노드와 엣지를 정의하는 규칙은 무엇인가요? (`app/flow/spec.py`)