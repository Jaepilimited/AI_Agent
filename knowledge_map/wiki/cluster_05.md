# Cluster 05

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 13

## Purpose
이 클러스터는 SKIN1004 AI Agent 시스템의 핵심 기반 인프라(Core Infrastructure)와 데이터 신뢰성 계층을 제공합니다. LLM 연동(Claude Opus, Gemini, 사내 Gateway), 외부 데이터베이스(BigQuery, MariaDB) 조회, 재시도 및 스트리밍 처리, 그리고 보고서 생성 시 LLM 환각을 방지하는 의미론적(Semantic) 스펙 계층을 관리합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/bigquery.py` — BigQuery 쿼리 실행 및 서킷 브레이커 연동 클라이언트.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/data_freshness.py` — 원본 DB와 파생 사본 간 동기화 지연을 감지하는 데이터 신선도 점검기.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/llm.py` — Gemini 및 Claude Opus를 아우르는 듀얼 LLM 클라이언트.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/llm_gateway.py` — 사내 보안 감사/가드레일 가이드라인을 준수하는 `llmgw` 통신 변환 계층.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/notify.py` — 테스트 및 작업 완료 알림을 전달하는 Windows 바탕화면 노티 유틸리티.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/retrying.py` — 외부 Google Sheets/API 호출 실패 시 배치 누락을 방지하는 재시도 로직.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/sales_carryover.py` — 월말 거래처 매출 백업 스냅샷을 비교해 이월 내역을 추적하는 모듈.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/stream_bridge.py` — 동기 제너레이터를 비동기 스트림으로 변환하는 백그라운드 스레드 브릿지.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/db/models.py` — MariaDB 기반 사용자 모델(User 데이터클래스).
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge_map/graph.py` — 지식 맵 분석을 위한 NetworkX 그래프 및 Louvain 커뮤니티 감지 래퍼.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/reports/semantic.py` — LLM이 직접 SQL을 작성하지 못하도록 지표·축을 고정하는 시맨틱 계층.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/reports/spec.py` — 계산·검증 규칙을 선언적으로 정의해 숫자는 코드가, 문장은 템플릿이 만들도록 강제하는 스펙 정의.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/update_log_2026-08-13.md` — 질문형 보고서, 골든셋 회귀 테스트, 모델 릴리즈 변경 기록.

## Key Concepts
- **Deterministic SQL Generation (`semantic.py`)**: LLM에게 직접 SQL 쿼리 생성을 맡기지 않고 검증된 축과 지표만 선택하게 하여 환각을 차단합니다.
- **Data Freshness Check (`data_freshness.py`)**: 사용자가 수동으로 발견하기 전에 데이터 동기화 지연을 시스템이 선제적으로 감지합니다.
- **Enterprise LLM Gateway (`llm_gateway.py`)**: Anthropic Messages 구조를 OpenAI 호환 페이로드로 변환하여 사내 보안 감사 및 프롬프트 가드레일을 통과시킵니다.
- **Sales Carryover Tracking (`sales_carryover.py`)**: SKIN1004 월말 마감 시 거래처 매출이 다음 달로 이월되는 현상을 백업 스냅샷 비교를 통해 추적합니다.

## How It Fits In
- **Circuit Breaker** (cluster_25): `app/core/bigquery.py`가 쿼리 실패율 임계치 초과 시 서킷 브레이커를 호출하여 장애 확산을 막습니다.
- **Data Freshness Monitoring** (cluster_11): `app/core/data_freshness.py`의 구현체가 신선도 점검 모니터링 시스템과 연계됩니다.
- **RBAC** (cluster_24): `app/db/models.py`의 User 데이터 모델이 역할 기반 권한 제어의 기초 엔티티로 동작합니다.
- **Knowledge Graph & Clustering** (cluster_03, cluster_09, cluster_41): `app/knowledge_map/graph.py`가 Louvain 알고리즘을 사용해 커뮤니티를 분할하고 과도하게 연결된 God Nodes를 감지합니다.

## Common Questions This Page Answers
- 보고서 작성 시 LLM이 임의로 부정확한 SQL을 작성하거나 숫자를 지어내지 않게 하려면 어떻게 설계해야 하는가?
- 사내 보안 가이드라인에 맞추어 Claude 프롬프트 감사/가드레일을 거치려면 어떤 게이트웨이 레이어를 거쳐야 하는가?
- 월말 거래처 매출 이월 여부를 데이터베이스 백업 스냅샷으로 비교하는 기준은 무엇인가?