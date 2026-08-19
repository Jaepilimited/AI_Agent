# Cluster 06

> Auto-generated 2026-08-19T03:00:36.499205+09:00 · Files: 5

## Purpose
이 클러스터는 SKIN1004 AI Agent 프로젝트에서 Google Workspace(GWS) 연동 및 사용자 데이터 보호를 위한 핵심 인프라를 담당합니다. 개별 사용자 단위의 OAuth2 인증을 처리하고, Gmail, Drive, Calendar 등의 API를 안전하게 호출하며, 개인정보 보호를 위한 가명화(Pseudonymization) 및 프로젝트 전반의 설정을 관리합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/agents/gws_agent.py` — 개별 사용자 OAuth2 인증, 타임아웃, 재귀 제한을 지원하는 Google Workspace 서브 에이전트 구현체입니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/google_auth.py` — 사용자별 GWS 인증을 위한 OAuth2 흐름, 토큰 저장 및 갱신을 관리하는 매니저입니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/google_workspace.py` — 전달받은 자격 증명(Credentials)을 사용해 Gmail, Drive, Calendar API를 호출하는 무상태(Stateless) 래퍼 함수 모음입니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/anonymization.py` — 대화 및 피드백 소유권을 보호하기 위해 사용자 ID를 안전하게 가명화하는 헬퍼 모듈입니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/config.py` — SKIN1004 Enterprise AI 시스템 전반의 환경 변수 및 설정을 관리하는 구성 관리 파일입니다.

## Key Concepts
- **Per-User OAuth2** — 기존 MCP 기반의 단일 사용자 방식에서 벗어나, 각 사용자가 자신의 Google 계정으로 개별 인증하여 GWS 자원에 접근하는 방식입니다. 토큰은 로컬 파일(`data/gws_tokens/{email}`) 등을 통해 관리됩니다.
- **Pseudonymization (가명화)** — `hmac_sha256` 알고리즘과 솔트(Salt)를 사용하여 `user_id`를 결정론적(Deterministic)인 `anon_id`로 변환합니다. 이를 통해 실제 개인정보를 노출하지 않으면서도 사이드바에서 동일 사용자의 대화 목록을 일관되게 그룹화할 수 있습니다.
- **Stateless GWS Wrapper** — 상태를 저장하지 않고, 호출 시점에 유효한 자격 증명을 주입받아 Google API를 실행하는 구조로 설계되어 확장성과 안정성을 높입니다.

## How It Fits In
- **Cluster 29 (Core Agent & Settings)**: `gws_agent.py`는 ReAct 에이전트 패턴(`concept:react_agent`)을 구현하며, `config.py`는 Pydantic 기반 설정(`concept:pydantic_settings`)을 활용합니다. 또한 가명화 로직은 `concept:hmac_sha256` 암호화 개념을 공유합니다.
- **Cluster 21 & Cluster 12 (Privacy & User Management)**: `anonymization.py`는 사용자 가명화(`concept:pseudonymization`, `concept:user_anonymization`)를 구현하여, 대화 이력 및 피드백 저장 시 사용자의 민감한 식별 정보를 보호합니다.

## Common Questions This Page Answers
- **Q1: GWS 에이전트는 다중 사용자 환경에서 어떻게 인증을 처리하나요?**
  - `google_auth.py`를 통해 사용자별로 독립적인 OAuth2 흐름을 수행하며, 발급된 토큰은 `data/gws_tokens/` 경로에 이메일별로 안전하게 구분되어 저장 및 갱신됩니다.
- **Q2: 사용자 개인정보를 보호하면서 어떻게 이전 대화 목록을 그룹화할 수 있나요?**
  - `anonymization.py`에서 `hmac_sha256(salt, str(user_id))[:16]` 방식을 사용하여 고유하고 일관된 가명 ID(`anon_id`)를 생성하므로, 실제 식별자 없이도 동일 사용자의 세션을 묶을 수 있습니다.