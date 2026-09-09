# Cluster 03

> Auto-generated 2026-09-09T03:00:59.448358+09:00 · Files: 22

## Purpose
SKIN1004 AI Agent 프로젝트의 핵심 API 엔드포인트와 사용자 인증, 개인화 서비스 및 보안 제어를 담당하는 클러스터입니다. Google Workspace, Entra ID 기반의 인증 체계를 구축하고, 보고서 공유·개인 브리핑·알림 등 민감한 비즈니스 데이터에 대한 철저한 권한 검증과 사용자별 맞춤형 기능을 제공합니다.

## Key Files
- `app/api/auth_middleware.py` — MariaDB 연동 및 JWT 쿠키 기반의 FastAPI 인증 디펜던시 (역할 기반 접근 제어 구현)
- `app/api/auth_routes.py` — Google Workspace OAuth2 인증 엔드포인트
- `app/api/entra_routes.py` — 과도기 대응을 위한 Entra ID (OIDC) 로그인 엔드포인트 (기존 ID/PW 로그인과 병행)
- `app/api/reports_api.py` — 보고서 생성, 목록, 열람 및 공유 API (작성자와 지목된 수신자만 열람 가능하도록 엄격히 제한)
- `app/reports/share_ui.py` — 보고서 상단 공유 막대를 응답 시점에 동적으로 렌더링하여 권한별 UI 일관성 유지
- `app/api/attachment_api.py` — 업로드된 엑셀/CSV 파일을 서버에 저장하지 않고 즉시 표 텍스트로 변환하여 반환하는 API
- `app/api/personal_briefing_api.py` — 로그인 시 제공되는 사용자 맞춤형 출근 브리핑 API
- `app/api/sql_export_api.py` — 채팅 중 잘린 SQL 결과를 작성자 본인만 CSV로 다운로드할 수 있게 하는 엔드포인트
- `app/api/jandi_briefing_api.py` — 잔디(Jandi) 메신저 출근 브리핑 웹훅 등록 및 3중 방어선 기반의 내부 릴레이 API
- `app/api/coa_finder_api.py` — 사용자 본인의 OAuth 권한을 그대로 사용하여 구글 공유드라이브 내 COA/MSDS 문서를 검색하는 API

## Key Concepts
- **엄격한 소유권 검증 (Strict Ownership)** — `reports_api.py` 및 `sql_export_api.py` 등에서 원가, 마진 등 민감한 데이터 유출을 막기 위해 admin 권한 유무와 상관없이 오직 '작성자 및 명시적 공유 대상자'만 데이터에 접근할 수 있도록 제한합니다.
- **무저장 원칙 (Stateless Processing)** — `attachment_api.py`는 업로드된 파일을 서버에 저장하지 않고 텍스트로 변환 후 즉시 반환하여, 보안 위협과 파일 관리 리스크를 원천 차단합니다.
- **점진적 인증 전환 (Coexistence of Auth)** — `entra_routes.py`는 시스템 전환기 동안 기존 로컬 로그인과 Entra ID 로그인을 동시에 지원하여 사용자 락인(Lock-in) 및 차단 사고를 방지합니다.
- **동적 UI 바인딩 (Dynamic UI Injection)** — `share_ui.py`는 공유 버튼 등의 UI 요소를 정적 HTML에 저장하지 않고, 조회하는 사용자의 권한에 따라 응답 시점에 동적으로 결합합니다.

## How It Fits In
- **인증 및 권한 제어**: `auth_middleware.py`는 [cluster_05](cluster_05)의 JWT 인증 개념 및 [cluster_38](cluster_38)의 역할 기반 접근 제어(RBAC)를 실무 API 레이어에 적용합니다.
- **외부 플랫폼 연동**: `auth_routes.py`는 [cluster_14](cluster_14)의 Google Workspace 인증을, `entra_routes.py` 및 `jandi_briefing_api.py`는 [cluster_13](cluster_13)의 Entra ID 및 잔디 브리핑 연동 규격을 구현합니다.
- **개인화 및 브리핑**: `personal_briefing_api.py`와 `notion_briefing_api.py`는 [cluster_12](cluster_12)의 개인화 브리핑 아키텍처와 연결됩니다.
- **데이터 내보내기**: `sql_export_api.py`는 [cluster_04](cluster_04)의 임시 SQL 결과 저장소(`sql_result_store`)에서 데이터를 안전하게 인출하여 사용자에게 전달합니다.

## Common Questions This Page Answers
- Q. 업로드한 엑셀이나 CSV 파일은 서버의 어디에 저장되고 어떻게 삭제되나요?
  - A. `attachment_api.py`는 파일을 서버에 절대 저장하지 않습니다. 읽어서 텍스트로 변환한 뒤 즉시 반환하며, 대화 기록에만 텍스트 형태로 남습니다.
- Q. 관리자(admin) 계정은 모든 보고서(`reports_api.py`)를 열람할 수 있나요?
  - A. 아닙니다. 매출, 원가, 마진 등 민감한 정보가 포함되어 있어 관리자라 하더라도 작성자가 직접 지목하여 공유하지 않은 보고서는 열람할 수 없습니다.
- Q. 서버에서 이메일 알림 발송이 실패하는 이유는 무엇인가요?
  - A. 2026-08-19 실측 결과 WAS/APP 전 영역에서 SMTP 포트가 차단되어 있습니다. 따라서 IT 부서의 릴레이 개방 전까지는 `notifications_api.py`를 통해 앱 내 알림으로 대체 처리합니다.