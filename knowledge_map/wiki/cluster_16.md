# Cluster 16: Google Workspace 연동 및 COA·MSDS 롯트 일괄 검색

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 5

## Purpose
Google Workspace Sub Agent(`gws_agent.py`)를 통해 개별 사용자 OAuth2 인증 기반의 Google Drive/Workspace 연동을 지원합니다. 특히 SKIN1004 공유드라이브에 보관된 인증서류(COA, MSDS)를 롯트(Lot) 번호 기준으로 일괄 탐색하고 최신 파일을 선별하는 기능의 설계, 구현 계획 및 릴리즈 이력을 다룹니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/agents/gws_agent.py` — per-user OAuth2 인증, 타임아웃, recursion limit이 적용된 Google Workspace Sub Agent (v4.3)
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/superpowers/specs/2026-08-28-coa-lot-finder-design.md` — 공유드라이브 인증서류(COA·MSDS) 롯트 일괄 찾기 기능 설계 문서
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/superpowers/plans/2026-08-28-coa-lot-finder-finder.md` — COA·MSDS 롯트 일괄 검색 기능 구현 계획
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/update_log_2026-09-03.md` — 2026-08-27~09-03 업데이트 로그 (COA 파인더, 비밀번호 복구 등)
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/COA_LATEST_RELEASE_20260909.md` — COA·MSDS 최신 파일 선택 릴리즈 노트 (2026-09-09)

## Key Concepts
- **per-user OAuth2 (`gws_agent.py`)**: 단일 사용자 MCP 방식에서 벗어나, 개별 사용자가 각자의 인증 정보로 Google Workspace 자원에 접근하도록 구성된 v4.3 서브 에이전트.
- **COA·MSDS 롯트 파인더**: SKIN1004 원료 및 제품의 인증서류(COA, MSDS)를 복수 롯트 번호 기반으로 공유드라이브에서 일괄 검색하는 기능.
- **최신 파일 선택 로직**: 동일 롯트나 제품에 대해 복수의 문서가 존재할 경우 최신 버전을 선별하는 처리 규칙.
- **안정성 제어**: `gws_agent.py`의 timeout 설정 및 recursion limit을 통해 요청이 비정상 종료되거나 무한 호출에 빠지는 문제를 방지.

## How It Fits In
이 클러스터는 Google Drive 및 Google Workspace API와의 통합 레이어와 이를 응용한 도메인 서류(COA·MSDS) 탐색 파이프라인을 독립적으로 구성하고 있습니다. 현재 감지된 타 클러스터와의 직접적인 교차 의존성은 없습니다.

## Common Questions This Page Answers
- `gws_agent.py`의 인증 방식은 어떻게 동작하며 어떤 안정성 제어 장치(timeout, recursion limit)가 있는가?
- 공유드라이브 내 COA·MSDS 문서를 롯트 번호로 일괄 조회하는 시스템 설계 및 계획은 어디에 정의되어 있는가?
- 2026년 9월 초 진행된 COA 파인더 및 관련 패치 내역은 어디서 확인할 수 있는가?