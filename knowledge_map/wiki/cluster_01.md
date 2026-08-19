# Cluster 01

> Auto-generated 2026-08-19T03:00:36.499205+09:00 · Files: 4

## Purpose
이 클러스터는 SKIN1004, COMMONLABS, ZOMBIE BEAUTY 등 브랜드의 고객 서비스(CS) 데이터를 구글 스프레드시트에서 가져와 관리하고, 이를 기반으로 일관되고 전문적인 답변을 생성하는 CS 에이전트의 핵심 기능을 담당합니다. 구글 시트 API 연동, 프롬프트 템플릿 관리, 그리고 시스템 테스트 결과를 포함하고 있습니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/agents/cs_agent.py` — 구글 스프레드시트의 13개 탭에서 약 1,100개의 Q&A 행 데이터를 메모리에 캐싱하고, 키워드 매칭을 통해 고객 문의에 답변하는 CS DB 에이전트입니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/google_sheets.py` — 서비스 계정 인증을 사용하여 구글 스프레드시트 데이터를 읽어오고, LLM 컨텍스트에 주입하기 적합한 마크다운 테이블 형식으로 변환하는 API 래퍼입니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/prompt_fragments.py` — 여러 에이전트에서 공통으로 사용하는 엔터프라이즈급 포맷팅 상수와 프롬프트 조각을 정의하여 일관된 톤앤매너를 유지합니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/test_report_2026-02-11.md` — SKIN1004 엔터프라이즈 AI 시스템의 기능 검증 및 테스트 결과를 기록한 보고서 문서입니다.

## Key Concepts
- **CS DB Agent** — SKIN1004, COMMONLABS, ZOMBIE BEAUTY 브랜드의 방대한 Q&A 데이터를 기반으로 정확한 고객 응대를 수행하는 에이전트입니다.
- **Google Sheets Integration** — 외부 구글 스프레드시트에 저장된 실시간 CS 데이터를 API를 통해 동적으로 동기화하고 마크다운으로 변환하여 LLM의 컨텍스트로 활용합니다.
- **Prompt Fragments** — 답변의 전문성과 일관성을 보장하기 위해 공통으로 적용하는 표준화된 프롬프트 규칙 세트입니다.

## How It Fits In
이 클러스터는 외부 데이터 소스(Google Sheets)로부터 지식 베이스를 구축하고, 이를 정형화된 프롬프트 구조와 결합하여 최종 사용자에게 신뢰할 수 있는 CS 답변을 제공하는 독립적인 데이터 파이프라인 및 에이전트 레이어를 형성합니다. 타 클러스터와의 직접적인 의존성은 감지되지 않았으나, 시스템의 핵심 지식 공급원 역할을 합니다.

## Common Questions This Page Answers
- 구글 스프레드시트의 Q&A 데이터를 어떻게 LLM 컨텍스트로 변환하나요?
- SKIN1004, COMMONLABS 등의 브랜드 CS 문의를 처리하는 에이전트는 어떻게 구현되어 있나요?
- 에이전트들이 출력하는 답변의 일관된 톤앤매너는 어떻게 관리하나요?