# Cluster 14

> Auto-generated 2026-09-09T03:00:59.448358+09:00 · Files: 3

## Purpose
본 클러스터는 Google Workspace(GWS) 연동을 통해 개별 사용자의 업무 생산성을 극대화하는 핵심 에이전트 기능과 브리핑 시스템을 다룹니다. 개별 사용자 단위의 OAuth2 인증을 기반으로 Gmail 및 Calendar 데이터를 안전하게 처리하고, 공유드라이브 내의 인증서류를 효율적으로 검색하는 설계를 포함합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/agents/gws_agent.py` — 개별 사용자별 OAuth2 인증, 타임아웃, 재귀 제한을 지원하는 Google Workspace 서브 에이전트 구현체입니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/personal_briefing.py` — 사용자의 로그인 시점에 제공할 Gmail 및 캘린더 메타데이터를 캐싱하고 읽기 전용으로 집계하는 개인화 브리핑 모듈입니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/superpowers/specs/2026-08-28-coa-lot-finder-design.md` — SKIN1004 업무에 필요한 공유드라이브 내 인증서류(COA·MSDS) 및 롯트(Lot) 번호 일괄 검색 기능에 대한 설계 문서입니다.

## Key Concepts
- **개별 사용자 OAuth2 (Per-user OAuth2)**: 기존 MCP 기반의 단일 사용자 접근 방식을 대체하여, 각 사용자가 자신의 구글 계정으로 개별 인증하고 권한을 부여받아 안전하게 GWS 데이터에 접근합니다.
- **개인화 브리핑 (Personal Briefing)**: 로그인 시점에 Gmail 메타데이터와 캘린더 이벤트 일정을 요약하여 제공하는 기능입니다. 보안을 위해 원본 본문은 메일 요약 시에만 일시적으로 사용되며, 캐싱된 메타데이터 위주로 안전하게 경계를 넘나듭니다.
- **COA/MSDS 롯트 일괄 찾기**: SKIN1004 원료 및 제품 관리 과정에서 구글 공유드라이브에 저장된 시험성적서(COA) 및 물질안전보건자료(MSDS)의 롯트 번호를 신속하게 일괄 검색하기 위한 기능 명세입니다.

## How It Fits In
본 클러스터는 시스템의 사용자 맞춤형 연동 및 자동화 레이어에서 중요한 역할을 합니다.
- `gws_agent.py`는 **cluster_38**의 `concept:gws_agent` 구체적 명세를 직접 구현하여, 시스템 전반에 구글 서비스 연동 기능을 제공합니다.
- `personal_briefing.py`는 **cluster_12**의 `concept:personal_briefing` 설계를 구현하여, 사용자가 시스템에 진입할 때 개인화된 대시보드 정보를 안전하고 빠르게 제공할 수 있도록 돕습니다.

## Common Questions This Page Answers
- **Q1: Google Workspace 에이전트는 다중 사용자 환경에서 보안을 어떻게 유지하나요?**
  - 단일 공용 계정 대신 개별 사용자별 OAuth2 인증 방식을 채택하여, 각 사용자가 허가한 범위 내의 Gmail 및 캘린더 데이터에만 접근할 수 있도록 격리합니다.
- **Q2: 로그인 브리핑 생성 시 이메일 본문 노출 위험은 없나요?**
  - 브리핑 시스템은 기본적으로 읽기 전용 메타데이터(제목, 시간 등)만 캐싱하여 활용하며, 상세 요약이 필요한 경우에만 제한적으로 원본 스니펫을 일시적으로 처리하므로 안전합니다.
- **Q3: 공유드라이브 내 COA/MSDS 서류 검색은 어떻게 설계되어 있나요?**
  - `2026-08-28-coa-lot-finder-design.md` 설계에 따라, 대량의 롯트 번호에 대응하는 인증서류를 구글 공유드라이브 내에서 일괄적으로 탐색하고 매칭하는 구조를 가집니다.