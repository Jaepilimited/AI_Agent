# Cluster 10

> Auto-generated 2026-09-09T03:00:59.448358+09:00 · Files: 3

## Purpose
이 클러스터는 SKIN1004 AI Agent의 핵심 라우팅 및 정보 검색 아키텍처를 담당합니다. 사용자의 질문을 분석하여 적절한 서브 에이전트로 분배하는 오케스트레이터와, 노션(Notion) 워크스페이스 및 MariaDB에 저장된 스킨1004 제품 정보를 안전하게 조회하는 기능을 제공합니다.

## Key Files
- `app/agents/orchestrator.py` — 사용자 질의를 분석하고 적절한 전문 서브 에이전트(Specialized Sub Agent)로 작업을 위임하며, 대화 맥락(Conversation Context)을 유지하는 컨트롤 타워 역할을 수행합니다.
- `app/agents/notion_agent.py` — MCP 의존성 없이 Notion API를 직접 호출하여 허용된(Allowlisted) 페이지 및 데이터베이스의 블록 콘텐츠를 검색하고 답변을 생성합니다.
- `app/core/product_info.py` — 노션의 `스킨1004 전제품 한 눈에 파악하기` 데이터를 MariaDB로 동기화하고 관리하는 제품 정보 스펙 모듈입니다.

## Key Concepts
- **Orchestrator (오케스트레이터)**: v3.0 이상 버전에서 도입된 핵심 구조로, 단순한 쿼리 분석(Query Analyzer)을 넘어 여러 전문 서브 에이전트에게 역할을 위임하고 대화의 연속성(Context Continuity)을 보장합니다.
- **Notion Sub Agent**: 보안을 위해 사전에 지정된 화이트리스트(Allowlist) 범위 내의 노션 페이지와 데이터베이스만 접근하여 정보를 탐색하는 에이전트입니다.
- **제품 정보 vs 제품 Q&A**: 2026-09-04 지침에 따라 엄격히 구분됩니다. `product_info.py`가 다루는 **제품 정보**는 스킨1004의 공식 제품 라인 및 상세 스펙(CS 데이터 영역)을 의미하며, 실제 고객 문의 대응 기록인 **제품 Q&A**(`[BD_BP] CS제품문의_모음집` 시트, 914건)와는 명확히 분리되어 처리됩니다.

## How It Fits In
이 클러스터는 AI Agent 시스템의 중추 신경계 역할을 합니다. 외부 사용자의 입력이 들어오면 `orchestrator.py`가 이를 수신하여 분석한 뒤, 노션 내부 지식 검색이 필요할 경우 `notion_agent.py`를 호출하고, 정형화된 스킨1004 제품 스펙 정보가 필요할 경우 `product_info.py`를 통해 MariaDB의 데이터를 조회하여 최적의 답변을 구성합니다.

## Common Questions This Page Answers
- 사용자의 질문이 들어왔을 때 어떤 서브 에이전트가 처리할지 어떻게 결정하나요?
- 노션(Notion) 연동 시 보안을 위해 접근 가능한 페이지를 어떻게 제한하나요?
- 스킨1004의 공식 제품 스펙 정보와 일반 CS 제품 Q&A 데이터는 코드상에서 어떻게 다르게 취급되나요?