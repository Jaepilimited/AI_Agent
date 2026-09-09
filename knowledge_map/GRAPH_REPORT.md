# SKIN1004 AI Agent — Knowledge Map
**Generated**: 2026-09-09T03:00:59.448358+09:00 · **Files**: 266 · **Nodes**: 3074 · **Commit**: 41586c5

## What this project is
SKIN1004 AI Agent는 화장품 브랜드 SKIN1004의 사내 업무 효율화를 위한 엔터프라이즈 AI 에이전트 플랫폼입니다. FastAPI 백엔드와 LangGraph 기반의 Text-to-SQL 엔진을 탑재하여, 사내 데이터베이스 조회, Active Directory 연동, 개인화 데일리 브리핑 및 시스템 자가 점검 기능을 통합 제공합니다.

## Top-level Clusters
1. **cluster_00** (28 files): 사내 ERP 및 외부 채널 데이터 동기화를 위한 배치 파이프라인 모듈군
2. **cluster_01** (11 files): 데이터베이스 커넥션 풀 및 트랜잭션 관리 유틸리티
3. **cluster_02** (41 files): 상품 정보, 재고 현황 및 물류 데이터 처리를 위한 핵심 비즈니스 로직
4. **cluster_03** (268 files): LangChain 기반의 LLM 체인 및 프롬프트 템플릿 관리 레이어
5. **cluster_04** (139 files): 프론트엔드 UI 컴포넌트 및 정적 에셋 리소스
6. **cluster_05** (197 files): 데이터 분석 및 시각화 리포트 생성을 위한 백엔드 모듈
7. **cluster_06** (265 files): 시스템 전반의 공통 유틸리티, 로깅 및 예외 처리 핸들러
8. **cluster_07** (94 files): 외부 API 연동 및 웹훅 수신 처리를 위한 통합 게이트웨이
9. **cluster_08** (255 files): 사용자 세션 관리 및 실시간 알림 전송을 위한 WebSocket 서버
10. **cluster_09** (64 files): 다국어 번역 및 글로벌 마켓 데이터 전처리 파이프라인
11. **cluster_10** (56 files): 시스템 설정값 로드 및 환경 변수 검증 모듈
12. **cluster_11** (40 files): FastAPI 애플리케이션 진입점 및 미들웨어 설정 (`app/main.py` 포함)
13. **cluster_12** (220 files): 데이터베이스 마이그레이션 스크립트 및 스키마 정의서
14. **cluster_13** (33 files): 이메일 발송 및 사내 메신저 알림 연동 모듈
15. **cluster_14** (65 files): Gmail 및 Google Calendar 메타데이터 기반의 개인화 브리핑 캐싱 레이어
16. **cluster_15** (96 files): 마케팅 성과 지표 집계 및 광고 효율 분석 엔진
17. **cluster_16** (75 files): 파일 업로드/다운로드 및 S3 스토리지 연동 인터페이스
18. **cluster_17** (25 files): 백그라운드 작업 스케줄링 및 Celery 태스크 정의
19. **cluster_18** (133 files): LangGraph 기반의 Text-to-SQL 에이전트 워크플로우 핵심 로직
20. **cluster_19** (62 files): Active Directory 연동 및 MariaDB 기반 사용자 인증 API
21. **cluster_20** (10 files): 시스템 리소스 모니터링 및 성능 메트릭 수집기
22. **cluster_21** (2 files): 레거시 시스템 마이그레이션 지원용 임시 헬퍼 스크립트
23. **cluster_22** (107 files): 관리자 전용 사용자 관리 및 LLM 모델 접근 제어 API
24. **cluster_23** (107 files): 고객 피드백 분석 및 감성 분석 파이프라인
25. **cluster_24** (10 files): 보안 취약점 진단 및 API 요청 속도 제한(Rate Limiting) 모듈
26. **cluster_25** (43 files): Open WebUI 연동을 위한 OpenAI 호환 규격 API 엔드포인트
27. **cluster_26** (39 files): PDF 및 이미지 문서 OCR 텍스트 추출 파이프라인
28. **cluster_27** (42 files): 벡터 데이터베이스(ChromaDB) 연동 및 RAG 검색 모듈
29. **cluster_28** (82 files): 매출 예측 및 수요 분석을 위한 머신러닝 추론 엔진
30. **cluster_29** (12 files): 시스템 백업 및 재해 복구(DR) 자동화 스크립트
31. **cluster_30** (31 files): 사내 위키 및 지식 베이스 문서 파싱 엔진
32. **cluster_31** (59 files): 사용자 활성화 유도를 위한 능동형 데일리 브리핑 생성기
33. **cluster_32** (96 files): 단위 테스트 및 통합 테스트 스위트
34. **cluster_33** (27 files): API 문서 자동 생성 및 Swagger 설정 유틸리티
35. **cluster_34** (15 files): 컨테이너 가상화 및 Docker Compose 배포 설정
36. **cluster_35** (46 files): 외부 쇼핑몰 주문 데이터 수집 및 정규화 모듈
37. **cluster_36** (19 files): 캐시 메모리(Redis) 관리 및 데이터 만료 정책 설정
33. **cluster_37** (58 files): 시스템 무결성 및 데이터 동기화 상태 자가 점검 모듈
39. **cluster_38** (110 files): 프론트엔드 상태 관리 및 API 통신 레이어
40. **cluster_39** (42 files): 사용자 행동 로그 수집 및 통계 분석 엔진
41. **cluster_40** (3 files): CI/CD 파이프라인 및 GitHub Actions 워크플로우

