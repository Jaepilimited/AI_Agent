# Cluster 43

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 2

## Purpose
SKIN1004 Enterprise AI 프로젝트의 핵심 서버 진입점과 조직 연동을 위한 인증/디렉터리 구현 계획을 포함하는 클러스터입니다. 3000번 포트 기반의 단일 FastAPI 서버 애플리케이션 구동 및 Microsoft Entra ID 디렉터리 연동 설계를 관리합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/main.py` — SKIN1004 Enterprise AI의 FastAPI 애플리케이션 엔트리포인트로, 3000번 포트에서 AI 백엔드와 커스텀 프론트엔드를 단일 서버로 구동
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/superpowers/plans/2026-09-08-entra-directory.md` — Microsoft Entra Directory 구현 계획 문서

## Key Concepts
- **FastAPI Single Server Entrypoint**: `main.py`를 통해 AI 백엔드 서비스와 커스텀 웹 프론트엔드를 3000 포트 단일 프로세스에서 호스팅하는 실행 구조.
- **Entra Directory Integration**: SKIN1004 엔터프라이즈 환경에서 사용자 디렉터리 및 계정 관리를 수행하기 위한 Entra ID 연동 계획.

## How It Fits In
이 클러스터는 프로젝트 전체의 서버 구동 기초와 사용자 인증 레이어를 연결합니다. 특히 `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/superpowers/plans/2026-09-08-entra-directory.md` 문서는 인증 및 권한 부여를 담당하는 `concept:entra_auth`(cluster_16)의 구현 계획으로서 연결됩니다. `main.py` 엔트리포인트는 향후 Entra ID 기반 인증 미들웨어 및 라우터가 마운트되는 최상위 허브 역할을 합니다.

## Common Questions This Page Answers
- SKIN1004 AI Agent 애플리케이션의 엔트리포인트 파일 위치와 기본 서빙 포트는 무엇인가?
- AI 백엔드와 프론트엔드는 각각 다른 서버로 분리되어 구동되는가, 아니면 단일 서버로 실행되는가?
- 사내 사용자 관리 및 인증을 위한 Microsoft Entra Directory 구현 계획은 어디서 확인할 수 있는가?