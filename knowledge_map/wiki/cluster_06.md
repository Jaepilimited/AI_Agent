# Cluster 06

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 18

## Purpose
정형 데이터 기반의 자동화 보고서 생성 파이프라인(`app/reports/`)과 세션 컨텍스트 및 메타데이터 동기화를 지원하는 핵심 코어 유틸리티(`app/core/`)를 제공합니다. LLM의 환각(hallucination)을 최소화하기 위해 수치 계산, 발견 사실 도출, 템플릿 렌더링을 엄격한 규칙 기반 엔진으로 제어합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/reports/engine.py` — 보고서 스펙을 기반으로 데이터 조회, 품질 게이트 검증, 파생 지표 연산을 순차 실행하여 payload 생성
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/reports/planner.py` — 검증된 어휘(`semantic.vocabulary`) 내에서 보고서 블록 조합 및 계획 수립
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/reports/blocks.py` — 보고서의 최소 단위 블록 정의 및 규칙 기반 문장(finding) 기계적 추출
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/reports/registry.py` — 질문에 맞는 보고서 스펙 및 파라미터를 결정론적 규칙으로 매핑
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/reports/render.py` — 서술 속 숫자를 슬롯 형태로만 허용하여 HTML 보고서 렌더링
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/reports/specs/cost_efficiency.py` — FOC 및 바우처 비용 효율화 정적 보고서 스펙 정의
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/turn_state.py` — 대화 턴의 조회 조건을 구조체로 유지하여 멀티턴 질문 지원
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/zero_row.py` — SQL 결과가 0건일 때 어떤 필터가 원인인지 실측
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/schema_docs.py` — 노션 DB 정의서와 BigQuery 컬럼 설명 동기화
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/product_info.py` — 노션의 스킨1004 전제품 스펙 정보를 MariaDB로 동기화
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/group_alerts.py` — 권한 없는 Entra 로그인 사용자의 데이터 조회 그룹 배정 대기 알림

## Key Concepts
- **Rule-based Finding**: 보고서 본문의 해석 문장(예: 점유율, 전년비)을 LLM에 맡기지 않고 수치 기반 규칙으로 직접 생성합니다.
- **Strict Template Slots**: `render.py`에서 템플릿 내 하드코딩된 숫자를 차단하고 `lint_template()`으로 슬롯 삽입만 허용합니다.
- **Zero-row Filter Diagnostics**: 결과가 0건일 때 LLM이 원인을 자의적으로 지어내지 않도록 각 필터 조건을 분해하여 실측합니다.
- **Turn State Tracking**: 이전 SQL 텍스트 일부만 자르는 대신 정형화된 조회 상태를 유지하여 이전 턴의 맥락을 안정적으로 보존합니다.

## How It Fits In
- **Cluster 24 (`concept:access_groups`)**: `group_alerts.py`가 Entra 신규 로그인 유저의 접근 그룹 배정 상태를 감시하고 관리자 알림을 연결합니다.
- **Cluster 25 (`concept:langgraph_sql_agent`)**: `turn_state.py` 및 `graph.py`가 SQL 에이전트 그래프의 상태 전이 및 캔버스 시각화 구조를 지원합니다.
- **Cluster 07 (`concept:hallucination_prevention`)**: `external.py`의 외부 검색 맥락 분리와 `registry.py`, `render.py`의 엄격한 규칙 파이프라인이 결합되어 수치 환각을 방지합니다.

## Common Questions This Page Answers
- Q: 보고서 생성 시 수치나 요약 문장이 환각 없이 정확하게 반영되도록 보장하는 방법은 무엇인가요?
- Q: BigQuery 조회 결과가 0건일 때 에이전트가 거짓 정보를 지어내지 않도록 진단하는 파일은 무엇인가요?
- Q: 노션에 작성된 스킨1004 제품 스펙과 BigQuery 정의서는 각각 어떤 모듈을 통해 동기화되나요?