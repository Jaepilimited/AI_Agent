# Cluster 15

> Auto-generated 2026-08-19T03:00:36.499205+09:00 · Files: 1

## Purpose
본 클러스터는 SKIN1004 AI Agent 시스템의 관리자 전용 API 엔드포인트를 제공합니다. MariaDB 데이터베이스를 기반으로 사용자 계정을 관리하고, 각 사용자별 LLM 모델 접근 권한을 제어하는 핵심적인 어드민 기능을 담당합니다.

## Key Files
- `app/api/admin_api.py` — MariaDB 연동을 통해 사용자 관리(User Management) 및 모델 접근 제어(Model Access Control) API 엔드포인트를 구현하는 파일입니다.

## Key Concepts
- **사용자 관리 (User Management)**: SKIN1004 AI Agent 플랫폼을 사용하는 관리자 및 일반 사용자의 계정 정보를 조회, 생성, 수정하는 기능입니다.
- **모델 접근 제어 (Model Access Control)**: 특정 사용자나 그룹이 접근할 수 있는 LLM 모델의 권한을 세부적으로 제어하여, 보안 및 리소스 사용을 최적화합니다.

## How It Fits In
본 클러스터는 시스템의 보안 및 운영 관리를 위한 중추적인 역할을 하며, 다른 주요 클러스터들과 다음과 같이 긴밀하게 연결되어 있습니다.
- **모델 접근 제어 구현 (cluster_08 연계)**: `app/api/admin_api.py`는 `concept:model_access_control`을 실제로 구현하여, 허가되지 않은 사용자가 고비용 또는 특정 LLM 모델에 접근하는 것을 방지합니다.
- **지식 공백 관리 (cluster_29 연계)**: 관리자 API는 시스템 운영 중 발생하는 `concept:knowledge_gaps`(지식 공백)를 모니터링하고 관리자가 이를 보완할 수 있도록 지원하는 엔드포인트와 연결됩니다.

## Common Questions This Page Answers
- 특정 사용자의 LLM 모델 접근 권한을 제한하거나 변경하려면 어떤 API 엔드포인트를 사용해야 하나요?
- 관리자 기능에서 사용자 계정 정보를 관리하기 위해 어떤 데이터베이스(MariaDB) 테이블과 연동되나요?