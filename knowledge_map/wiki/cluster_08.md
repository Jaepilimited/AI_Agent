# Cluster 08

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 10

## Purpose
사용자 맞춤형 출근 브리핑 생성, 잔디(Jandi) 발송 큐 관리, 도메인별 결정론적(deterministic) 비즈니스 지표 산출(매출 전망, 수출 물류 환율 환산, CS 집계)을 담당하는 코어 모듈군입니다. LLM의 환각(hallucination)을 원천 차단하고, 실제 구글 워크스페이스 이벤트 및 BigQuery 데이터 기반의 검증된 집계 결과만을 안전하게 제공합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/work_briefing.py` — 일정과 메일을 결합한 출근 브리핑 본문 생성 및 환각 방지 검증 로직.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/personal_briefing.py` — 로그인 시 표시되는 개인 브리핑용 경량 메타데이터 캐싱 및 집계.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/jandi_briefing.py` — WAS 환경의 프록시 제약을 우회하기 위한 잔디 웹훅 발송 대기열 처리.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/workday.py` — 구글 캘린더 공휴일 연동 기반의 근무일 및 메일 조회 구간 동적 판정.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/saved_questions.py` — 저장된 질문의 소유권 관리 및 출근 전 자동 백그라운드 실행.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/calendar_stats.py` — 사용자별 구글 캘린더 미팅 통계 집계 (인메모리 처리).
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/sales_outlook.py` — 등록된 확정 매출 데이터 기반의 결정론적 월 마감 실적 산출.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/logistics_fx.py` — 사내 BigQuery 환율표 기반 수출 물류비 월말환율 원화(KRW) 환산.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/cs_dashboard.py` — Google Sheets 기반 해외/국내 CS 대시보드 읽기 전용 집계 API.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/growth_report.py` — SQL 캐시 및 스킬 메모리 성장 지표를 측정하는 주간 리포트 생성.

## Key Concepts
- **Deterministic Fact Binding**: 브리핑의 모든 항목은 실재하는 `event_id`나 `message_id`에 바인딩되어야 하며, 매핑되지 않는 생성 텍스트는 버려집니다.
- **Dynamic Workday Evaluation**: 하드코딩된 공휴일 목록 대신 구글 '대한민국 공휴일' 캘린더를 참조해 브리핑 조회 대상 기간을 결정합니다.
- **Jandi Webhook Queue**: 서버망(WAS)에서 `wh.jandi.com` 403 차단 이슈를 극복하기 위해 DB_PC를 경유하는 발송 대기열 구조를 사용합니다.
- **월말환율 기반 환산**: 물류비 환산 시 임의의 환율을 쓰지 않고 사내 BigQuery 환율표의 기준 월말환율을 투명하게 명시하여 계산합니다.

## How It Fits In
- **Cluster 07 (환각 방지)**: `work_briefing.py`가 LLM의 사실 조작을 방지하는 엄격한 ID 바인딩 검증을 수행합니다.
- **Cluster 18 / 22 (스케줄링 & 연동)**: `jandi_briefing.py`는 잔디 웹훅 파이프라인과 연결되며, `personal_briefing.py`는 Google Workspace 연동 인터페이스를 소비합니다.
- **Cluster 24 / 25 / 04 (데이터 엔진)**: `sales_outlook.py`는 브랜드 필터를 적용하고, `growth_report.py`는 SQL 캐시와 스킬 메모리 적재량을 계측합니다.
- **Cluster 31 (CS 분석)**: `cs_dashboard.py`가 구글 시트 연동 집계 구현부 역할을 수행합니다.

## Common Questions This Page Answers
- 출근 브리핑 생성 시 LLM 환각으로 인해 없는 일정이나 메일이 작성되는 것을 어떻게 방지하나요?
- 서버 환경에서 잔디 웹훅 URL 접속이 차단될 때 알림을 어떻게 정상 발송하나요?
- 수출 물류 데이터 다운로드 시 외화 금액의 원화 환산 기준과 데이터 소스는 무엇인가요?
- 연휴나 대체공휴일 직후 출근 브리핑의 조회 날짜 범위는 어떻게 계산되나요?