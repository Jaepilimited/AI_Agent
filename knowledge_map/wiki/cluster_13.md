# Cluster 13

> Auto-generated 2026-08-19T03:00:36.499205+09:00 · Files: 11

## Purpose
SKIN1004 AI Agent 프로젝트의 핵심 웹 API 레이어를 구성하는 클러스터입니다. 사용자 인증(Authentication), 권한 관리(RBAC), 대화 이력 관리, 보고서 생성 및 보안, 그리고 AI 지식 베이스(CLAUDE.md 등) 편집과 같은 시스템 전반의 핵심 비즈니스 로직을 외부에 API 엔드포인트 형태로 제공합니다.

## Key Files
- `app/api/auth_api.py` — MariaDB 기반의 AD 연동 부서/이름 로그인 및 JWT 토큰 발급 API
- `app/api/auth_middleware.py` — FastAPI용 JWT 쿠키 기반 인증 의존성 주입 모듈
- `app/api/admin_group_api.py` — AD 사용자 및 그룹 관리, 브랜드 필터링 및 권한 설정 API
- `app/api/reports_api.py` — 원가, 마진 등 민감 정보가 포함된 보고서의 생성, 조회 및 엄격한 권한 제어 API
- `app/api/conversation_api.py` — 대화 이력(Chat History) CRUD 및 피드백 관리 API
- `app/api/harness_api.py` — `CLAUDE.md` 및 Memory 파일 시각화/편집을 위한 AI 지식 베이스 에디터 API
- `app/api/eval_api.py` — AI 답변 평가(Eval Run) 결과 검토 및 피드백을 위한 관리자용 API
- `app/api/face_search_routes.py` — 구글 드라이브 사진 인덱스 검색 및 썸네일 제공 API
- `app/api/routes.py` — Open WebUI 연동을 위한 OpenAI 호환 규격 API 엔드포인트

## Key Concepts
- **엄격한 보고서 권한 제어 (Report Permissions)**: 원가, 마진, 거래처별 FOC율 등 민감한 매출 데이터가 포함된 보고서는 작성자와 지정된 수신자만 열람할 수 있으며, 관리자(Admin)조차도 예외 없이 접근이 제한됩니다. 권한 판정은 `store.get_for_user(report_id, user_id)` 단 한 곳에서만 엄격하게 처리됩니다.
- **AD 연동 및 RBAC**: Active Directory(AD) 정보를 기반으로 사용자의 부서와 이름을 연동하여 로그인 및 권한(Role-Based Access Control)을 관리합니다.
- **Open WebUI 호환성**: 외부 UI 도구와의 원활한 연동을 위해 OpenAI 규격을 준수하는 API 엔드포인트를 제공합니다.

## How It Fits In
- **권한 및 필터링 연동**: `admin_group_api.py`는 Cluster 29의 브랜드 필터링(`concept:brand_filter`) 및 Cluster 30의 역할 기반 권한 제어(`concept:role_based_access_control`)를 구현합니다.
- **대화 및 피드백**: `conversation_api.py`는 Cluster 12의 사용자 익명화 기술(`concept:user_anonymization`) 및 Cluster 05의 메시지 피드백 기능(`concept:message_feedback`)과 긴밀하게 연결되어 작동합니다.
- **보고서 보안**: `reports_api.py`는 Cluster 05의 보고서 공유 권한 모델(`concept:report_sharing_permissions`)을 구체적으로 구현하여 강력한 보안을 유지합니다.
- **외부 연동**: `routes.py`는 Cluster 04의 OpenAI 호환 API 규격(`concept:openai_compatible_api`) 및 Cluster 12의 브랜드 필터링(`concept:brand_filtering`) 정책을 적용하여 외부 클라이언트 요청을 처리합니다.

## Common Questions This Page Answers
- 원가나 마진이 포함된 민감한 보고서의 접근 권한은 어떻게 통제되나요?
- Open WebUI 등 외부 서비스와 연동하기 위한 API 엔드포인트는 어디에 정의되어 있나요?
- AI Agent가 참조하는 `CLAUDE.md`나 지식 베이스 파일을 웹에서 시각화하고 편집하려면 어떤 API를 사용해야 하나요?
- AD(Active Directory) 정보를 활용한 로그인 및 JWT 인증 프로세스는 어떻게 이루어지나요?