## God Nodes (highest edge count — most central)
- `app/agents/sql_agent.py` (18 edges) — LangGraph 기반 Text-to-SQL 에이전트 (generate_sql → validate_sql → execute_sql → format_answer)
- `app/main.py` (11 edges) — SKIN1004 Enterprise AI FastAPI 애플리케이션 엔트리 포인트 및 단일 서버 구동
- `app/core/self_check.py` (37 edges) — AD 동기화 실패 등 시스템 장애 및 데이터 무결성 회귀를 매일 감지하는 자가 점검 모듈
- `app/api/auth_api.py` (19 edges) — MariaDB 및 Active Directory 연동 기반의 사용자 인증(회원가입, 로그인, 로그아웃) API
- `app/api/admin_api.py` (22 edges) — 관리자 전용 사용자 권한 관리 및 LLM 모델 접근 제어 API
- `app/core/personal_briefing.py` (14 edges) — Gmail 및 Calendar 메타데이터를 캐싱하여 제공하는 읽기 전용 개인 업무 브리핑 모듈
- `app/api/routes.py` (25 edges) — Open WebUI 등 외부 클라이언트 연동을 위한 OpenAI 호환 API 엔드포인트
- `app/core/briefing.py` (31 edges) — 미사용 사용자 활성화를 위해 선제적으로 업무 요약을 제공하는 개인화 데일리 브리핑 엔진

## Suggested Questions This Map Can Answer Instantly
1. **Text-to-SQL 에이전트의 SQL 생성 및 검증 파이프라인은 어떻게 구성되어 있나요?** (`app/agents/sql_agent.py`)
2. **사내 Active Directory(AD) 동기화 실패를 감지하는 자가 점검 로직은 어디서 확인하나요?** (`app/core/self_check.py`)
3. **Open WebUI와 연동하기 위한 OpenAI 호환 API 규격은 어떻게 구현되어 있나요?** (`app/api/routes.py`)
4. **사용자 로그인 시 제공되는 Gmail 및 캘린더 요약 브리핑의 캐싱 정책은 무엇인가요?** (`app/core/personal_briefing.py`)
5. **사용자 가입 후 활성화를 유도하기 위한 데일리 브리핑 발송 조건은 어떻게 되나요?** (`app/core/briefing.py`)
6. **FastAPI 서버의 미들웨어 설정 및 CORS 정책은 어디에 정의되어 있나요?** (`app/main.py`)
7. **사내 MariaDB를 활용한 사용자 권한 및 LLM 모델 접근 제어는 어떻게 관리되나요?** (`app/api/admin_api.py`)
8. **AD 연동 로그인 실패 시 예외 처리 및 사용자 정보 업데이트 흐름은 어떻게 되나요?** (`app/api/auth_api.py`)

## Recent Changes
- 2026-09-09 · `sql_agent` LangGraph 워크플로우 예외 처리 강화
- 2026-08-20 · `briefing` 사용자 활성화를 위한 선제적 데일리 브리핑 기능 추가
- 2026-08-04 · `self_check` AD 동기화 누락 방지를 위한 일일 자가 점검 모듈 도입

## How to navigate
Read this file first. Then open graph.json and find the 2-3 nodes most relevant to your question. Read only those nodes' wiki_page values (`knowledge_map/wiki/**/*.md`). Only read original source files if the wiki page doesn't answer. Never Grep without consulting this map first.