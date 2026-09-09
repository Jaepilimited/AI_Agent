# Cluster 36

> Auto-generated 2026-09-09T03:00:59.448358+09:00 · Files: 3

## Purpose
이 클러스터는 SKIN1004 AI Agent의 라우팅 시스템 1단계인 "소스가 필요한가?"(Source Gate) 판정 로직의 설계, 구현 계획 및 관련 업데이트 로그를 관리합니다. 거대해진 `orchestrator.py`에 판정 규칙을 추가하는 대신, 독립적이고 테스트 가능한 순수 함수(Pure Function) 기반의 라우팅 구조를 지향합니다.

## Key Files
- `app/core/route_intent.py` — 라우터 1단계 판정을 수행하는 순수 함수들을 정의하는 핵심 모듈입니다.
- `docs/superpowers/plans/2026-09-07-router-source-gate.md` — 사용자 질의에 외부 소스(Knowledge Base 등)가 필요한지 여부를 판정하는 Source Gate의 상세 구현 계획서입니다.
- `docs/update_log_2026-03-30.md` — 시스템 안정성을 위한 서킷 브레이커 도입 등 과거 주요 변경 사항을 기록한 업데이트 로그입니다.

## Key Concepts
- **Source Gate (소스 게이트)**: 사용자의 입력 메시지를 분석하여 SKIN1004 상품 정보나 메가와리(megawari) 프로모션 등 외부 지식 소스(Source) 조회가 필요한지, 아니면 단순 일상 대화나 즉각 답변이 가능한지 1단계로 판정하는 관문입니다.
- **Pure Function Routing (순수 함수 라우팅)**: `orchestrator.py` 내부의 복잡도를 낮추기 위해, 상태를 가지지 않고 입력값에 따라 일관된 결과를 반환하는 순수 함수 형태로 라우팅 판정 로직을 분리하여 유지보수성을 극대화합니다.

## How It Fits In
이 클러스터는 AI Agent의 전체 요청 처리 파이프라인에서 최전방의 의도 분석(Intent Classification) 및 라우팅을 담당합니다. 
- `docs/update_log_2026-03-30.md` 파일에 기록된 변경 사항은 시스템 장애 전파를 막는 **Circuit Breaker** 패턴(`concept:circuit_breaker`, cluster_12)을 구현 및 연동하고 있어, 라우팅 단계에서 발생할 수 있는 외부 API 및 소스 조회 실패에 유연하게 대응할 수 있도록 돕습니다.

## Common Questions This Page Answers
- 사용자의 질문에 외부 문서나 상품 정보 검색이 필요한지 어떻게 판정하나요?
- `orchestrator.py`가 너무 비대해지는 것을 방지하기 위해 라우팅 로직을 어떻게 격리했나요?
- Source Gate 설계 및 구현 계획의 구체적인 배경은 무엇인가요?