# Cluster 06

> Auto-generated 2026-09-09T03:00:59.448358+09:00 · Files: 24

## Purpose
본 클러스터는 SKIN1004 AI Agent 프로젝트의 핵심 비즈니스 로직을 지탱하는 **보고서 생성 엔진(Dynamic/Fixed Report Pipeline)**과 **코어 데이터 정합성 및 보안 감시 체계**를 다룹니다. 사용자의 질문 의도를 정밀하게 판정하여 동적으로 보고서를 조립·렌더링하고, BigQuery 스키마 변화나 광고 매체 누락 등 데이터 파이프라인의 실시간 이상 징후를 감지하여 시스템의 신뢰성을 보장합니다.

## Key Files
- `app/reports/service.py` — 질문 분석부터 캐시 확인, 병렬 조회, 품질 게이트 검증, 렌더링 및 요약까지 보고서 생성 전 과정을 관장하는 진입점
- `app/reports/planner.py` — 검증된 어휘만을 사용하여 질문에 적합한 보고서 블록, 지표, 축의 조합 계획을 수립하는 플래너
- `app/reports/engine.py` — 정의된 스펙에 따라 데이터 조회, 품질 게이트 검증, 파생 지표 계산을 순차적으로 수행하는 실행 엔진
- `app/reports/render.py` — 하드코딩된 숫자 리터럴을 배제하고 오직 슬롯 기반으로만 서술형 템플릿을 HTML로 안전하게 변환하는 렌더러
- `app/core/schema_watch.py` — BigQuery 테이블 구조 및 스키마 변경 사항을 실시간 감지하여 앱의 데이터 인지 공백을 방지하는 감시 모듈
- `app/core/ad_media_watch.py` — 특정 광고 매체 데이터가 통째로 유실되거나 누락되는 현상을 실시간으로 추적하는 모듈
- `app/core/failure_learning.py` — 사용자 피드백(붐따) 중 해결된 실패 사례를 골든셋에 반영하여 회귀를 방지하는 학습 루프
- `app/core/turn_state.py` — 단순 텍스트 잘림 문제를 해결하기 위해 대화 턴 간의 조회 상태 구조를 유지하는 상태 관리 모듈

## Key Concepts
- **동적 조립 (Dynamic Assembly)** — 고정된 템플릿 대신 사용자의 질문 의도(`intent.py`)에 맞춰 필요한 분석 블록들을 유연하게 조합하여 보고서를 구성하는 방식입니다.
- **판정 계층 (Judgment Layer)** — 단순히 표와 차트를 나열하는 것을 넘어, 각 장마다 명확한 결론(KEY MESSAGE)을 도출하여 의사결정을 돕는 구조입니다 (`judge.py`).
- **스키마 동기화 (Schema Synchronization)** — 노션의 BigQuery 데이터베이스 정의서와 실제 BigQuery 컬럼 설명을 동기화하여 앱에 데이터 의미를 전달합니다 (`schema_docs.py`).

## How It Fits In
이 클러스터는 AI Agent의 신뢰성과 비즈니스 분석 능력을 극대화하는 중추 역할을 합니다.
- `app/core/failure_learning.py`는 **Cluster 29**의 `concept:golden_set_regression`을 구현하여 시스템의 지속적인 품질 향상을 보장합니다.
- `app/core/security.py`는 **Cluster 38**의 `concept:text_to_sql` 안전성 검증을 수행하여 악의적인 쿼리 실행을 차단합니다.
- `app/reports/engine.py` 및 `specs/cost_efficiency.py`는 **Cluster 23**의 `concept:quality_gate`를 통과한 데이터만 보고서에 반영하도록 강제합니다.
- `app/reports/insight.py`는 **Cluster 30**의 `concept:insight_generation`을 활용하여 정량적 지표 외에 LLM이 유일하게 정성적 Action Item을 작성할 수 있도록 지원합니다.

## Common Questions This Page Answers
- **Q. 보고서 생성 시 LLM의 환각(Hallucination)으로 인한 숫자 왜곡을 어떻게 방지하나요?**
  - A. `planner.py`와 `registry.py`에서 LLM의 역할을 최소화하고 검증된 어휘 조합만 허용하며, `render.py`를 통해 모든 서술 속 숫자는 템플릿 슬롯(`{{ derived... }}`)으로만 주입되도록 강제합니다.
- **Q. 민감한 원가나 마진 정보가 포함된 보고서의 보안은 어떻게 유지되나요?**
  - A. `store.py`를 통해 보고서를 생성한 사람과 명시적으로 지목된 사람만 열람할 수 있도록 권한을 제한하며, 어드민 계정이라도 예외 없이 접근이 통제됩니다.