# Cluster 02

> Auto-generated 2026-08-19T03:00:36.499205+09:00 · Files: 7

## Purpose
이 클러스터는 SKIN1004 AI Agent의 핵심 오케스트레이션(Orchestration) 및 지식 검색/연동 에이전트들을 포함하고 있습니다. 사용자의 질의를 분석하여 적절한 서브 에이전트로 라우팅하고, Notion, Qdrant, 팀별 공유 자료(Google Sheets 등) 및 외부 구글 검색 결과를 결합하여 신뢰할 수 있는 답변과 보고서 맥락을 생성합니다.

## Key Files
- `app/agents/orchestrator.py` — 사용자 질의를 분석하여 최적의 전문 서브 에이전트로 위임하고 대화 컨텍스트를 유지하는 핵심 오케스트레이터 에이전트
- `app/agents/notion_agent.py` — 허용된(Allowlisted) Notion 페이지 및 데이터베이스에 직접 API로 접근하여 블록 콘텐츠를 읽고 답변을 생성하는 에이전트
- `app/agents/qdrant_agent.py` — Qdrant Cloud 벡터 검색을 활용하여 Notion 사내 문서를 검색하고 Gemini Flash를 통해 답변을 생성하는 에이전트
- `app/agents/team_agent.py` — DB HUB에서 동기화된 팀별 자료(Google Sheets, Notion 등)를 키워드 매칭으로 검색하여 링크와 설명을 반환하는 에이전트
- `app/reports/external.py` — 매출 변동 시점의 외부 시장 맥락(구글 검색 기반)을 수집하여 보고서에 제공하는 모듈 (인과관계를 직접 주장하지 않고 서술형 맥락만 제공)
- `app/core/stream_bridge.py` — 동기식 블로킹 제너레이터(LLM 스트리밍 등)를 백그라운드 스레드에서 실행하여 비동기식 제너레이터로 변환해 주는 브릿지 유틸리티
- `docs/superpowers/plans/2026-05-14-notion-sync-skill.md` — Notion 동기화 기능 구현 계획 문서

## Key Concepts
- **Orchestrator Delegation** — `orchestrator.py`는 단일 에이전트 호출 방식(v2.0)에서 발전하여, 질의에 따라 특화된 Sub Agent(Notion, Qdrant, Team 등)로 작업을 위임하고 대화 흐름을 이어갑니다.
- **Notion 사내 문서 검색** — `qdrant_agent.py`는 로컬 JSON 파일(`notion_vectors_gemini.json`)을 소스 오브 트루스(Source of Truth)로 유지하면서, Qdrant Cloud를 백엔드로 삼아 고속 벡터 검색을 수행합니다.
- **외부 맥락 절 (External Context)** — `external.py`는 특정 시점의 매출 변화 요인을 분석할 때, 구글 검색을 통해 당시의 시장 상황이나 외부 이벤트를 단순 나열해 줍니다. LLM이 임의로 상관계수나 인과관계를 왜곡하여 계산하지 않도록 제한합니다.

## How It Fits In
이 클러스터는 외부 데이터 소스(Notion API, Qdrant Cloud, Google Sheets, Google Search)와 AI Agent 코어 시스템을 연결하는 중추 역할을 합니다. 사용자로부터 입력된 메시지는 `orchestrator.py`를 거쳐 각 도메인에 특화된 에이전트(`notion_agent.py`, `qdrant_agent.py`, `team_agent.py`)로 분기되며, 비동기 스트리밍 처리를 위해 `stream_bridge.py`를 공통으로 활용합니다.

## Common Questions This Page Answers
- Notion 사내 문서를 검색할 때 벡터 검색(Qdrant)과 직접 API 호출(Notion Agent)은 각각 어떻게 수행되나요?
- 매출 변동 분석 보고서에서 외부 시장 맥락을 추가할 때 LLM의 환각(Hallucination)이나 잘못된 인과관계 주장을 어떻게 방지하나요?
- 동기식 LLM 스트리밍 API를 비동기 이벤트 루프 환경에서 블로킹 없이 어떻게 처리하나요?