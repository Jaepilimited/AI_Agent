# Cluster 31

> Auto-generated 2026-09-09T03:00:59.448358+09:00 · Files: 3

## Purpose
본 클러스터는 SKIN1004 AI Agent 프로젝트의 핵심 비즈니스 가치 제안, 보안 아키텍처, 그리고 사용자 리텐션을 극대화하기 위한 선제적 개인화 브리핑 시스템의 설계 및 운영 문서를 포함합니다. 사용자가 질문하기 전에 먼저 필요한 정보를 제공하여 활성 사용자 비율을 높이고, 기업 보안 요구사항에 부합하는 LLM 데이터 처리 메커니즘을 정의합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/briefing.py` — 사용자의 능동적 방문을 유도하기 위해 매일 아침 맞춤형 정보를 선제적으로 전달하는 개인화 데일리 브리핑 핵심 로직
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/AI_Agent_5문항_답변_2026-08.md` — AI Agent 도입 및 운영과 관련하여 운영본부의 주요 5가지 질문에 대응하는 공식 답변 문서
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/security_response_llm_detail.md` — 사내 보안팀 회신을 위해 LLM의 데이터 처리 흐름, 프롬프트 보안 및 데이터 비식별화 메커니즘을 상세히 기술한 문서

## Key Concepts
- **개인화 데일리 브리핑 (Personalized Daily Briefing)**: 사용자가 직접 질문을 입력하지 않아도, 시스템이 먼저 유용한 비즈니스 인사이트를 찾아 제공하는 기능입니다. 30일 활성 사용자 중 단 하루만 사용하는 유저 비율(38%)을 개선하기 위해 고안되었습니다.
- **LLM 데이터 처리 보안 (LLM Data Security)**: LLM API 호출 시 민감한 사내 정보나 개인정보가 외부로 유출되지 않도록 보장하는 보안 필터링 및 데이터 처리 메커니즘입니다.

## How It Fits In
이 클러스터는 AI Agent의 비즈니스 생존 전략 및 보안 신뢰성을 담당하며 다른 기술 클러스터와 다음과 같이 연결됩니다:
- `app/core/briefing.py`는 데일리 브리핑에 필요한 실시간 지표 및 통계 데이터를 조회하기 위해 BigQuery 클라이언트(`concept:bigquery_client` - cluster_06)를 호출합니다.
- `docs/AI_Agent_5문항_답변_2026-08.md`에 명시된 보안 및 권한 제어 정책은 시스템의 역할 기반 접근 제어(`concept:role_based_access_control` - cluster_38) 아키텍처를 통해 실제 코드로 구현됩니다.

## Common Questions This Page Answers
- **Q1. 사용자가 AI Agent를 자주 방문하게 만들기 위해 어떤 전략을 취하고 있나요?**
  - 단순히 질문에 답하는 수동적 도구에서 벗어나, `app/core/briefing.py`를 통해 매일 아침 개인화된 비즈니스 인사이트를 선제적으로 배달하는 "찾아가는 서비스"를 제공하여 리텐션을 강화합니다.
- **Q2. LLM 사용 시 사내 데이터 유출 우려에 어떻게 대응하고 있나요?**
  - `docs/security_response_llm_detail.md`에 정의된 LLM 데이터 처리 메커니즘에 따라, 민감 데이터의 비식별화 조치 및 안전한 API 프록시 레이어를 거쳐 보안팀의 요구사항을 충족합니다.