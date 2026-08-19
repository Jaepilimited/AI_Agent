# Cluster 68

> Auto-generated 2026-07-28T03:00:08.710343+09:00 · Files: 2

## Purpose
이 클러스터는 SKIN1004 AI Agent 애플리케이션의 데이터 구조와 상태 관리를 정의하는 핵심 데이터 모델 레이어입니다. 외부 API 요청/응답을 위한 규격과 LangGraph 워크플로우 내에서 에이전트의 상태를 유지하고 전달하기 위한 스키마를 제공합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/models/schemas.py` — OpenAI 호환 API 규격을 따르는 Pydantic 요청(Request) 및 응답(Response) 데이터 모델 정의
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/models/state.py` — LangGraph 기반 에이전트 워크플로우의 실행 상태(State) 및 컨텍스트 관리를 위한 스키마 정의

## Key Concepts
- **OpenAI-compatible API Schemas**: 외부 클라이언트가 SKIN1004 AI Agent와 통신할 때 사용하는 표준화된 데이터 포맷입니다. `schemas.py`에서 Pydantic을 통해 메시지 구조, 토큰 사용량, 채팅 완성(Chat Completion) 응답 등을 검증하고 구조화합니다.
- **LangGraph State**: AI Agent가 메가와리(Megawari) 할인 정보 조회, skin1004 제품 추천 등 복잡한 태스크를 수행할 때, 노드(Node) 간에 공유되는 실행 컨텍스트입니다. `state.py`에서 에이전트의 대화 기록, 현재 실행 단계, 중간 도구(Tool) 호출 결과 등을 추적할 수 있도록 상태 구조를 정의합니다.

## How It Fits In
이 클러스터는 AI Agent 서비스의 뼈대를 이루는 데이터 모델 영역입니다. 
- `schemas.py`에서 정의된 모델은 FastAPI 엔드포인트(Controller) 레이어에서 클라이언트 요청을 파싱하고 최종 응답을 반환할 때 사용됩니다.
- `state.py`에서 정의된 상태 모델은 LangGraph 워크플로우 엔진이 에이전트의 의사결정 흐름(Reasoning Loop)을 제어하고, 적절한 도구를 호출할 때 상태를 유지하는 기반이 됩니다.

## Common Questions This Page Answers
- OpenAI API 규격과 호환되는 채팅 요청/응답 데이터 구조는 어디에 정의되어 있나요?
  - `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/models/schemas.py`에서 확인할 수 있습니다.
- LangGraph 워크플로우가 실행되는 동안 에이전트의 대화 맥락과 상태 정보는 어떻게 관리되나요?
  - `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/models/state.py`에 정의된 State 스키마를 통해 관리됩니다.