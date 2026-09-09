# Cluster 40

> Auto-generated 2026-09-09T03:00:59.448358+09:00 · Files: 1

## Purpose
SKIN1004 AI Agent 프로젝트에서 사용자 요청이 처리되는 핵심 실행 흐름(Execution Flow)을 선언하고 조립하는 역할을 합니다. 시스템에 들어온 요청이 어떤 단계와 노드를 거쳐 처리되는지 정의하는 진입점 역할을 수행합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/flow/__init__.py` — AI Agent의 요청 실행 흐름을 구성하는 노드들을 선언하고 연결하여 전체적인 Flow를 조립합니다.

## Key Concepts
- **실행 흐름 (Execution Flow)** — 사용자의 요청이 들어왔을 때, AI Agent 내부에서 순차적 혹은 조건부로 실행되는 약 25~30개의 노드로 구성된 처리 경로입니다.
- **Knowledge Map과의 차이점** — 시스템의 전체 파일 의존 관계를 나타내는 `knowledge_map/`(1,832개 노드)과 달리, 이 Flow는 실제 런타임에 요청이 통과하는 실행 단계(약 25~30개 노드)만을 다루는 독립적인 층위입니다.

## How It Fits In
이 클러스터는 외부에서 들어온 요청을 받아 AI Agent 내부의 비즈니스 로직과 LLM 처리 파이프라인으로 연결해 주는 뼈대 역할을 합니다. 다른 컴포넌트들과 직접적인 의존 관계가 명시되지는 않았으나, 시스템의 런타임 실행 흐름을 제어하는 핵심 컨트롤러 역할을 담당합니다.

## Common Questions This Page Answers
- AI Agent가 사용자 요청을 받았을 때 어떤 순서와 흐름으로 작업을 처리하나요?
- 시스템 의존성 그래프(`knowledge_map/`)와 실제 요청 처리 흐름(Flow)은 어떻게 다른가요?