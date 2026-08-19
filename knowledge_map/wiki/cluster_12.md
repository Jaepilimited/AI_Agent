# Cluster 12

> Auto-generated 2026-08-19T03:00:36.499205+09:00 · Files: 4

## Purpose
이 클러스터는 SKIN1004 AI Agent 프로젝트에서 정형화된 보고서(Report)를 생성하고 관리하는 핵심 엔진과 스펙 정의를 담당합니다. LLM의 확률적 판정을 배제하고 명확한 규칙 기반의 스펙 매핑과 품질 게이트(Quality Gate) 검증을 거쳐 신뢰할 수 있는 보고서 데이터를 산출합니다.

## Key Files
- `app/reports/engine.py` — 보고서 스펙을 기반으로 조회, 품질 게이트 검증, 파생 지표 계산을 순차적으로 실행하여 최종 `payload`를 생성하는 실행 엔진입니다.
- `app/reports/registry.py` — 사용자 질문을 분석하여 적절한 보고서 스펙과 파라미터로 매핑하는 레지스트리입니다. LLM을 사용하지 않고 결정론적으로 처리합니다.
- `app/reports/specs/cost_efficiency.py` — FOC(무상지원품) 및 바우처 비용 효율화를 분석하는 구체적인 보고서 스펙 파일입니다.
- `docs/weekly_report_2026-04-23.md` — 2026년 4월 17일부터 4월 23일까지의 주간 업무 보고 문서입니다.

## Key Concepts
- **Payload 구조**: 보고서 엔진이 출력하는 표준 데이터 구조로, 메타데이터(`meta`), 원천 데이터(`facts`), 품질 검증 결과(`gates`), 계산된 파생 지표(`derived`)를 포함합니다.
- **결정론적 스펙 매핑 (Deterministic Mapping)**: `registry.py`는 보고서 생성 시 LLM을 배제합니다. 지원하지 않는 주제는 환각(Hallucination)을 방지하기 위해 단호히 "없음"으로 응답하며, 기간 및 국가/팀 리터럴을 명확히 교정합니다.
- **비용 효율화 스펙 (Cost Efficiency Spec)**: `Production_Cost2` 등 CLAUDE.md의 "원가·FOC·할인 집계 계약"을 준수하여 B2B FOC 및 B2C 할인 비용 효율성을 재현 가능한 파이프라인으로 계산합니다.

## How It Fits In
이 클러스터는 보고서의 신뢰성을 보장하기 위해 **Cluster 07**의 핵심 개념들을 구체적으로 구현합니다.
- `app/reports/engine.py` 및 `app/reports/specs/cost_efficiency.py`는 데이터의 정합성을 검증하기 위해 **Cluster 07**의 `concept:quality_gate`를 구현하여 적용합니다.
- `app/reports/registry.py`는 **Cluster 07**의 `concept:report_specification` 인터페이스를 구현하여, 입력된 질문에 대응하는 정확한 보고서 스펙을 매핑합니다.

## Common Questions This Page Answers
- **Q1. 보고서 생성 과정에서 LLM을 사용하지 않는 이유는 무엇인가요?**
  - 보고서 종류와 기간 표현은 유한하므로, 확률적 판정을 도입하면 "왜 특정 기간의 데이터가 도출되었는지" 설명할 수 없는 문제가 발생합니다. 데이터 신뢰성을 위해 결정론적 규칙과 후처리를 사용합니다.
- **Q2. 보고서 엔진이 출력하는 Payload의 세부 구성은 어떻게 되나요?**
  - 스펙 ID와 파라미터가 담긴 `meta`, 원천 로우 데이터인 `facts`, 품질 통과 여부를 나타내는 `gates`, 그리고 최종 계산된 파생 지표인 `derived`로 구성됩니다.