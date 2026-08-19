# Cluster 14

> Auto-generated 2026-08-19T03:00:36.499205+09:00 · Files: 2

## Purpose
이 클러스터는 SKIN1004 Enterprise AI 에이전트 애플리케이션의 진입점(Entry Point)과 API 패키지 구조의 기반을 정의합니다. 단일 포트(Port 3000)에서 AI 백엔드 기능과 커스텀 프론트엔드를 동시에 서빙하는 FastAPI 애플리케이션을 구동하는 역할을 합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/main.py` — SKIN1004 Enterprise AI 애플리케이션의 메인 진입점 파일로, FastAPI 인스턴스를 생성하고 포트 3000에서 백엔드 API와 프론트엔드 정적 파일을 통합 서빙합니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/api/__init__.py` — `api` 디렉토리를 Python 패키지로 인식하게 하여 하위 모듈 및 라우터 임포트를 가능하게 하는 빈 초기화 파일입니다.

## Key Concepts
- **FastAPI Entry Point**: `app/main.py`는 전체 AI 에이전트 시스템의 구동 엔진 역할을 하며, 라우팅 설정 및 미들웨어, 이벤트 핸들러 등을 초기화합니다.
- **Single Server Architecture**: 별도의 웹 서버 분리 없이, 하나의 FastAPI 인스턴스가 포트 3000을 통해 AI 백엔드 API와 커스텀 프론트엔드 리소스를 모두 호스팅합니다.
- **API Package Initialization**: `app/api/__init__.py`를 통해 API 관련 비즈니스 로직과 엔드포인트 모듈들을 체계적으로 구조화하고 임포트할 수 있는 기반을 제공합니다.

## How It Fits In
이 클러스터는 SKIN1004 AI 에이전트 프로젝트의 최상위 실행 레이어입니다. 다른 클러스터에서 정의된 비즈니스 로직, 데이터베이스 모델, 에이전트 워크플로우 및 API 라우터들이 최종적으로 `app/main.py`에 등록되어 외부 클라이언트(커스텀 프론트엔드 및 API 요청자)와 통신하게 됩니다.

## Common Questions This Page Answers
- SKIN1004 AI 에이전트 서버를 시작하는 메인 진입점 파일은 어디에 있나요?
  - `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/main.py` 파일이 애플리케이션의 진입점입니다.
- 백엔드 API와 프론트엔드는 어떤 포트를 통해 서빙되나요?
  - 단일 서버 환경으로 구성되어 포트 3000을 통해 동시에 서빙됩니다.
- `app/api` 디렉토리 내부의 모듈들을 패키지 형태로 임포트하려면 어떻게 해야 하나요?
  - `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/api/__init__.py` 파일이 패키지 초기화를 담당하고 있으므로, 표준 Python 임포트 구문을 사용하여 하위 API 라우터들을 불러올 수 있습니다.