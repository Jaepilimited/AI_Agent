# Cluster 10

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 13

## Purpose
사용자 인증(Entra ID, Google OAuth), 세션 관리, 사용자 권한 디렉토리 및 마이그레이션 등 코어 보안 및 계정 관리 기능을 담당합니다. 또한 대화 기록 익명화, 시스템 서킷 브레이커/점검 모드 제어, 지식 위키 신뢰도 검증 및 벡터 임베딩 생성과 같은 안전성 및 기반 유틸리티를 제공합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/entra_auth.py` — 사내 계정 통합을 위한 Microsoft Entra ID (OIDC) 로그인 처리
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/session_auth.py` — 계정 연동과 분리된 독립 Cella 세션 검증
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/user_directory.py` — Entra ID 인증 기반 직원 정보 및 권한 디렉토리 관리
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/user_directory_migration.py` — 기존 AD 권한 체계에서 Cella 디렉토리로 스키마 이전
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/google_auth.py` — 사용자별 GWS 접근을 위한 Google OAuth2 토큰/인증 관리
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/google_oauth_state.py` — 일회용 OAuth state 토큰 생성 및 검증
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/password_reset_google.py` — Google 연동 계정을 통한 자체 비밀번호 재설정
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/anonymization.py` — 대화 및 피드백 소유권 식별을 위한 HMAC-SHA256 가명화 헬퍼
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/safety.py` — MaintenanceManager(점검 모드) 및 CircuitBreaker 장애 차단기
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/embeddings.py` — 벡터 검색용 BGE-M3 임베딩 모델 인터페이스
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge/trust.py` — 검색된 knowledge-wiki 팩트의 신뢰 상태 및 안전성 검증 헬퍼
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/mailer.py` — 사내 SMTP 릴레이 메일 발송 모듈 (현재 포트 차단 상태)
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/IT_REQUEST_MAIL_JANDI.md` — AI Craver 알림 발송 경로 개방 요청 문서

## Key Concepts
- **Entra ID OIDC & User Directory**: 기존 로컬 DB(`users.password_hash`) 및 분산 관리되던 비밀번호 체계를 사내 Microsoft Entra ID로 일원화하고, Microsoft Graph API를 통해 사내 직원 디렉토리와 권한을 동기화합니다.
- **User Pseudonymization**: `anon_id = hmac_sha256(salt, str(user_id))[:16]` 형태로 사용자를 가명화하여, UI 사이드바 그룹핑 기능은 유지하면서 개인 식별성을 분리합니다.
- **Safety (Maintenance & CircuitBreaker)**: BigQuery `__TABLES__` 행 수 변경 감지 등을 활용한 시스템 점검 모드 전환 및 외부 서비스 장애 전파를 막는 서킷 브레이커를 지원합니다.
- **Trust State**: 어시스턴트 과거 답변에서 마이닝된 knowledge-wiki 데이터의 신뢰 수준을 평가하여 안전한 컨텍스트 주입 여부를 결정합니다.

## How It Fits In
- **Cluster 16 & Cluster 24**: `session_auth.py`와 `user_directory.py`는 Cluster 16의 Entra ID 인증 및 Cluster 24의 Service Token/Microsoft Graph API 및 AD 사용자 데이터와 연계됩니다.
- **Cluster 09**: `user_directory_migration.py`의 스키마 마이그레이션과 `trust.py`의 knowledge-wiki 신뢰도 검증 로직이 지식 기반 및 스키마 관리와 연결됩니다.
- **Cluster 14**: `google_auth.py`와 `anonymization.py`를 통해 Google OAuth2 및 사용자 가명화 파이프라인과 통합됩니다.
- **Cluster 25**: `safety.py`의 MaintenanceManager와 CircuitBreaker 인터페이스를 통해 시스템 전반의 운영 안전성을 제어합니다.

## Common Questions This Page Answers
- 기존 사내 AD 기반 계정/비밀번호 체계에서 Entra ID OIDC 인증으로 어떻게 전환되었는가?
- 대화 내역 조회 시 개인정보를 노출하지 않으면서 본인의 대화 목록을 그룹핑하는 방식은 무엇인가?
- 구글 계정 연동 사용자와 미연동 사용자의 비밀번호 복구 경로는 어떻게 분기되는가?
- 시스템 장애 감지(서킷 브레이커) 및 점검 모드 자동 전환의 메커니즘은 어떻게 동작하는가?