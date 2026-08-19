# Cluster 45

> Auto-generated 2026-07-08T03:00:19.681328+09:00 · Files: 1

## Purpose
이 클러스터는 Open WebUI와의 통합을 위해 OpenAI 호환 규격의 API 엔드포인트를 제공하는 역할을 합니다. SKIN1004 AI Agent 백엔드가 외부 인터페이스 및 클라이언트와 표준화된 방식으로 통신할 수 있도록 라우팅을 정의합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/api/routes.py` — Open WebUI 연동을 위한 OpenAI 호환 API 엔드포인트를 정의하고 요청을 처리합니다.

## Key Concepts
- **OpenAI-compatible API** — Open WebUI 등 OpenAI API 규격을 지원하는 다양한 외부 클라이언트 도구들이 SKIN1004 AI Agent 시스템과 원활하게 연동될 수 있도록 돕는 표준 인터페이스입니다.
- **Open WebUI Integration** — 사용자가 AI Agent와 상호작용할 수 있는 웹 기반 UI 환경과의 연동을 지원합니다.

## How It Fits In
이 클러스터의 `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/api/routes.py`는 **cluster_41**의 `concept:brand_filter`를 구현(implements)합니다. 이를 통해 API 요청이 들어왔을 때 SKIN1004 및 메가와리(megawari) 등 특정 브랜드 컨텍스트에 맞는 필터링이 API 라우팅 단계에서 올바르게 적용되도록 보장합니다.

## Common Questions This Page Answers
- Open WebUI와 SKIN1004 AI Agent를 연동하기 위한 API 엔드포인트는 어디에 정의되어 있나요?
- 외부 클라이언트가 OpenAI 규격으로 AI Agent에 요청을 보낼 때 브랜드 필터링(`brand_filter`)은 어떻게 적용되나요?