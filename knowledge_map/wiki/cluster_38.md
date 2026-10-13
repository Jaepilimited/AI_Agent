# Cluster 38

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 1

## Purpose
SKIN1004 AI Agent 시스템 내부에서 사용자 요청이 처리되는 실행 흐름(request execution flow)의 선언 및 조립을 담당합니다. 1,832개 노드로 구성된 파일 의존성 그래프(`knowledge_map/`)와는 다른 층위이며, 요청 단위가 거쳐 가는 약 25~30개 노드 규모의 실행 파이프라인 단계를 관리합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/flow/__init__.py` — 요청 실행 흐름의 선언과 조립 모듈 진입점

## Key Concepts
- **Request Execution Flow** — 사용자 요청이 들어왔을 때 각 처리 단계(약 25~30 노드)를 따라 순차적 또는 조건부로 진행되는 런타임 실행 흐름입니다.
- **Flow vs Knowledge Map 분리** — 정적 파일 의존 그래프를 의미하는 `knowledge_map/`(1,832 노드)과 런타임 요청 실행 흐름을 혼동하지 않도록 명확히 분리된 구조를 유지합니다.

## How It Fits In
현재 탐지된 타 클러스터 간 명시적 참조 관계는 없습니다(`No cross-cluster relations detected`). 다만 구조적으로는 전체 애플리케이션의 요청 수신 및 처리 시점에서 실행 파이프라인의 조립체 역할을 수행합니다.

## Common Questions This Page Answers
- `app/flow/__init__.py`의 주된 역할과 다루는 흐름은 무엇인가?
- 약 25~30 노드의 요청 실행 흐름은 1,832 노드의 `knowledge_map/`과 어떻게 구별되는가?