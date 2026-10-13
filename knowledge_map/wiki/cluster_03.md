# Cluster 03: API Endpoints & User Integrations

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 22

## Purpose
FastAPI 기반의 HTTP 라우터 계층으로, 사용자 인증/인가, 대화 세션 CRUD, 개인화 브리핑 및 실무 지원 유틸리티 API를 제공합니다. MariaDB 기반 세션 관리와 Google/Entra ID 외부 연동을 처리하며, 민감한 매출 및 개인 질의 데이터에 대한 철저한 격리 원칙을 강제합니다.

## Key Files
- `app/api/auth_api.py` — MariaDB 기반 회원가입, 로그인, 로그아웃 및 `/api/auth/me` 세션 확인 엔드포인트.
- `app/api/auth_middleware.py` — JWT 쿠키 기반 인증 종속성(FastAPI Dependency).
- `app/api/auth_routes.py` — Google Workspace 기반 OAuth2 인증 라우트.
- `app/api/entra_routes.py` — Microsoft Entra ID (OIDC) 과도기 로그인 라우트.
- `app/api/conversation_api.py` — 대화 히스토리 및 세션 관리 CRUD API.
- `app/api/admin_group_api.py` — Cella 조직도 및 부서/권한 그룹 관리 관리자 API.
- `app/api/personal_briefing_api.py` — 로그인 사용자 맞춤형 캐시 우선 개인 브리핑 API.
- `app/api/personal_profile_api.py` — JWT 이메일 기반 사용자별 자주 묻는 질문 추천 API.
- `app/api/sql_export_api.py` — 채팅 중 잘린 대규모 SQL 조회 결과를 본인 확인 후 CSV 다운로드 제공.
- `app/api/coa_finder_api.py` — 사용자 본인의 Google Drive OAuth 권한을 이용한 COA·MSDS 파일 검색 API.
- `app/api/attachment_api.py` — 서버에 원본 파일을 저장하지 않고 엑셀·CSV를 표 텍스트로 즉시 변환하는 API.
- `app/api/jandi_briefing_api.py` — 잔디 웹훅 연동 및 DB_PC 루프백 릴레이 엔드포인트.
- `app/api/harness_api.py` — CLAUDE.md 및 Memory 파일 시각화/편집용 Knowledge Base API.
- `docs/superpowers/plans/2026-08-25-login-personal-briefing.md` — 개인 브리핑 구현 계획 문서.

## Key Concepts
- **Strict User Isolation**: 대화 이력, 자주 묻는 질문, SQL 내보내기 등에서 타인의 데이터 조회를 원천 차단하기 위해 Request Body 대신 JWT 클레임에서 식별자를 추출합니다.
- **Zero-Storage Attachment Processing**: `attachment_api.py`는 첨부 엑셀/CSV를 메모리상에서 텍스트로 변환해 프롬프트에 전달할 뿐 디스크에 저장하지 않습니다.
- **Dual Authentication Migration**: Entra ID 연동 전환기 동안 기존 MariaDB 로컬 계정과 Entra ID 로그인을 병행 지원합니다.

## How It Fits In
- **Cluster 14 & 24 (인증 및 접근 제어)**: `auth_routes.py`, `entra_routes.py`, `admin_group_api.py`가 Google OAuth2, Entra ID, 조직도 및 RBAC 로직을 구현합니다.
- **Cluster 10 (세션 및 미들웨어)**: `middleware.py` 및 `auth_middleware.py`가 전역 세션 검증과 요청 로깅을 담당합니다.
- **Cluster 08 (저장 질문)**: `saved_questions_api.py`가 사용자 질의 북마크 및 실행 주기 스케줄링을 노출합니다.
- **Cluster 16 (외부 협업 도구)**: `jandi_briefing_api.py` 및 `notion_briefing_api.py`를 통해 잔디와 노션으로 브리핑을 전달합니다.

## Common Questions This Page Answers
- Q: 사용자가 업로드한 엑셀 파일이 서버 어디에 저장되나요?
  - A: `attachment_api.py` 정책에 따라 서버에 저장되지 않고 텍스트 표로 변환되어 메시지에만 포함됩니다.
- Q: COA/MSDS 조회 시 서비스 계정 권한 문제가 발생하면 어떻게 처리하나요?
  - A: 서비스 계정을 사용하지 않으며, `coa_finder_api.py`를 통해 호출자 본인의 Google OAuth 권한으로 직접 Drive를 조회합니다.