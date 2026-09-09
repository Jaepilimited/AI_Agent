# Cluster 11

> Auto-generated 2026-09-09T03:00:59.448358+09:00 · Files: 1

## Purpose
SKIN1004 Enterprise AI 에이전트 시스템의 진입점(Entry Point) 역할을 수행하는 FastAPI 애플리케이션을 정의합니다. 단일 서버 환경(Port 3000)에서 AI 백엔드 API와 커스텀 프론트엔드 서비스를 동시에 호스팅하고 초기화하는 핵심 구동 레이어입니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/main.py` — FastAPI 애플리케이션 인스턴스를 생성하고, 라우터 등록, 미들웨어 설정, 그리고 서버 시작 시 필요한 초기화 작업을 수행하는 메인 진입점 파일입니다.

## Key Concepts
- **FastAPI Entry Point**: Port 3000을 통해 외부 요청을 수신하며, SKIN1004 AI 에이전트의 백엔드 로직과 프론트엔드 정적 파일을 함께 서빙하는 단일 서버 아키텍처의 중심입니다.
- **Unified Server**: AI 백엔드 기능과 커스텀 프론트엔드 화면을 하나의 프로세스 내에서 통합 관리하여 배포 및 운영을 단순화합니다.

## How It Fits In
이 클러스터는 시스템의 구동부로서 다른 핵심 인프라스트럭처 클러스터들과 밀접하게 연결됩니다.
- **데이터베이스 마이그레이션 연계**: `app/main.py`는 애플리케이션 시작 시 데이터베이스 스키마와 초기 데이터를 정렬하기 위해 `concept:database_migration` (cluster_16) 메커니즘을 호출하고 실행합니다.
- **구조화된 로깅 적용**: 시스템 전반의 관측 가능성(Observability)을 확보하기 위해 `concept:structured_logging` (cluster_05) 규격을 구현하여, 서버의 시작, 종료 및 런타임 에러를 표준화된 포맷으로 기록합니다.

## Common Questions This Page Answers
- SKIN1004 AI 에이전트 서버는 몇 번 포트(Port)에서 구동되나요?
- 백엔드 API와 프론트엔드 소스를 통합하여 서빙하는 메인 진입점 파일은 무엇인가요?
- 애플리케이션이 시작될 때 데이터베이스 마이그레이션과 구조화된 로깅은 어떻게 초기화되나요?