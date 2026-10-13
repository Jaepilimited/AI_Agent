# Cluster 12

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 5

## Purpose
사용자 질의를 분석해 전문 서브 에이전트로 위임하는 오케스트레이션과 사내 Notion 지식 검색 파이프라인을 담당하는 클러스터입니다. 대화 맥락이 끊기지 않도록 후속 질문을 보강(`followup_rewrite`)하고, direct API 또는 Qdrant Cloud 벡터 검색을 통해 사내 문서를 조회·답변합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/agents/orchestrator.py` — 질의 분석 및 서브 에이전트 위임, 대화 컨텍스트 연속성(`messages passthrough`)을 관리하는 오케스트레이터 코어 (v3.0/v3.1)
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/agents/notion_agent.py` — Notion direct API 기반 서브 에이전트로 허용 목록(allowlist) 내 페이지/DB 블록을 직접 검색 및 조회 (v6.2)
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/agents/qdrant_agent.py` — Gemini embedding과 Qdrant Cloud를 활용하여 사내 Notion 문서를 벡터 검색하고 Gemini Flash로 답변 생성
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/followup_rewrite.py` — 대화 맥락에서 누락된 정보를 채워 단독 실행 가능한 질문으로 재작성하는 모듈
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/model_upgrade_test_2026-02-11.md` — 에이전트 시스템의 모델 업그레이드 테스트 결과 및 평가 리포트

## Key Concepts
- **Sub Agent Delegation (v3.0)**: `orchestrator.py`가 사용자 질의를 단일 실행 경로로 라우팅하던 방식(v2.0)에서 벗어나, 특화된 서브 에이전트(`notion_agent`, `qdrant_agent` 등)로 작업을 분기 및 위임하는 구조.
- **Followup Rewrite**: 라우터나 문서 검색기가 단일 턴의 불완전한 문장만 보고 문맥을 잃어버리는 문제를 방지하기 위해, 직전 대화 기록을 기반으로 질문을 재작성하여 온전한 독립 질의로 변환하는 메커니즘.
- **Notion Vector Search Pipeline**: `notion_vectors_gemini.json`을 단일 진실 공급원(Source of Truth)으로 유지하며, Qdrant Cloud 백엔드 검색 및 Gemini Flash 생성을 연결하는 사내 지식 검색 구조.
- **Allowlisted Notion Access**: MCP 의존성 없이 direct API를 사용하여 사전에 지정된 페이지/데이터베이스 블록만 안전하게 검색·접근하는 방식.

## How It Fits In
- `orchestrator.py`는 질의 라우팅 구현체로 `concept:query_routing` (cluster_18)과 연결되어 질의 분석 및 적절한 에이전트 호출을 총괄합니다.
- `qdrant_agent.py`는 `concept:gemini_embeddings` (cluster_35)을 활용하여 로컬 문서를 임베딩하고 Qdrant Cloud 벡터 검색 쿼리를 수행합니다.

## Common Questions This Page Answers
- 이전 대화의 맥락이 누락된 짧은 후속 질문을 문서/라우터 검색 시 어떻게 처리하는가? (`followup_rewrite.py`)
- 오케스트레이터가 서브 에이전트에게 작업을 위임하고 대화 컨텍스트를 유지하는 방식은 무엇인가? (`orchestrator.py`)
- 사내 Notion 문서를 검색할 때 direct API(`notion_agent.py`)와 Qdrant Cloud 벡터 검색(`qdrant_agent.py`)은 각각 어떤 방식으로 동작하는가?