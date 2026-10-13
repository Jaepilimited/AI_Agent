# SKIN1004 AI Agent — Knowledge Map
**Generated**: 2026-10-13T03:01:36.793771+09:00 · **Files**: 294 · **Nodes**: 3506 · **Commit**: ac6577d

## What this project is
SKIN1004 사내 업무 자동화 및 분석을 지원하는 엔터프라이즈 AI 에이전트 시스템입니다. FastAPI 기반 백엔드 단일 서버(포트 3000)를 구동하며, Text-to-SQL(LangGraph), Google Workspace(Gmail/Calendar) 연동 개인 브리핑, 대량 메일 발송 파이프라인 및 Active Directory 연동 인증 체계를 통합 제공합니다.

## Top-level Clusters
1. `cluster_03` (293 files) — MariaDB 및 Active Directory 기반 사용자 인증·인가 시스템 (`wiki/cluster_03.md`)
2. `cluster_09` (349 files) — 핵심 비즈니스 로직 및 외부 연동 인터페이스 모듈 (`wiki/cluster_09.md`)
3. `cluster_25` (258 files) — 사내 데이터 파이프라인 및 백그라운드 워커 스케줄러 (`wiki/cluster_25.md`)
4. `cluster_06` (217 files) — 사내 분석 데이터 집계 및 도메인 레포팅 서비스 (`wiki/cluster_06.md`)
5. `cluster_08` (204 files) — Gmail 메타데이터 및 Calendar 이벤트 기반 개인 맞춤형 모닝 브리핑 엔진 (`wiki/cluster_08.md`)
6. `cluster_11` (172 files) — LangGraph 기반 Text-to-SQL 에이전트 및 DB 쿼리 유효성 검증 계층 (`wiki/cluster_11.md`)
7. `cluster_10` (168 files) — 공통 유틸리티 라이브러리 및 DB 연결 풀 관리 (`wiki/cluster_10.md`)
8. `cluster_04` (167 files) — 모델 접근 제어 및 프롬프트 관리 서브시스템 (`wiki/cluster_04.md`)
9. `cluster_05` (153 files) — 외부 서드파티 통신 클라이언트 및 API 어댑터 (`wiki/cluster_05.md`)
10. `cluster_21` (118 files) — 비동기 데몬 스레드 메일 발송 큐 및 템플릿 검증 파이프라인 (`wiki/cluster_21.md`)

## God Nodes (highest edge count — most central)
- `app/agents/sql_agent.py` — LangGraph 기반 Text-to-SQL 에이전트 파이프라인 (`generate_sql` → `validate_sql` → `execute_sql` → `format_answer`)
- `app/main.py` — 포트 3000 단일 진입점 FastAPI 앱 엔트리포인트 (API + 정적 프론트엔드 서빙)
- `app/api/mail_sender_api.py` — 템플릿 사전점검, 미리보기, 테스트 및 작업 발송 전용 논LLM API 계층
- `app/core/self_check.py` — AD 동기화 및 DB 무결성 회귀를 주기적으로 자가 감지하는 시스템 헬스체크 모듈
- `app/api/auth_api.py` — MariaDB 및 AD 연동 부서·사용자 등록, 로그인(`signin`), 세션 유지 엔드포인트
- `app/api/admin_api.py` — 관리자 전용 사용자 권한 관리 및 LLM 모델 접근 권한 통제 엔드포인트
- `app/core/personal_briefing.py` — Gmail/Calendar 메타데이터 경량 집계 및 로그인 캐시 브리핑 서비스
- `app/core/mail_sender_jobs.py` — MariaDB 상태 기록 및 데몬 스레드 기반 메일 발송 안전 큐 관리자

## Suggested Questions This Map Can Answer Instantly
1. 자연어 질문을 SQL로 변환하고 안전하게 검증하는 워크플로우는 어떻게 구성되어 있는가? (`app/agents/sql_agent.py`)
2. FastAPI 서버 구동 시 라우터 마운트 및 전역 미들웨어 설정은 어디서 수행되는가? (`app/main.py`)
3. 메일 발송 작업 중단 시 서버 재시작 후 중복 발송을 방지하는 안전 장치는 무엇인가? (`app/core/mail_sender_jobs.py`)
4. AD(Active Directory) 계정 정보와 MariaDB 사용자 데이터의 동기화 실패를 어떻게 모니터링하는가? (`app/core/self_check.py`)
5. 사용자 로그인 시 부서 정보 확인 및 토큰 발급은 어떤 방식으로 처리되는가? (`app/api/auth_api.py`)
6. 특정 사용자 또는 부서에만 LLM 모델 접근을 제한하는 제어 로직은 어디에 있는가? (`app/api/admin_api.py`)
7. 로그인 시 노출되는 개인 업무 브리핑 데이터는 어떤 외부 API에서 메타데이터를 수집하는가? (`app/core/personal_briefing.py`)
8. 메일 전송 전 템플릿 유효성 검사 및 미리보기 렌더링을 처리하는 API 규격은 무엇인가? (`app/api/mail_sender_api.py`)

## Recent Changes
- 2026-10-13 · Knowledge map synchronization and graph update (Commit `ac6577d`)
- 2026-08-04 · AD 동기화 연속 실패 방지를 위한 self_check 자가 점검 모듈 도입

## How to navigate
Read this file first. Then open graph.json and find the 2-3 nodes most relevant to your question. Read only those nodes' wiki_page values (`knowledge_map/wiki/**/*.md`). Only read original source files if the wiki page doesn't answer. Never Grep without consulting this map first.