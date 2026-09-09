# Cluster 19

> Auto-generated 2026-09-09T03:00:59.448358+09:00 · Files: 2

## Purpose
이 클러스터는 SKIN1004 AI Agent 애플리케이션의 핵심 인프라 기능인 사용자 인증(Authentication) 및 프론트엔드 시각화를 위한 동적 차트 설정(Chart Configuration) 생성을 담당합니다. 사용자의 안전한 시스템 접속을 보장하고, 데이터 분석 결과를 인터랙티브한 UI 요소로 변환하는 기반을 제공합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/api/auth_api.py` — MariaDB 및 사내 AD(Active Directory) 부서/이름 정보와 연동된 회원가입, 로그인, 로그아웃 및 현재 사용자 정보 조회(me) API 엔드포인트를 제공합니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/chart.py` — 서버 사이드에서 무거운 이미지(PNG)를 생성하는 대신, 프론트엔드에서 인터랙티브하게 렌더링할 수 있도록 Chart.js 호환 설정 JSON 객체를 빌드하는 모듈입니다.

## Key Concepts
- **AD 연동 로그인 (Active Directory Integration)**: `auth_api.py`는 단순한 자체 회원 관리를 넘어, 사내 AD 시스템의 부서(Department) 및 이름(Name) 정보와 연동하여 사용자를 인증하고 MariaDB에 정보를 동기화합니다.
- **인터랙티브 차트 JSON (Interactive Chart JSON)**: `chart.py`는 서버의 리소스를 소모하여 정적 이미지를 그리는 대신, 클라이언트 사이드 렌더링에 최적화된 Chart.js 구성 데이터를 생성하여 프론트엔드에 전달합니다. 이를 통해 사용자는 웹 UI에서 차트 데이터와 동적으로 상호작용할 수 있습니다.

## How It Fits In
이 클러스터는 독자적인 유틸리티 및 API 레이어로서 다른 비즈니스 로직 클러스터들과 직접적인 의존성을 크게 가지지 않으면서도 시스템 전반의 기반을 지탱합니다. 
- **인증(Auth)** 기능은 향후 에이전트 제어 및 메가와리(Megawari) 분석 데이터 조회 등 권한이 필요한 모든 API 요청의 관문 역할을 합니다.
- **차트(Chart)** 생성 모듈은 AI Agent가 분석한 통계 데이터나 스킨1004(SKIN1004) 관련 실적 지표를 프론트엔드 대시보드에 시각화할 때 공통 유틸리티로 활용됩니다.

## Common Questions This Page Answers
- **사용자 로그인 시 사내 AD 정보가 어떻게 활용되나요?**
  - `auth_api.py`를 통해 로그인할 때, 사용자의 AD 연동 부서 및 이름 정보를 기반으로 인증이 수행되며 관련 정보가 MariaDB에 저장 및 관리됩니다.
- **차트 시각화 시 서버 부하를 줄이기 위해 어떤 방식을 사용하나요?**
  - `chart.py`를 사용하여 서버에서 직접 이미지를 렌더링하지 않고, 프론트엔드가 동적으로 그릴 수 있는 Chart.js 설정 JSON만 빠르게 생성하여 반환합니다.