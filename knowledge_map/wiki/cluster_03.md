# Cluster 03

> Auto-generated 2026-08-19T03:00:36.499205+09:00 · Files: 5

## Purpose
이 클러스터는 SKIN1004 AI Agent 시스템의 핵심 안전장치(Safety), 정적 검증(Static Validation), 메타데이터 동기화 및 멀티모달 검색 엔진을 포함하는 핵심 유틸리티 및 에이전트 군으로 구성되어 있습니다. 생성된 SQL의 보안을 검증하고, 시스템 전반의 무오류를 보장하는 정적 검사를 수행하며, 보고서 템플릿의 하드코딩을 방지하고, 얼굴 및 제품 이미지 검색을 지원합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/agents/face_clip_agent.py` — Drive 인덱스 기반 CLIP 및 InsightFace를 활용한 얼굴/제품 사진 검색 에이전트
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/schema_docs.py` — Notion의 BigQuery 데이터베이스 정의서를 기반으로 컬럼 설명을 동기화하는 모듈
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/security.py` — Text-to-SQL 에이전트가 생성한 SQL의 안전성을 검증하는 보안 모듈
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/static_checks.py` — 코드와 자산을 읽어 에러가 나지 않는 고장(Silent Failures)을 잡아내는 정적 검사 모듈
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/reports/render.py` — 페이로드와 템플릿을 결합하여 HTML 보고서를 렌더링하고 하드코딩된 숫자를 방지하는 모듈

## Key Concepts
- **정적 자가 점검 (Static Self-Checks)**: `static_checks.py`는 개발 단계(`test_no_silent_failures.py`)와 서버 운영 환경 모두에서 동일한 규칙으로 시스템 고장을 진단할 수 있도록 단일 판정 로직을 제공합니다.
- **SQL 안전성 검증 (SQL Safety Validation)**: `security.py`는 생성된 모든 SQL이 실행되기 전에 악의적인 쿼리나 비정상적인 연산을 수행하지 않는지 정적으로 검증합니다.
- **템플릿 리터럴 제한**: `render.py`는 보고서 템플릿 내에 하드코딩된 숫자 리터럴이 남지 않도록 강제하며, 모든 수치는 `{{ derived.pnl.H1_26.sales | eok }}`와 같은 슬롯 형태로만 주입되도록 제한합니다.

## How It Fits In
- **OCR Reranking 연계**: `face_clip_agent.py`는 이미지 검색 성능을 고도화하기 위해 `concept:ocr_reranking` (cluster_13)을 구현하여 활용합니다.
- **BigQuery 메타데이터 연계**: `schema_docs.py`는 `INFORMATION_SCHEMA`만으로는 파악할 수 없던 컬럼의 상세한 한글 의미를 Notion 정의서로부터 가져와 `concept:bigquery_metadata` (cluster_05)에 동기화하고 앱에 전달합니다.
- **Text-to-SQL 보안**: `security.py`는 `concept:text_to_sql` (cluster_30) 에이전트가 생성한 SQL이 데이터베이스에서 실행되기 직전에 필수적으로 거쳐야 하는 보안 필터 역할을 합니다.

## Common Questions This Page Answers
- **Q. 생성된 SQL이 안전한지 어떻게 검증하나요?**
  - `app/core/security.py`가 제공하는 SQL safety validation을 통해 실행 전 정적 검증을 통과해야만 쿼리가 실행됩니다.
- **Q. 보고서 템플릿에 숫자를 직접 하드코딩하면 어떻게 되나요?**
  - `app/reports/render.py` 내부의 `lint_template()` 검사에 의해 차단되며, 모든 숫자는 반드시 슬롯 형태로 템플릿에 주입되어야 합니다.
- **Q. 테스트 환경과 서버 환경에서 정적 검사 규칙이 달라져 발생하는 문제는 어떻게 해결했나요?**
  - `app/core/static_checks.py`에 단일 판정 함수를 정의하여, `pytest` 환경과 서버 자가 점검 루틴이 동일한 검사 로직을 공유하도록 일원화했습니다.