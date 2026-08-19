# Cluster 46

> Auto-generated 2026-07-04T03:00:14.613803+09:00 · Files: 10

## Purpose
이 클러스터는 SKIN1004 AI Agent 프로젝트의 핵심 백본(Backbone) 인프라와 공통 코어를 담당합니다. 이중 LLM 클라이언트 지원, RAG(Retrieval-Augmented Generation) 파이프라인, SQL 안전성 검증, 그리고 시스템의 자율적 성장을 측정하는 모니터링 도구를 통합하여 제공합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/bigquery.py` — BigQuery 쿼리 실행 및 클라이언트 관리 모듈
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/chart.py` — 프론트엔드에서 인터랙티브하게 렌더링할 수 있는 Chart.js 설정 JSON 생성 모듈
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/embeddings.py` — 벡터 검색을 위한 BGE-M3 임베딩 모델 관리
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/growth_report.py` — SQL 캐시 히트율 및 신규 패턴 등을 측정하여 시스템의 자율적 성장을 기록하는 주간 보고서 모듈
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/llm.py` — Gemini 3 Pro와 Claude (Opus 4.6 / Sonnet 4.6)를 통합 지원하는 이중 LLM 클라이언트 인터페이스
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/response_formatter.py` — 일관된 마크다운 레이아웃과 시각적 위계를 보장하는 응답 포스트 프로세서
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/security.py` — Text-to-SQL 에이전트가 생성한 SQL의 실행 전 안전성을 검증하는 보안 모듈
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/rag/chunker.py` — Semantic(의미론적) 및 Hierarchical(계층적) 방식을 결합한 하이브리드 청킹 모듈
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/rag/indexer.py` — BigQuery 벡터 인덱싱 및 RAG 임베딩 빌드 모듈
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/rag/parser.py` — Docling 라이브러리를 활용하여 PDF, HWP, PPT 문서를 마크다운으로 변환하는 파서

## Key Concepts
- **Dual LLM Client**: Open WebUI의 모델 선택에 따라 Gemini 3 Pro와 Claude 모델을 유연하게 전환하여 호출할 수 있는 단일 인터페이스를 제공합니다.
- **Hybrid Chunking**: RAG 검색 정확도를 높이기 위해 단순 길이 기준 분할이 아닌, 의미론적(Semantic) 흐름과 문서 구조의 계층(Hierarchical)을 모두 고려하여 텍스트를 최적의 단위로 쪼갭니다.
- **SQL Safety Validation**: Text-to-SQL 에이전트가 생성한 쿼리가 데이터베이스에 해를 끼치지 않도록 실행 전에 구문을 분석하고 안전성을 검증합니다.
- **Growth Report**: 시스템이 자율적으로 진화하는 과정을 모니터링하기 위해 `sql_cache_hits`, `sql_cache_new` 등의 지표를 수집하고 분석합니다.

## How It Fits In
이 클러스터는 다른 상위 에이전트나 비즈니스 로직 레이어에 핵심 유틸리티와 인프라 서비스를 제공하는 공통 레이어입니다. 명시적인 타 클러스터 간 의존성은 감지되지 않았으나, 프로젝트 내의 모든 LLM 호출, RAG 기반 문서 검색, BigQuery 연동 및 보안 검증이 이 클러스터의 모듈들을 기반으로 수행됩니다.

## Common Questions This Page Answers
- PDF, HWP, PPT 등 다양한 포맷의 문서를 어떻게 파싱하고 RAG용 벡터로 인덱싱하나요?
- Text-to-SQL 에이전트가 생성한 SQL 쿼리의 보안 및 안전성은 어떻게 보장하나요?
- 프론트엔드에서 정적 이미지가 아닌 인터랙티브한 차트를 보여주기 위해 백엔드는 어떤 데이터를 제공하나요?
- 시스템이 자율적으로 학습하고 성장하는 지표(Growth Report)는 어떻게 측정되나요?