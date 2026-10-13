# Cluster 31

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 4

## Purpose
CS 운영 지표 집계와 지식베이스 데이터 누락을 사전에 방지하기 위한 카탈로그 검증 및 모니터링 계약 모듈입니다. 수동 목록 관리나 누락된 노션 문서로 인해 최신 제품 데이터(예: 센텔라 테카 등)가 에이전트에 반영되지 않는 문제를 방지하고 정합성을 유지합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/cs_metrics.py` — 국내 및 해외 CS 운영 집계 계약 인터페이스.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/notion_watch.py` — 노션 DB 연동 중 학습되지 않고 404 스킵되는 누락 자료를 사전 감지하는 감시 모듈.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/product_catalog.py` — 수기 관리 대신 실측 기반으로 최신 대표 제품 목록을 구성하는 카탈로그 모듈.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/CS_METRICS.md` — 국내·해외 CS 조회 사양 및 계약 문서.

## Key Concepts
- **CS 운영 집계 계약 (`cs_metrics.py`)**: 원본 데이터인 DB 정보 시트의 '국내 CS' 및 '해외 CS' 탭을 바탕으로 집계를 수행합니다. 해당 테이블은 기존 ETL이 갱신하며, 제품 상담 및 CS 문서는 Best Practice(BP)에서 함께 검색됩니다.
- **노션 미학습 감지 (`notion_watch.py`)**: 제품 라인업 DB 등이 등록되었으나 실제 학습되지 않고 로그상 `404스킵`만 발생하는 상태를 사용자가 인지하기 전에 조기 발견합니다.
- **실측 기반 카탈로그 (`product_catalog.py`)**: 손으로 작성한 고정 목록이 노후화되는 문제를 방지하기 위해 실측을 통해 최신 제품 목록을 생성하여 에이전트의 오답을 방지합니다.

## How It Fits In
이 클러스터는 외부 데이터 소스(구글 시트, 노션)와 AI Agent 지식베이스 간의 최신성 보장 및 CS 지표 제공을 담당합니다. 별도의 타 클러스터 직접 의존성 없이, ETL 파이프라인이 갱신한 CS 시트 및 노션 문서의 무결성을 점검하고 표준 조회 인터페이스를 제공하는 기반 역할을 수행합니다.

## Common Questions This Page Answers
- 국내/해외 CS 통계 및 운영 지표는 어떤 원본 데이터를 기반으로 집계되나요?
- 신규 등록된 노션 DB 문서가 누락되거나 학습되지 않는 문제를 어떻게 사전에 감지하나요?
- 신제품(예: 센텔라 테카 등)이 제품 목록에서 누락되지 않도록 카탈로그를 관리하는 방식은 무엇인가요?