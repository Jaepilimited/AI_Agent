# Cluster 25

> Auto-generated 2026-09-09T03:00:59.448358+09:00 · Files: 2

## Purpose
본 클러스터는 SKIN1004 AI Agent 프로젝트의 핵심 진입점이 되는 API 엔드포인트를 정의합니다. 외부 클라이언트(예: Open WebUI)와의 연동을 위한 OpenAI 호환 API 규격을 제공하고, AI 에이전트의 지식 기반이 되는 마크다운(MD) 문서 및 메모리 파일을 시각화하고 편집할 수 있는 관리 도구용 API를 담당합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/api/harness_api.py` — AI Knowledge Base Editor API로, `CLAUDE.md` 및 메모리 파일을 읽고 편집하며 섹션 간 연결 그래프를 생성하는 기능을 제공합니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/api/routes.py` — Open WebUI 등 외부 시스템과의 통합을 위한 OpenAI 호환 API 엔드포인트를 제공합니다.

## Key Concepts
- **AI Knowledge Base Editor**: AI 에이전트가 프로젝트를 이해하는 기반이 되는 `CLAUDE.md` 파일과 메모리 파일들을 시각화하고 직접 편집할 수 있게 해주는 도구입니다.
- **OpenAI-compatible API**: Open WebUI와 같은 표준 클라이언트가 별도의 커스텀 구현 없이 SKIN1004 AI Agent와 통신할 수 있도록 `/v1/chat/completions` 등의 표준 규격을 제공하는 인터페이스입니다.

## How It Fits In
이 클러스터는 외부 요청을 수신하여 시스템 내부의 핵심 비즈니스 로직으로 라우팅하는 게이트웨이 역할을 합니다.
- `app/api/routes.py`는 OpenAI 호환 API 규격(`concept:openai_compatible_api`, cluster_28)을 구현하여 외부 클라이언트의 요청을 처리합니다.
- 수신된 요청은 내부의 오케스트레이터 에이전트(`concept:orchestrator_agent`, cluster_10)로 전달되어 메인 워크플로우를 트리거합니다.
- 요청 처리 과정에서 메가와리(megawari) 등 특정 브랜드 컨텍스트를 필터링하기 위해 브랜드 필터(`concept:brand_filter`, cluster_12)를 적용합니다.

## Common Questions This Page Answers
- Open WebUI와 SKIN1004 AI Agent를 연동하기 위해 어떤 API 엔드포인트를 사용해야 하나요?
- AI 에이전트의 지식 기반이 되는 `CLAUDE.md`나 메모리 파일을 웹 인터페이스에서 시각화하고 수정하려면 어떤 API를 호출해야 하나요?