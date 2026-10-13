# Cluster 09

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 28

## Purpose
운영 신뢰도 및 데이터 드리프트 모니터링, 피드백 기반 품질 개선, 자체 사내 지식 위키(Knowledge Graph/Wiki) 파이프라인을 총괄하는 핵심 모듈 군이다. 프롬프트 수작업 의존을 줄이고 실제 데이터 변화를 감지하며, 실패한 질문을 골든셋으로 승격시켜 지속 가능한 LLM 답변 품질을 유지한다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/golden_runner.py` — 배포 전 및 데일리 골든셋 회귀 테스트 실행.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/failure_learning.py` — 붐따(👎) 피드백을 골든 테스트셋 후보로 승격하여 재발 방지.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/feedback_inbox.py` — 방치되던 붐따 코멘트 수집 및 처리 워크플로 관리.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/schema_watch.py` — BigQuery 스키마 및 테이블 변경사항 모니터링.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/ad_media_watch.py` — 광고 매체 데이터 누락 및 급작스러운 이탈 감지.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/value_lists.py` — DB `DISTINCT` 값을 실시간 주입하여 프롬프트의 정적 데이터 노후화 방지.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/ingredients.py` — 제품 전성분 스프레드시트 적재 및 성분 기반 정밀 필터링.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/company_facts.py` — 사업자등록번호 등 고정 식별값의 지연 없는 즉답 캐싱.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge/wiki_extractor.py` — 대화 기록에서 엔티티 및 사실을 추출하여 위키 데이터 생성.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge/wiki_graph.py` — 위키 사실 기반 엔티티 간 관계(Triple) 그래프 구축.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/reports/store.py` — 원가·마진 등 민감 지표가 포함된 보고서의 대상 지정 열람 권한 제어.

## Key Concepts
- **붐따 피드백 순환 (Failure Learning)**: 사용자가 누른 👎 피드백과 코멘트를 단순 통계 집계에 그치지 않고 골든셋 회귀 평가로 연결하는 체계.
- **Dynamic Value Lists**: 메가와리 프로모션 분기나 국가 목록처럼 주기적으로 변하는 값을 프롬프트에 하드코딩하지 않고 데이터베이스에서 실시간 추출.
- **Knowledge Wiki & Graph**: 과거 대화와 내부 데이터를 `text-embedding-004` 및 Gemini로 축적하여 엔티티별 컴파일 페이지 및 그래프 트리플을 구성하는 사내 지식 체계.
- **Identity Scrubbing**: `scrub_identity_processor`를 통해 로그에서 사용자 식별자(`user_id` 등)를 마스킹하여 개인정보를 보호하는 메커니즘.

## How It Fits In
- **Cluster 25 (Regression & Audit)**: `failure_learning.py`와 `golden_runner.py`가 회귀 테스트 체계를 구현하고, `log_scrub.py`가 감사 로깅 정책을 뒷받침합니다.
- **Cluster 18 (Schema Monitoring)**: `schema_watch.py`를 통해 BigQuery 스키마 드리프트를 조기 경보하여 SQL 생성 라우터 오류를 차단합니다.
- **Cluster 03 (Personalization & KG)**: `personal_briefing_store.py` 및 `wiki_graph.py`가 사용자 개인화 브리핑과 사내 지식 그래프 구축을 담당합니다.
- **Cluster 20 (Reports Management)**: `reports/store.py`가 원가/마진 보고서의 엄격한 사용자 지정 권한 제어를 제공합니다.

## Common Questions This Page Answers
- BigQuery 테이블 구조나 매체 데이터가 갑자기 변경되었을 때 에이전트는 어떻게 감지하는가?
- 오답(붐따)으로 보고된 실패 사례가 다음 배포 때 다시 재발하지 않도록 방지하는 방법은 무엇인가?
- 보고서 조회 권한 및 로그 내 개인정보 마스킹은 어떻게 관리되는가?