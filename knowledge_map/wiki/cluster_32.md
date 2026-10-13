# Cluster 32

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 2

## Purpose
이 클러스터는 SKIN1004 수출 물류(`Export_control.export_logistics`) 도메인의 비즈니스 계산 규칙과 데이터 품질 검증 로직을 정의합니다. 프롬프트 지시만으로는 해결되지 않는 금액 누락(무상분 누락) 및 비정상 수량 이상치 문제를 파이썬 코드로 강제하여 정확한 집계 결과를 보장합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/logistics_amount.py` — 수출 물류 금액 계산 시 유상 금액과 무상 금액을 모두 합산하도록 강제하는 모듈 (붐따 #159 대응)
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/logistics_quality.py` — 수출 물류 데이터 내 비정상 수량(억 단위 이상치 등)을 사전에 감지하고 공시하는 데이터 품질 검증 모듈

## Key Concepts
- **수출 물류 금액 (유상 + 무상)**: 단순 `amount` 컬럼만 조회할 경우 무상 물량이 집계에서 누락되는 문제가 발생합니다. 주문 금액을 정확히 산출하기 위해서는 유상 금액에 무상 금액을 합산(`유상 + 무상`)해야 합니다.
- **수량 이상치 공시 (Code-level validation)**: 프롬프트에 실측 최대치(1,332,166개) 기준을 명시하더라도 LLM이 억 단위 이상치가 포함된 답변을 그대로 반환하는 현상이 발생하여, 프로덕션 환경에서 프롬프트가 아닌 코드 레벨(`logistics_quality.py`)에서 이상치를 차단 및 공시합니다.
- **`Export_control.export_logistics`**: 수출 물류 데이터가 저장된 원천 데이터베이스/테이블 도메인입니다.

## How It Fits In
단일 클러스터 내에 독립적으로 구성되어 있으며, AI Agent가 수출 물류 관련 질의를 처리할 때 LLM의 환각이나 단순 SQL 조회의 누락을 방지하는 핵심 코어 유틸리티 역할을 수행합니다.

## Common Questions This Page Answers
- 수출 물류 주문 금액 집계 시 `amount` 컬럼 값과 실제 계산 결과가 왜 다른가요?
- 프롬프트에 이상치 가이드를 넣었는데도 왜 코드 레벨 검증(`logistics_quality.py`)이 필요한가요?
- 수출 물류 선적의 정상 실측 최대 수량 기준은 몇 개인가요?