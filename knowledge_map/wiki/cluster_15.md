# Cluster 15

> Auto-generated 2026-09-09T03:00:59.448358+09:00 · Files: 7

## Purpose
본 클러스터는 SKIN1004 AI Agent의 답변 신뢰성을 극대화하고 데이터 노후화를 방지하기 위한 핵심 모니터링 및 데이터 동기화 유틸리티로 구성되어 있습니다. 사용자가 오류를 발견하기 전에 시스템이 먼저 품질 저하, 미학습 데이터, 프롬프트 내 정적 값의 불일치를 감지하고 자동으로 최신 상태를 유지하는 메커니즘을 제공합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/golden_runner.py` — 배포 전 또는 매일 아침 실행되어 라우팅 오분류 및 맥락 유실 등의 회귀(Regression) 문제를 잡아내는 골든셋 회귀 러너
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/quality_monitor.py` — 최근 24시간 동안의 답변 정확도(피드백 기반), 컨텍스트 길이, 응답 속도 등 핵심 성능 지표를 일일 스냅샷으로 기록하는 모니터
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/notion_watch.py` — 노션 DB-HUB에 등록되었으나 실제 파이프라인에서 학습되지 않은 신규 자료를 선제적으로 감지하는 감시 도구
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/product_catalog.py` — 센텔라 테카 등 최신 제품 라인업이 누락되지 않도록 실제 데이터베이스를 기반으로 대표 제품 목록을 실측 생성하는 모듈
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/value_lists.py` — 메가와리(Megawari) 일정이나 국가 목록(에콰도르 등)처럼 프롬프트에 들어가는 컬럼 DISTINCT 값을 데이터에서 직접 추출하여 동기화하는 모듈
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge/trust.py` — 지식 위키(Knowledge-wiki)에서 검색된 사실(Facts)들의 신뢰 상태(Trust-state)를 평가하고 관리하는 헬퍼
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/google_workspace.py` — Gmail, Drive, Calendar API 호출을 처리하는 Google Workspace API 래퍼

## Key Concepts
- **골든셋 회귀 (Golden Set Regression)** — 단순 구조적 오류를 넘어, 실제 사용자의 질문-답변 품질이 이전보다 저하되었는지 배포 전에 검증하는 실사용 질 평가 프로세스입니다.
- **실측 기반 동기화 (Data-driven Prompting)** — 프롬프트 내에 제품 목록, 국가명, 메가와리 분기 정보 등을 수동으로 하드코딩하지 않고, 실제 DB 데이터에서 직접 추출하여 프롬프트가 낡아 발생하는 LLM의 환각(Hallucination)을 방지합니다.
- **신뢰 상태 (Trust-state)** — 어시스턴트 답변에서 마이닝된 지식 위키 정보의 안전성과 신뢰도를 등급별로 분류하여 답변 생성 시 안전하게 인용할 수 있도록 돕습니다.

## How It Fits In
이 클러스터는 시스템의 지속 가능한 운영과 품질 관리를 책임집니다.
- `app/core/golden_runner.py`는 **cluster_29**의 `concept:golden_set_regression`을 구현하여 배포 파이프라인의 안전장치 역할을 합니다.
- `app/core/quality_monitor.py`는 **cluster_03**의 `concept:performance_metrics`를 구현하여 일일 운영 지표를 시각화하고 관리자에게 공유합니다.

## Common Questions This Page Answers
- "센텔라 테카" 같은 최신 제품 정보가 누락되거나 프롬프트의 국가 목록이 실제 데이터와 불일치할 때 어떻게 해결하나요?
  - `product_catalog.py`와 `value_lists.py`를 통해 수동 관리를 배제하고 데이터베이스 실측값 기반으로 자동 갱신합니다.
- 사용자가 잘못된 답변을 받기 전에 라우팅 오류나 답변 품질 저하를 미리 예방하려면 어떻게 해야 하나요?
  - `golden_runner.py`를 통해 매일 아침 혹은 배포 전에 골든셋 회귀 테스트를 수행합니다.
- 노션에 새로 등록된 자료가 정상적으로 학습되고 있는지 어떻게 모니터링하나요?
  - `notion_watch.py`가 사람이 인지하기 전에 미학습 자료를 선제적으로 감지하여 경고합니다.