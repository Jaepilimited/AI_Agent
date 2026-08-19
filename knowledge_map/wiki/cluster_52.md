# Cluster 52

> Auto-generated 2026-07-08T03:00:19.681328+09:00 · Files: 5

## Purpose
이 클러스터는 SKIN1004 AI Agent의 핵심 유틸리티 및 모니터링 기능을 제공합니다. 사용자의 Google Workspace 연동을 위한 OAuth2 인증 및 API 래퍼를 제공하고, 시스템의 자율적 성장 지표와 답변 품질을 측정하며, 내부 지식 데이터베이스에서 필요한 정보를 검색하는 기초적인 검색 기능을 담당합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/google_auth.py` — 사용자별 Google Workspace 인증을 위한 OAuth2 흐름, 토큰 저장 및 갱신을 관리합니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/google_workspace.py` — Gmail, Drive, Calendar API 호출을 처리하는 상태 비저장(Stateless) API 래퍼 함수들을 포함합니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/growth_report.py` — SQL 캐시 히트 수, 신규 SQL 패턴 수, 스킬 메모리 등 시스템의 자율적 성장 지표를 측정하는 주간 성장 보고서를 생성합니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/quality_monitor.py` — 사용자 피드백(👍/👎) 기반의 정확도, 컨텍스트 길이, 응답 속도 등 일일 답변 품질 스냅샷을 모니터링합니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge/wiki_search.py` — 벡터 임베딩이나 LLM 리랭킹 없이 `knowledge_wiki` 테이블에 대해 SQL 기반의 단순 키워드 검색을 수행합니다.

## Key Concepts
- **Google Workspace 연동**: OAuth2 프로토콜을 통해 안전하게 사용자 권한을 획득하고, `data/gws_tokens/` 경로에 토큰을 관리하며 Gmail, Calendar 등의 API를 호출합니다.
- **자율 성장 지표 (Autonomous Growth Metrics)**: 에이전트가 반복되는 질문을 빠르게 처리하기 위해 활용하는 `sql_cache_hits` 및 새로 학습한 `sql_cache_new` 패턴 등을 추적하여 시스템의 발전 정도를 정량화합니다.
- **품질 모니터링 (Quality Monitoring)**: 최근 24시간 동안의 메시지 피드백 데이터를 바탕으로 `accuracy_rate` (👍 / (👍 + 👎)) 및 응답 속도를 계산하여 서비스 품질을 유지합니다.
- **Wiki 검색 (Wiki Search)**: 복잡한 벡터 검색 도입 전 단계로서, SQL 쿼리만을 이용해 `knowledge_wiki` 내에 저장된 사실 관계 데이터를 빠르게 찾아냅니다.

## How It Fits In
이 클러스터는 외부 서비스 연동(Google Workspace)과 시스템의 자가 진단(품질 및 성장 모니터링), 그리고 지식 검색(Wiki Search)이라는 독립적이면서도 필수적인 백엔드 코어 기능들을 모아두었습니다. 타 클러스터와의 명시적인 의존 관계는 감지되지 않았으나, 에이전트가 사용자의 일정을 관리하거나 이메일을 조회하고, 스스로의 성능을 모니터링하여 최적화하는 과정에서 기반 인프라로 작동합니다.

## Common Questions This Page Answers
- 에이전트가 사용자의 Google Calendar나 Gmail에 접근하기 위해 어떻게 인증을 처리하나요?
- 시스템이 자율적으로 성장하고 있는지(SQL 캐시 효율 등) 어떻게 측정하나요?
- 에이전트 답변에 대한 사용자의 긍정/부정 피드백과 응답 속도는 어디서 수집하고 분석하나요?
- 내부 지식 위키(`knowledge_wiki`)에서 정보를 검색할 때 어떤 방식을 사용하나요?