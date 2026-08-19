# Cluster 51

> Auto-generated 2026-07-28T03:00:08.710343+09:00 · Files: 2

## Purpose
본 클러스터는 SKIN1004 Enterprise AI 시스템의 전반적인 환경 설정(Configuration) 관리와 사용자 개인정보 보호를 위한 비식별화(Pseudonymization) 처리를 담당합니다. 시스템의 안정적인 운영을 위한 환경 변수를 정의하고, 대화 및 피드백 데이터의 소유권을 안전하게 보호하기 위한 암호화 유틸리티를 제공합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/config.py` — SKIN1004 Enterprise AI 애플리케이션의 전역 환경 설정 및 구성 관리 클래스를 정의합니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/anonymization.py` — 대화 기록 및 피드백 데이터의 소유자 식별 정보를 보호하기 위한 가명화(Pseudonymization) 헬퍼 함수들을 제공합니다.

## Key Concepts
- **Configuration Management (설정 관리)**: `config.py`를 통해 데이터베이스 연결 정보, API 키, 보안 솔트(Salt) 값 등 애플리케이션 전반에서 필요한 설정값들을 중앙 집중식으로 로드하고 관리합니다.
- **Deterministic Pseudonymization (결정론적 가명화)**: `anonymization.py`에서는 `anon_id = hmac_sha256(salt, str(user_id))[:16]` 방식을 사용하여 사용자의 ID를 가명화합니다. 이 방식은 동일한 사용자 ID에 대해 항상 동일한 가명 ID를 생성하므로, 실제 사용자 정보를 노출하지 않으면서도 사이드바 등에서 특정 사용자의 대화 목록을 일관되게 그룹화할 수 있도록 지원합니다.

## How It Fits In
이 클러스터는 SKIN1004 AI Agent 프로젝트의 기반 인프라 역할을 수행합니다. `config.py`에서 로드된 보안 솔트 값은 `anonymization.py`에서 HMAC-SHA256 연산을 수행할 때 사용됩니다. 비식별화된 사용자 ID(`anon_id`)는 데이터베이스 저장 및 대화 이력 관리 모듈로 전달되어, 개인정보 유출 위험 없이 안전하게 대화 세션을 유지하고 피드백 데이터를 관리할 수 있도록 돕습니다.

## Common Questions This Page Answers
- **사용자의 실제 ID를 노출하지 않으면서 어떻게 이전 대화 목록을 그룹화하여 보여줄 수 있나요?**
  - `anonymization.py`에서 제공하는 HMAC-SHA256 기반의 결정론적 가명화 알고리즘을 사용하여, 동일 사용자에게는 항상 일관된 16자리 `anon_id`를 부여함으로써 안전하게 그룹화합니다.
- **시스템 전반의 환경 변수와 설정값은 어디에서 관리하나요?**
  - `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/config.py` 파일에서 애플리케이션의 모든 환경 설정을 통합 관리합니다.