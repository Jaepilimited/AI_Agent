# Cluster 28

> Auto-generated 2026-09-09T03:00:59.448358+09:00 · Files: 6

## Purpose
이 클러스터는 SKIN1004 AI Agent의 핵심 인프라 스트럭처와 데이터 모델, 그리고 사용자 인터랙션의 흐름을 제어하는 공통 모듈을 포함합니다. 벡터 검색을 위한 임베딩, Google Workspace(GWS) 인증, LangGraph 상태 관리, API 스키마 정의, 그리고 노션 저장 및 보고서 생성 전 정제 단계와 같은 핵심 유틸리티를 제공합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/embeddings.py` — 벡터 검색 및 시맨틱 유사도 비교를 위해 BGE-M3 임베딩 모델을 로드하고 실행합니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/google_auth.py` — 사용자별 GWS 인증을 위한 Google OAuth2 흐름, 토큰 저장/갱신 및 자격 증명을 관리합니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/notion_save.py` — 사용자의 "이 답변 노션에 넣어줘"와 같은 저장 요청을 감지하고 처리하는 관문 역할을 합니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/models/schemas.py` — OpenAI 호환 API 규격을 충족하기 위한 Pydantic 요청/응답 데이터 모델을 정의합니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/models/state.py` — LangGraph 에이전트 워크플로우의 상태(State) 구조를 정의합니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/reports/clarify.py` — 보고서 생성 전, 모호한 요청에 대해 사용자에게 최소한의 확인 질문(Clarification)을 던져 기준을 정제합니다.

## Key Concepts
- **BGE-M3 Embeddings**: 다국어 지원 및 고성능 검색을 위해 채택된 임베딩 모델로, 사내 지식 검색의 기반이 됩니다.
- **OAuth Token Management**: `data/gws_tokens/{email}` 경로를 포함한 로컬 토큰 파일 및 세션 소스를 통해 사용자별 Google 권한을 안전하게 관리합니다.
- **노션 저장 관문**: 사내 문서 검색(`notion` 라우트)과 "저장" 액션을 명확히 구분하기 위해, 라우터보다 먼저 실행되어 저장 동사가 포함된 요청을 가로챕니다.
- **Clarification (질문 정제)**: 보고서 생성 시 발생할 수 있는 리소스 낭비를 막기 위해, 직전 assistant 메시지의 숨은 표식을 활용하여 세션 테이블 없이도 사용자의 의도를 한 번 더 확인하고 필터 기준을 구체화합니다.

## How It Fits In
이 클러스터는 특정 비즈니스 로직에 국한되지 않고, 에이전트 전체의 뼈대를 형성합니다. `schemas.py`와 `state.py`는 에이전트의 데이터 흐름을 규정하며, `embeddings.py`와 `google_auth.py`는 외부 서비스(벡터 DB, Google Workspace)와의 연동을 지원합니다. 또한 `notion_save.py`와 `clarify.py`는 사용자의 입력이 메인 에이전트 루프나 라우터로 들어가기 전/후 단계에서 인터랙션을 정제하는 필터 역할을 수행합니다.

## Common Questions This Page Answers
- 사용자가 "노션에 저장해줘"라고 요청했을 때, 단순 검색 라우트와 어떻게 구분되어 처리되나요?
- 보고서 생성 요청이 들어왔을 때, 곧바로 생성하지 않고 사용자에게 추가 질문을 던지는 기준과 방식은 무엇인가요?
- 에이전트의 상태(State) 정보와 OpenAI 호환 API 스키마는 어디에 정의되어 있나요?
- Google Workspace API 호출을 위한 사용자 인증 토큰은 어떻게 관리되고 갱신되나요?