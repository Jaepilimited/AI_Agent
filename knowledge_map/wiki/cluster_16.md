# Cluster 16

> Auto-generated 2026-08-19T03:00:36.499205+09:00 · Files: 3

## Purpose
이 클러스터는 SKIN1004 AI Agent가 수집한 비즈니스 지식과 과거 답변 사실(facts)을 체계적으로 관리하고 검색하기 위한 **지식 위키(Knowledge Wiki)** 핵심 모듈을 제공합니다. 수집된 개별 사실들을 엔티티별로 취합하여 정돈된 페이지로 컴파일하고, 검색 쿼리에 맞는 사실을 조회하며, 해당 정보의 신뢰성을 평가하는 역할을 수행합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge/entity_pages.py` — 수집된 SKIN1004 관련 사실들을 기간 및 메트릭별로 그룹화하여 하나의 마크다운 엔티티 페이지로 컴파일하는 모듈입니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge/wiki_search.py` — `knowledge_wiki` 테이블을 대상으로 SQL 기반 검색을 수행하여 사용자 질의에 부합하는 사실을 찾아내는 모듈입니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge/trust.py` — 검색되거나 마이닝된 지식 위키 사실들의 신뢰 상태(Trust-state)를 평가하고 안전성을 검증하는 헬퍼 모듈입니다.

## Key Concepts
- **엔티티 페이지 (Entity Pages)**: Andrej Karpathy의 "LLM wiki" 개념을 적용한 것으로, 특정 엔티티(예: 메가와리 행사, SKIN1004 특정 제품 등)에 대해 알려진 모든 사실을 기간과 메트릭 기준으로 통합한 마크다운 문서입니다.
- **SQL 기반 위키 검색 (SQL-only Search)**: 초기 단계(Week 2 v1)에서는 복잡한 벡터 임베딩이나 LLM 리랭킹 없이, `knowledge_wiki` 테이블에 직접 SQL 쿼리를 수행하여 빠르고 직관적으로 사실을 검색합니다.
- **신뢰 상태 (Trust-state)**: 과거 어시스턴트의 답변 등에서 마이닝된 사실들이 항상 100% 정확한 것은 아니므로, 답변에 안전하게 인용할 수 있는지 신뢰도를 검증하고 관리하는 메커니즘입니다.

## How It Fits In
이 클러스터는 시스템의 장기 기억 및 지식 저장소 역할을 하는 **`concept:knowledge_wiki` (Cluster 22)**를 구체적으로 구현하고 활용하는 레이어입니다. 
- `entity_pages.py`에서 컴파일된 지식과 `wiki_search.py`를 통한 검색 결과는 AI Agent가 SKIN1004 관련 질의(예: 메가와리 실적, 제품 판매 추이 등)에 답변할 때 신뢰할 수 있는 컨텍스트를 제공하는 데 사용됩니다.
- `trust.py`를 통해 필터링된 안전한 사실들만 답변 생성 프롬프트에 주입됨으로써 모델의 환각(Hallucination) 현상을 방지합니다.

## Common Questions This Page Answers
- 특정 SKIN1004 제품이나 메가와리 기간에 대한 흩어진 사실들을 어떻게 하나의 문서로 병합하나요?
  - `entity_pages.py`를 사용하여 기간 및 메트릭별로 그룹화된 마크다운 형식의 엔티티 페이지를 컴파일합니다.
- 벡터 데이터베이스나 임베딩 없이 위키 지식을 어떻게 검색하나요?
  - `wiki_search.py`에서 제공하는 `knowledge_wiki` 대상의 SQL-only 검색 기능을 활용합니다.
- 과거 답변에서 추출한 지식을 AI Agent가 다시 인용할 때, 정보의 신뢰성을 어떻게 검증하나요?
  - `trust.py` 모듈의 Trust-state 헬퍼 함수들을 사용하여 해당 사실의 안전성과 신뢰 등급을 평가합니다.