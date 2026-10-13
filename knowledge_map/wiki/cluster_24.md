# Cluster 24

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 8

## Purpose
이 클러스터는 Entra ID 기반의 사용자 인증 및 디렉터리 연동, 접근 제어 권한 관리와 Open WebUI 연동 API 엔드포인트를 담당합니다. 신규 로그인 사용자의 부서 기반 데이터 그룹 자동 배정 및 방문자 분석 권한 규칙을 포함하여, 사내 보안 아키텍처 규격을 충족하는 사용자 세션 체계를 구축합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/api/routes.py` — Open WebUI 연동을 지원하는 OpenAI 호환 API 엔드포인트 제공.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/group_autoassign.py` — 신규 로그인 계정 생성 시 부서에 따라 기본 데이터 그룹(유통본부는 DD, 나머지는 SK)을 자동으로 배정하는 로직.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/visitor_access.py` — 방문자 분석 뷰(visitor analytics view) 조회를 위한 공용 접근 권한 규칙.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/Craver_Security_Architecture_2026-04-22.md` — Craver Enterprise AI 보안 아키텍처 문서.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/ENTRA_DIRECTORY_RELEASE_20260908.md` — 2026-09-08 기준 Entra 사용자 관리 체계 전환 릴리즈 노트.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/ENTRA_SESSION_RELEASE_20260909.md` — 2026-09-09 기준 Entra 인증 필수 적용 릴리즈 노트.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/IT_REQUEST_ENTRA_DIRECTORY_20260908.md` — 셀라(Cella) 시스템의 Entra 전체 직원 조회 설정 IT 요청 문서.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/superpowers/specs/2026-09-08-entra-directory-design.md` — Entra 로그인 연동 및 셀라 자체 사용자 디렉터리 설계 사양서.

## Key Concepts
- **Entra ID Authentication & Session** — Microsoft Entra ID를 통한 사내 임직원 통합 로그인 및 필수 세션 인증 체계.
- **Group Auto-assignment** — 신규 회원가입/로그인 계정에 대해 유통본부는 DD, 그 외 부서는 SK로 데이터 그룹을 자동 설정하여 초기 질의 불가 문제를 해결하는 정책.
- **Visitor Access Control** — 한정된 범위의 방문자 분석 뷰 조회를 제어하는 공유 권한 규칙.
- **Open WebUI Integration** — 외부 UI 클라이언트와 통신하기 위한 OpenAI 호환 라우트 계층.

## How It Fits In
- `app/api/routes.py`는 클러스터 12의 `orchestrator_agent`를 구현/호출하여 Open WebUI 인터페이스로 AI 응답을 스트리밍합니다.
- `docs/ENTRA_DIRECTORY_RELEASE_20260908.md`의 디렉터리 사용자 관리는 클러스터 18의 `mariadb_connection_pool`을 통한 DB 연결과 연계되어 동작합니다.
- `docs/superpowers/specs/2026-09-08-entra-directory-design.md`는 클러스터 43의 `directory_users_migration` 설계를 구현하는 기반이 됩니다.

## Common Questions This Page Answers
- 신규 직원이 회원가입했을 때 데이터 그룹 배정(DD/SK)이 자동으로 처리되는 기준은 무엇인가?
- Entra ID 전환 및 세션 필수화가 적용된 이력과 보안 아키텍처 요구사항은 어디서 확인하는가?
- Open WebUI 연동을 위해 지원되는 API 규격 엔드포인트 파일은 어디에 정의되어 있는가?