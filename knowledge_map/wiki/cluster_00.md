# Cluster 00

> Auto-generated 2026-09-09T03:00:59.448358+09:00 · Files: 7

## Purpose
본 클러스터는 SKIN1004 AI Agent 애플리케이션의 핵심 패키지 구조를 정의하고, 시스템의 안정적인 운영을 위한 안전 장치(Safety) 메커니즘을 제공합니다. 특히 데이터베이스 상태를 주기적으로 감지하여 시스템 과부하를 방지하고, 비상 상황 시 트래픽을 제어하는 기능을 담당합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/safety.py` — 시스템의 안정성을 보장하기 위한 `MaintenanceManager` 및 `CircuitBreaker` 로직을 구현합니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/__init__.py` — `app` 디렉토리를 Python 패키지로 정의하여 모듈 임포트를 가능하게 합니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/__init__.py` — `core` 패키지 초기화 파일입니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/models/__init__.py` — `models` 패키지 초기화 파일입니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/rag/__init__.py` — RAG(Retrieval-Augmented Generation) 패키지 초기화 파일입니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/reports/__init__.py` — `reports` 패키지 초기화 파일입니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/reports/specs/__init__.py` — 리포트 명세(`specs`) 패키지 초기화 파일입니다.

## Key Concepts
- **MaintenanceManager**: 시스템 점검 상태를 관리하는 컴포넌트입니다. 관리자가 수동으로 점검 모드를 활성화(`activate`)하거나 비활성화(`deactivate`)할 수 있으며, 60초 주기로 데이터베이스 메타데이터(`__TABLES__` row_count)를 폴링하여 시스템 이상을 자동으로 감지합니다.
- **CircuitBreaker**: 외부 API 호출이나 데이터베이스 쿼리 실패율이 임계치를 초과할 경우, 추가적인 장애 확산을 막기 위해 호출을 차단하고 빠르게 에러를 반환하는 안전 패턴입니다.

## How It Fits In
이 클러스터는 SKIN1004 AI Agent의 뼈대가 되는 패키지 구조를 형성하며, 타 클러스터의 핵심 비즈니스 로직이 안전하게 실행될 수 있도록 보호막 역할을 합니다. 
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/safety.py` 파일은 **cluster_12**의 주요 개념인 `maintenance_manager`와 `circuit_breaker` 구성을 실제로 구현하여 시스템 전반의 안정성 기준을 정의합니다.

## Common Questions This Page Answers
- 시스템 점검 모드(Maintenance Mode)는 어떻게 활성화하고 자동 감지 주기는 어떻게 되나요?
- 데이터베이스 과부하를 방지하기 위해 메타데이터를 어떻게 모니터링하나요?
- 프로젝트의 주요 모듈(RAG, Reports, Models 등)을 가져오기 위한 패키지 구조는 어떻게 구성되어 있나요?