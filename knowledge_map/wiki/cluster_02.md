# Cluster 02

> Auto-generated 2026-09-09T03:00:59.448358+09:00 · Files: 3

## Purpose
이 클러스터는 SKIN1004, COMMONLABS, ZOMBIE BEAUTY 등 브랜드의 고객 서비스(CS) 데이터를 Google Spreadsheet로부터 동기화하고, 이를 기반으로 일관되고 전문적인 Q&A 답변을 생성하는 핵심 백엔드 및 프롬프트 인프라를 제공합니다. 대규모 스프레드시트 데이터를 효율적으로 캐싱하고 가공하여 LLM 컨텍스트에 적합한 형태로 변환하는 역할을 수행합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/agents/cs_agent.py` — Google Spreadsheet의 13개 탭에서 약 1,100개의 Q&A 행을 로드 및 메모리에 캐싱하고, 키워드 매칭을 통해 고객 문의에 답변하는 CS DB Agent입니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/google_sheets.py` — 서비스 계정 인증 정보를 사용하여 Google Sheets API를 호출하고, 가져온 데이터를 LLM 프롬프트에 주입하기 좋은 마크다운(Markdown) 테이블 형식으로 변환하는 유틸리티입니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/prompt_fragments.py` — 여러 에이전트에서 공통으로 사용하는 엔터프라이즈급 포맷팅 상수 및 프롬프트 조각을 정의하여 일관성 있고 전문적인 출력 형식을 보장합니다.

## Key Concepts
- **CS DB Agent** — SKIN1004 및 패밀리 브랜드의 방대한 CS 데이터를 관리하고, 사용자의 질문에 부합하는 최적의 답변을 찾아 제공하는 에이전트입니다.
- **Google Sheets Integration** — 외부 데이터베이스 대신 Google Spreadsheet를 지식 베이스(Knowledge Base)로 활용하여, 비개발자 현업 담당자도 CS Q&A 데이터를 쉽게 업데이트할 수 있도록 지원합니다.
- **Prompt Fragments** — LLM이 정형화되고 전문적인 톤앤매너로 답변을 생성할 수 있도록 돕는 공통 프롬프트 템플릿 조각입니다.

## How It Fits In
이 클러스터는 시스템의 지식 소스(Knowledge Source)와 프롬프트 표준화를 담당합니다. 
- `app/agents/cs_agent.py`는 `concept:cs_agent` (cluster_12) 인터페이스를 구현하여 전체 AI Agent 오케스트레이션 레이어에 통합됩니다.
- `app/core/google_sheets.py`에서 변환된 마크다운 데이터와 `app/core/prompt_fragments.py`의 포맷팅 상수는 CS Agent뿐만 아니라 다른 에이전트들이 LLM과 통신할 때 풍부하고 정제된 컨텍스트를 제공하는 기반이 됩니다.

## Common Questions This Page Answers
- CS Q&A 데이터는 어디서 가져오며 어떻게 관리되나요?
- Google Spreadsheet 데이터를 LLM이 이해하기 쉬운 형태로 어떻게 변환하나요?
- 에이전트들의 답변 출력 형식을 일관되게 유지하는 방법은 무엇인가요?