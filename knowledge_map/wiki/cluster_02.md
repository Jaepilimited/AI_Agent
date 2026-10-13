# Cluster 02

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 3

## Purpose
이 클러스터는 SKIN1004, COMMONLABS, ZOMBIE BEAUTY 브랜드의 고객 응대(CS) Q&A 데이터를 처리하는 에이전트와 외부 Google API 연동 코어를 담당합니다. Google Sheets를 통해 스프레드시트 Q&A 문서를 조회·변환하고, 사용자 토큰 기반의 Google 메일 발송 기능을 제공하여 CS 관련 질의응답 및 알림 워크플로우를 지원합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/agents/cs_agent.py` — BP Agent 구현체. SKIN1004, COMMONLABS, ZOMBIE BEAUTY 등 13개 탭에서 약 1,100건의 Q&A 행을 메모리에 캐싱하고 키워드 매칭 기반으로 CS 제품 관련 질의에 답변.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/google_sheets.py` — Service account 인증을 사용하는 Google Sheets API 래퍼. 스프레드시트 데이터를 읽어 LLM 컨텍스트에 삽입 가능한 Markdown 테이블로 변환.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/mail_google.py` — 메일 발송용 Google API 호출 모듈. 사용자 본인 토큰을 사용하며, WAS 프록시 설정 시 `HTTPS_PROXY` 환경변수를 통해 동작.

## Key Concepts
- **cs_agent (BP Agent)** — 스킨1004 패밀리 브랜드(SKIN1004, COMMONLABS, ZOMBIE BEAUTY)의 13개 탭, 약 1,100개 Q&A 행을 캐싱해 CS 관련 제품 질의를 키워드 매칭 등으로 해결하는 에이전트.
- **Google Sheets Markdown Conversion** — `google_sheets.py`를 통해 Google Sheets 데이터를 조회하고 이를 Markdown 형식으로 변환하여 LLM 프롬프트/컨텍스트로 전달하는 처리 방식.
- **사용자 토큰 메일 발송** — `mail_google.py`에서 서비스 계정이 아닌 사용자 본인 토큰을 이용해 Google 메일을 발송하며, WAS 환경에서는 `HTTPS_PROXY`를 통해 프록시를 경유.

## How It Fits In
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/agents/cs_agent.py`는 `cluster_25`의 `concept:cs_agent` 인터페이스 및 역할을 구현합니다.
- 본 클러스터의 `google_sheets.py`와 `mail_google.py`는 `cs_agent.py` 및 프로젝트 내 다른 에이전트들이 외부 Google 서비스(시트 조회 및 메일 발송)를 활용할 수 있도록 공통 코어(core) 계층을 제공합니다.

## Common Questions This Page Answers
- CS 제품 질의응답을 위해 어떤 브랜드 데이터(SKIN1004, COMMONLABS, ZOMBIE BEAUTY)와 몇 개의 탭/행을 캐싱하는가?
- Google Sheets의 데이터를 LLM 컨텍스트로 전달하기 위해 어떤 형식(Markdown table)으로 변환하는가?
- `mail_google.py` 호출 시 어떤 인증 토큰을 사용하며, WAS 프록시 환경에서는 어떤 환경변수(`HTTPS_PROXY`)를 타는가?