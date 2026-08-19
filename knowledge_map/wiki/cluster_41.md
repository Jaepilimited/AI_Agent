# Cluster 41

> Auto-generated 2026-07-08T03:00:19.681328+09:00 · Files: 2

## Purpose
이 클러스터는 SKIN1004 AI Agent 애플리케이션의 보안 인프라와 사용자 인증(Authentication) 및 API 요청 흐름 관리를 담당합니다. Active Directory(AD)와 연동된 부서 및 이름 정보를 기반으로 사용자 인증을 처리하고, CORS 설정 및 요청 로깅과 같은 공통 미들웨어 기능을 제공하여 안전하고 투명한 API 환경을 구축합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/api/auth_api.py` — MariaDB를 사용자 저장소로 사용하여 회원가입(signup), 로그인(signin), 현재 로그인 사용자 정보 조회(me), 로그아웃(logout) 등의 인증 API 엔드포인트를 제공합니다. AD 연동 부서 및 이름 정보를 활용한 로그인을 지원합니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/api/middleware.py` — 애플리케이션 전반에 적용되는 CORS(Cross-Origin Resource Sharing) 설정, 사용자 인증 여부 검증, 그리고 들어오는 모든 API 요청에 대한 로깅을 수행하는 미들웨어입니다.

## Key Concepts
- **AD 연동 로그인 (AD-linked Login)**: 사용자의 부서(department)와 이름(name) 정보를 Active Directory(AD)와 연동하여 인증을 수행하고, 해당 정보를 MariaDB에 매핑하여 관리합니다.
- **인증 미들웨어 (Authentication Middleware)**: 보호된 API 엔드포인트에 접근하는 요청을 가로채어 유효한 인증 세션이 존재하는지 사전에 검증합니다.
- **요청 로깅 (Request Logging)**: API 서버로 유입되는 HTTP 요청의 메타데이터를 기록하여 시스템 모니터링 및 디버깅을 용이하게 합니다.

## How It Fits In
이 클러스터는 다른 비즈니스 로직 클러스터들과 직접적인 코드 의존 관계가 명시되지는 않았으나, SKIN1004 AI Agent 시스템 전체의 관문 역할을 수행합니다. 모든 API 요청은 `middleware.py`를 거쳐 CORS 및 로깅 처리가 완료된 후 각 엔드포인트로 라우팅되며, `auth_api.py`를 통해 인증된 사용자만이 메가와리(megawari) 분석이나 상품 관리 등 시스템 내 권한이 필요한 기능에 안전하게 접근할 수 있도록 보장합니다.

## Common Questions This Page Answers
- 사용자 로그인 및 회원가입 시 어떤 데이터베이스와 인증 체계를 사용하나요? (`C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/api/auth_api.py`에서 MariaDB와 AD 연동 부서+이름 정보를 사용합니다.)
- API 요청에 대한 CORS 허용 설정과 글로벌 로깅은 어디에서 정의하나요? (`C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/api/middleware.py`에서 일괄 처리합니다.)
- 현재 로그인한 사용자의 세션 상태나 상세 정보를 확인하려면 어떤 엔드포인트를 호출해야 하나요? (`auth_api.py`에 정의된 `me` 엔드포인트를 호출합니다.)