# Cluster 17

> Auto-generated 2026-09-09T03:00:59.448358+09:00 · Files: 2

## Purpose
이 클러스터는 SKIN1004 AI Agent가 Notion 사내 문서를 효율적으로 검색하고 동기화할 수 있도록 지원하는 벡터 검색 시스템을 구축합니다. 로컬 JSON 데이터를 소스 오브 트루스(Source of Truth)로 유지하면서, Qdrant Cloud를 실제 벡터 검색 백엔드로 활용하여 고성능 지식 검색 및 답변 생성 기능을 제공합니다.

## Key Files
- `app/agents/qdrant_agent.py` — Gemini 임베딩 모델과 Qdrant Cloud를 연동하여 사내 문서에 대한 벡터 검색을 수행하고, Gemini Flash를 통해 최종 답변을 생성하는 에이전트 핵심 로직입니다.
- `docs/superpowers/plans/2026-05-14-notion-sync-skill.md` — Notion 데이터를 로컬 JSON 파일(`notion_vectors_gemini.json`)과 Qdrant Cloud 간에 일관성 있게 동기화하기 위한 기술적 구현 계획서입니다.

## Key Concepts
- **Qdrant Cloud 벡터 검색** — 사내 지식 데이터베이스(Notion 문서)를 고차원 벡터로 변환하여 저장하고, 사용자의 질문과 가장 유사한 맥락의 문서를 빠르게 찾아내는 백엔드 엔진입니다.
- **소스 오브 트루스 (Source of Truth)** — 로컬에 저장된 `notion_vectors_gemini.json` 파일을 최신 데이터의 기준으로 삼아, Qdrant Cloud의 인덱스가 항상 신뢰할 수 있는 상태를 유지하도록 관리합니다.
- **Gemini Embedding & Flash** — 사용자 질문을 벡터로 변환할 때는 Gemini 임베딩을 사용하고, Qdrant에서 검색된 컨텍스트를 바탕으로 최종 사용자 답변을 구성할 때는 Gemini Flash 모델을 활용합니다.

## How It Fits In
이 클러스터는 AI Agent가 단순한 규칙 기반 답변을 넘어, SKIN1004의 실제 사내 노하우와 Notion 문서에 기반한 정확한 답변을 생성할 수 있도록 돕는 지식 베이스(RAG) 역할을 합니다. 외부 데이터 소스(Notion)와 벡터 데이터베이스(Qdrant)를 연결하는 가교 역할을 수행하며, 다른 에이전트들이 사내 정보를 필요로 할 때 신뢰할 수 있는 API 인터페이스를 제공합니다.

## Common Questions This Page Answers
- AI Agent가 Notion 사내 문서를 검색할 때 어떤 벡터 데이터베이스를 사용하나요?
- 로컬 JSON 파일과 Qdrant Cloud 간의 데이터 일관성은 어떻게 유지하나요?
- 문서 검색 결과를 바탕으로 최종 답변을 생성하는 데 사용되는 AI 모델은 무엇인가요?