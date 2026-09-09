# Cluster 22

> Auto-generated 2026-09-09T03:00:59.448358+09:00 · Files: 1

## Purpose
본 클러스터는 SKIN1004 AI Agent 시스템의 관리자 전용 API 엔드포인트를 제공합니다. MariaDB 데이터베이스를 기반으로 시스템 관리자가 사용자 계정을 관리하고, 각 사용자의 AI 모델 접근 권한을 제어할 수 있는 핵심 기능을 담당합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/api/admin_api.py` — 사용자 관리(User Management) 및 모델 접근 제어(Model Access Control)를 처리하는 관리자 전용 API 엔드포인트 정의 파일

## Key Concepts
- **사용자 관리 (User Management)**: 시스템을 이용하는 사용자 계정의 생성, 수정, 삭제 및 권한 부여를 관리합니다.
- **모델 접근 제어 (Model Access Control)**: 특정 AI 모델(예: 거대 언어 모델 등)에 접근할 수 있는 사용자의 권한을 세부적으로 통제합니다.
- **MariaDB 연동**: 관리자 설정 및 사용자 권한 데이터를 관계형 데이터베이스인 MariaDB에 안전하게 저장하고 조회합니다.

## How It Fits In
이 클러스터는 시스템의 보안 및 운영 관리를 위한 중추적인 역할을 하며 다른 클러스터와 다음과 같이 연결됩니다:
- **Cluster 38 (Model Access Control)**: `admin_api.py`는 사용자가 AI 모델에 접근할 수 있는 권한을 검증하고 제어하는 `concept:model_access_control`을 직접 구현합니다.
- **Cluster 12 (Knowledge Gaps)**: 시스템 운영 중 발생하는 지식 공백(`concept:knowledge_gaps`) 현상을 관리자가 모니터링하고 대응할 수 있도록 지원하는 인터페이스를 구현합니다.

## Common Questions This Page Answers
- 관리자가 특정 사용자의 AI 모델 접근 권한을 어떻게 제어하고 설정하나요?
- 사용자 계정 정보와 권한 데이터는 어떤 데이터베이스(MariaDB)를 통해 관리되나요?
- 시스템 내 지식 공백(Knowledge Gaps)을 관리자가 확인하기 위한 API는 어디에 정의되어 있나요?