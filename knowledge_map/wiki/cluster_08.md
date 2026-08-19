# Cluster 08

> Auto-generated 2026-08-19T03:00:36.499205+09:00 · Files: 9

## Purpose
SKIN1004 AI Agent 프로젝트의 핵심 인프라스트럭처와 데이터 처리의 기반이 되는 코어 모듈 및 관련 문서들을 포함하는 클러스터입니다. 외부 LLM API(Gemini, Claude) 및 BigQuery, MariaDB와의 연동을 담당하는 클라이언트와 지식 베이스 구축을 위한 임베딩 유틸리티, 그리고 시스템 마이그레이션 및 보안 대응 문서를 제공합니다.

## Key Files
- `app/core/llm.py` — Gemini (Pro/Flash) 및 Claude Opus 모델을 통합하여 제공하는 이중 LLM 클라이언트 인터페이스
- `app/core/bigquery.py` — 데이터 분석 및 쿼리 실행을 위한 BigQuery 클라이언트
- `app/knowledge/wiki_embed.py` — `text-embedding-004` 모델을 사용하여 `knowledge_wiki` 테이블의 데이터를 벡터화하는 임베딩 유틸리티
- `app/db/models.py` — MariaDB와 연동되는 User 데이터 모델 정의
- `app/core/notify.py` — 윈도우 데스크톱 알림을 전송하는 유틸리티
- `docs/security_response_llm_detail.md` — 보안팀 회신용 LLM 데이터 처리 메커니즘 상세 설명 문서
- `docs/MIGRATION_AI_CRAVER.md` — AI Craver 서버 마이그레이션 실행 가이드 (2026-07-27 ~ 07-30)
- `docs/update_log_2026-08-13.md` — 질문형 보고서, 초상권 사진식별, 골든셋 회귀 등 주요 업데이트 로그

## Key Concepts
- **Dual LLM Client**: Claude Opus를 메인 챗 모델로 사용하고, Gemini Flash를 보조 작업에 배치하여 비용과 성능을 최적화하는 이중화 구조입니다.
- **Wiki Embedding**: 지식 베이스(`knowledge_wiki`) 구축을 위해 Google GenAI의 768차원 `text-embedding-004` 모델을 사용하여 일관성 있는 벡터 스토어를 생성합니다.
- **Security Response**: LLM 호출 시 개인정보 및 민감 데이터가 안전하게 처리되는 방식을 정의한 보안 아키텍처입니다.

## How It Fits In
이 클러스터는 프로젝트 전반의 뼈대를 형성하며 다른 클러스터들과 다음과 같이 긴밀하게 연결됩니다:
- `app/core/bigquery.py`는 안정적인 쿼리 수행을 위해 Circuit Breaker 패턴(`cluster_29`)을 구현하여 연동됩니다.
- `app/core/llm.py`는 LLM의 불안정한 JSON 출력을 보정하기 위해 JSON Repair 메커니즘(`cluster_02`)을 활용합니다.
- `app/db/models.py`는 MariaDB 스키마 정의(`cluster_05`)를 구체화하여 사용자 데이터를 관리합니다.
- `docs/security_response_llm_detail.md`에 기술된 데이터 처리 방식은 MariaDB 커넥션 풀 관리(`cluster_13`)의 보안 및 성능 최적화 설계와 밀접하게 연관되어 있습니다.

## Common Questions This Page Answers
- 프로젝트에서 사용하는 메인 LLM과 보조 LLM은 각각 무엇이며 어떻게 호출하나요?
- `knowledge_wiki` 데이터를 벡터화할 때 사용하는 임베딩 모델과 차원 수는 무엇인가요?
- AI Craver 서버 마이그레이션 이력과 보안팀 대응을 위한 LLM 데이터 처리 상세 메커니즘은 어디서 확인할 수 있나요?