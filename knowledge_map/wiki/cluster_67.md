# Cluster 67

> Auto-generated 2026-07-08T03:00:19.681328+09:00 · Files: 1

## Purpose
이 클러스터는 SKIN1004 Enterprise AI 애플리케이션의 핵심 진입점(Entry Point) 역할을 합니다. FastAPI 프레임워크를 사용하여 단일 서버 환경을 구축하고, 3000번 포트를 통해 AI 백엔드 API와 커스텀 프론트엔드 서비스를 동시에 제공합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/main.py` — SKIN1004 AI Agent 애플리케이션의 FastAPI 진입점 파일로, 서버 초기화 및 백엔드/프론트엔드 통합 서빙을 담당합니다.

## Key Concepts
- **FastAPI Application Entry Point** — 전체 AI Agent 시스템을 구동하는 웹 서버 인스턴스를 생성하고 설정하는 코드입니다.
- **Single Server (Port 3000)** — 별도의 프론트엔드 서버를 분리하지 않고, 3000번 포트 하나로 AI 백엔드 기능과 커스텀 사용자 인터페이스(Frontend)를 통합하여 서비스하는 구조입니다.

## How It Fits In
이 클러스터는 SKIN1004 AI Agent 프로젝트의 런타임 호스트 역할을 합니다. 다른 클러스터에서 구현된 다양한 AI 에이전트 기능, 비즈니스 로직, 그리고 API 라우트들이 최종적으로 이 `main.py` 파일에 통합되어 하나의 웹 서비스로 구동됩니다. 비록 명시적인 다른 클러스터와의 연결 관계는 감지되지 않았으나, 시스템 전체를 실행하고 외부와 통신할 수 있게 만드는 중심축입니다.

## Common Questions This Page Answers
- SKIN1004 AI Agent 애플리케이션을 시작하는 메인 진입점 파일은 무엇인가요?
- 백엔드 API와 커스텀 프론트엔드는 어떤 포트를 통해 함께 서비스되나요?
- 프로젝트에서 사용하고 있는 웹 프레임워크는 무엇인가요?