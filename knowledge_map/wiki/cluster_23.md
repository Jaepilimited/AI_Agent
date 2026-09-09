# Cluster 23

> Auto-generated 2026-09-09T03:00:59.448358+09:00 · Files: 7

## Purpose
Cluster 23은 SKIN1004 AI Agent 프로젝트에서 데이터의 신뢰성과 보고서 생성의 정확성을 보장하는 핵심 인프라를 제공합니다. LLM의 환각(Hallucination)을 방지하기 위해 SQL 생성과 지표 계산을 엄격한 규칙 기반 템플릿으로 제한하고, 원본 데이터베이스의 최신 상태(Data Freshness)를 스스로 감시하는 메커니즘을 구축합니다.

## Key Files
- `app/core/data_freshness.py` — 파생 사본이 원본 데이터베이스의 업데이트를 지연 없이 따라가고 있는지 시스템이 스스로 감시하는 모듈
- `app/db/models.py` — MariaDB와 연동되는 사용자(User) 정보 등 핵심 데이터 모델 정의
- `app/knowledge_map/md_parser.py` — 마크다운 파일의 헤더 구조(H1-H6), 링크, 파일명 기반 날짜 등을 파싱하는 도구
- `app/knowledge_map/semantic.py` — Gemini Flash를 활용하여 개별 파일의 개념, 관계, 요약을 추출하는 의미론적 분석기
- `app/reports/blocks.py` — 보고서를 구성하는 최소 단위인 분석 블록을 정의하며, LLM 대신 사전에 정의된 규칙을 통해 지표 발견(Finding) 문장을 기계적으로 생성
- `app/reports/semantic.py` — 지표, 축, 필터를 검증된 어휘로 고정하여 LLM이 SQL을 직접 작성하지 않고도 안전하게 데이터를 조회할 수 있도록 돕는 의미론 계층
- `app/reports/spec.py` — 보고서에서 무엇을 조회하고 점검하며 계산할지 선언하는 스펙 정의서

## Key Concepts
- **숫자는 코드가, 문장은 템플릿이 (Numbers by Code, Text by Template)** — LLM이 보고서의 핵심 숫자나 통계치를 직접 작성하지 못하도록 차단합니다. "1위가 전체의 X%를 차지한다"와 같은 발견(Finding) 문장은 데이터로부터 기계적인 규칙에 의해 생성됩니다.
- **의미론 계층 (Semantic Layer)** — 플래너(LLM)는 "어떤 지표를 어떤 축으로 볼 것인가"만 결정하고, 실제 SQL 쿼리는 검증된 조합 안에서 안전하게 자동 생성되도록 제약합니다.
- **데이터 신선도 감시 (Data Freshness)** — 사람이 데이터 누락이나 지연을 인지하기 전에, 시스템이 스스로 원본 데이터의 업데이트 여부를 추적하고 동기화 상태를 점검합니다.

## How It Fits In
이 클러스터는 보고서 작성 및 데이터 추출의 안정성을 담보하는 구체적인 구현체입니다.
- `app/reports/blocks.py`는 **cluster_06**의 `concept:report_blocks`를 구현하여, 정형화된 보고서 블록 생성 규칙을 제공합니다.
- `app/reports/spec.py`는 **cluster_09**의 `concept:fact_extraction`을 구현하여, LLM의 개입 없이 사실 관계(Fact)를 안전하게 추출하는 명세를 정의합니다.

## Common Questions This Page Answers
- **Q. LLM이 보고서 작성 중 잘못된 숫자나 SQL을 생성하는 것을 어떻게 방지하나요?**  
  A. `app/reports/semantic.py`와 `app/reports/spec.py`를 통해 LLM의 역할을 지표와 축의 선택으로 제한하고, 실제 SQL 생성과 지표 계산은 사전에 검증된 규칙과 코드가 전담합니다.
- **Q. 데이터베이스 동기화가 지연되는 문제를 시스템이 어떻게 스스로 감지하나요?  
  A. `app/core/data_freshness.py`가 파생 사본과 원본의 상태를 비교하여 사람이 인지하기 전에 선제적으로 데이터 신선도를 모니터링합니다.