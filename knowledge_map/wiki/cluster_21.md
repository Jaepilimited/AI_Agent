# Cluster 21

> Auto-generated 2026-08-19T03:00:36.499205+09:00 · Files: 2

## Purpose
본 클러스터는 SKIN1004 AI Agent 프로젝트의 개인정보 비식별화(Anonymization) 및 평가 파이프라인(Eval Pipeline)의 설계 스펙과 업데이트 로그를 관리합니다. 고객 상담 데이터 및 메가와리(Megawari) 프로모션 관련 데이터 처리 시 개인정보 보호를 보장하고, LLM 응답의 품질을 체계적으로 평가하기 위한 아키텍처적 기반을 제공합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/superpowers/specs/2026-04-17-anonymization-and-eval-design.md` — 비식별화 엔진(Anonymization Engine) 및 평가 파이프라인(Eval Pipeline)의 상세 설계 사양서
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/update_log_2026-04-17.md` — Anonymization v1.0 및 Eval Pipeline v1.0의 실제 구현 및 릴리즈 변경 사항을 기록한 업데이트 로그

## Key Concepts
- **비식별화 (Anonymization)**: 고객의 이름, 전화번호, 주소 등 민감한 개인정보(PII)를 탐지하고 가명화(Pseudonymization) 또는 마스킹 처리하여 외부 LLM API로 안전하게 전달하는 기능입니다.
- **평가 파이프라인 (Eval Pipeline)**: AI Agent가 생성한 답변의 정확성, 일관성, 그리고 SKIN1004 브랜드 가이드라인 준수 여부를 정량적/정성적 지표로 검증하는 자동화된 평가 체계입니다.
- **가명화 복원 (De-anonymization)**: LLM이 비식별화된 텍스트를 바탕으로 답변을 생성하면, 최종 사용자에게 전달하기 전에 마스킹된 플레이스홀더를 원래의 정보로 안전하게 복원하는 역방향 매핑 프로세스입니다.

## How It Fits In
이 클러스터는 SKIN1004 AI Agent가 실제 운영 환경(Production)에서 안전하게 작동할 수 있도록 돕는 보안 및 품질 보증(QA)의 핵심 레이어입니다. 외부 LLM 및 데이터베이스와 통신하기 직전 단계에서 개인정보를 보호하고, 생성된 답변이 메가와리 등 실제 이커머스 도메인 규칙에 부합하는지 평가하는 독립적인 인프라 역할을 수행합니다.

## Common Questions This Page Answers
- **Q1. AI Agent가 수집한 고객의 개인정보는 어떻게 보호되나요?**
  - `2026-04-17-anonymization-and-eval-design.md` 설계에 따라, 데이터가 외부 LLM으로 전송되기 전에 비식별화 엔진이 PII를 감지하여 고유 토큰으로 치환하며, 답변 수신 후 다시 복원하는 양방향 매핑을 거칩니다.

- **Q2. LLM의 답변 품질과 브랜드 가이드라인 준수 여부는 어떻게 측정하나요?**
  - `update_log_2026-04-17.md`에 기록된 Eval Pipeline v1.0을 통해 정의된 평가 메트릭을 기준으로 자동화된 테스트 및 스코어링을 수행합니다.