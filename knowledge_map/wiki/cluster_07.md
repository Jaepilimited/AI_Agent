# Cluster 07

> Auto-generated 2026-08-19T03:00:36.499205+09:00 · Files: 16

## Purpose
이 클러스터는 SKIN1004 AI Agent 프로젝트의 **지식 맵(Knowledge Map) 구축**, **위키 지식 그래프(Wiki Graph) 분석**, 그리고 **정형 보고서(Reports) 생성 엔진**을 담당합니다. 코드베이스와 문서의 구조를 정적·의미론적으로 분석하여 시각화하고, LLM의 오작동(Hallucination)을 방지하면서 안전하게 비즈니스 지표 보고서를 생성하는 핵심 인프라를 제공합니다.

## Key Files
- `app/knowledge_map/ast_parser.py` — Python AST를 파싱하여 클래스, 함수, 임포트 관계를 추출합니다.
- `app/knowledge_map/semantic.py` — Gemini Flash를 활용해 개별 파일의 개념, 관계, 요약을 추출하는 의미론적 분석 패스입니다.
- `app/knowledge_map/graph.py` — NetworkX와 Louvain 알고리즘을 사용하여 지식 맵 그래프를 구축하고 커뮤니티를 감지합니다.
- `app/knowledge/wiki_graph.py` — 위키 사실(Facts)로부터 엔티티 관계를 추출하여 `wiki_graph_edge`에 저장합니다.
- `app/reports/semantic.py` — LLM이 직접 SQL을 작성하지 못하도록 지표, 축, 필터를 검증된 어휘로 고정하는 의미론 계층입니다.
- `app/reports/spec.py` — "숫자는 코드가 계산하고, 문장은 템플릿이 만든다"는 원칙 하에 보고서 스펙을 정의합니다.
- `app/core/log_scrub.py` — 로그 출력 시 개인정보(`user_id` 등)를 마스킹하는 structlog 프로세서입니다.

## Key Concepts
- **Knowledge Map (지식 맵)**: Claude Code 세션 등을 위해 프로젝트의 소스 코드와 Markdown 문서를 분석하여 정적 관계 그래프를 생성하는 기능입니다.
- **Semantic Layer (의미론 계층)**: LLM 플래너가 보고서 생성 시 직접 SQL을 작성하는 대신, 사전에 정의되고 검증된 지표와 축의 조합만을 선택하도록 제한하여 데이터 일관성을 보장합니다.
- **Wiki Graph & Insights**: 위키 데이터에서 엔티티 간의 관계(src, relation, dst)를 추출하고, 연결성이 높은 God node나 고립된 Orphan 엔티티를 분석하여 지식의 밀도를 관리합니다.

## How It Fits In
- **보안 및 감사**: `app/core/log_scrub.py`는 시스템 전반의 로깅 과정에서 개인정보를 제거하여 `concept:audit_logging` (Cluster 24)을 안전하게 구현합니다.
- **배치 처리**: `app/knowledge_map/semantic.py`는 대규모 파일 분석을 위해 `concept:batch_processing` (Cluster 11) 구조를 활용하여 효율적으로 Gemini API를 호출합니다.
- **사실 추출**: `app/reports/spec.py`는 보고서 생성에 필요한 원천 데이터를 검증하고 정제하기 위해 `concept:fact_extraction` (Cluster 22) 메커니즘과 연계됩니다.

## Common Questions This Page Answers
- **Q. LLM이 잘못된 SQL을 실행하여 엉뚱한 보고서 지표를 만들지 않으려면 어떻게 해야 하나요?**
  - `app/reports/semantic.py`와 `app/reports/spec.py`에 정의된 원칙에 따라, LLM은 사전에 정의된 지표와 축의 조합만 선택할 수 있으며 실제 SQL 생성과 숫자 계산은 엄격하게 통제된 코드 엔진이 수행합니다.
- **Q. 코드베이스의 구조와 문서 간의 연관 관계를 시각화하거나 분석하려면 어떤 모듈을 사용하나요?**
  - `app/knowledge_map` 패키지의 AST/Markdown 파서와 NetworkX 기반의 `graph.py`를 사용하여 프로젝트의 정적 지식 맵을 빌드하고 분석할 수 있습니다.