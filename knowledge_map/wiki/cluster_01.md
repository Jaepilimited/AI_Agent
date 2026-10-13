# Cluster 01

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 2

## Purpose
이 클러스터는 SKIN1004 AI Agent 애플리케이션의 에이전트 패키지 구성 및 사내 도메인 특화 용어 전처리를 담당합니다. 사용자가 입력하는 사내 은어, 제품 축약어, 흔한 오타를 LLM 호출 전에 정식 명칭으로 보정하여 모델의 질의 이해도를 높이고 에이전트 패키지 환경을 초기화합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/agents/__init__.py` — 애플리케이션 내 에이전트 구현체를 패키지화하는 Python 패키지 초기화 파일
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/term_aliases.py` — 사내 은어·축약어·오타를 LLM 호출 전에 정식 명칭으로 변환 및 보정하는 사내 용어 사전 모듈

## Key Concepts
- **사내 용어 사전 (`term_aliases.py`)** — "센앰"(센텔라 앰플), "포마"/"pm"(포어마이징), "프바시"(프로바이오시카) 등의 축약어나 "힝라우선세럼"(히알루시카 선세럼) 같은 오타를 보정하는 사전. 사외에서는 알 수 없는 SKIN1004 내부 지식을 LLM 전달 전에 매핑해 주는 전처리 계층입니다.
- **에이전트 패키지 (`app/agents`)** — AI agent 구현체들을 모듈화하여 관리하는 핵심 패키지 네임스페이스.

## How It Fits In
현재 탐지된 외부 클러스터 연계는 없으나, `term_aliases.py`의 용어 정제 처리는 사용자 입력이 LLM 에이전트 파이프라인으로 전달되기 전 단계에서 동작하며, `app/agents/__init__.py`는 에이전트 실행 환경의 기반 구조를 형성합니다.

## Common Questions This Page Answers
- "센앰", "포마", "프바시" 등 사용자가 입력한 축약어나 은어는 어디서 정식 명칭으로 변환되나요?
- 히알루시카 선세럼 오타("힝라우선세럼") 같은 입력 실수를 LLM 호출 전에 보정하는 로직은 어떤 파일에 있나요?
- AI agent 모듈 패키지는 프로젝트 내 어디에 초기화되어 있나요?