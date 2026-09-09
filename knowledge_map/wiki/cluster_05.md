# Cluster 05

> Auto-generated 2026-09-09T03:00:59.448358+09:00 · Files: 17

## Purpose
SKIN1004 AI Agent 프로젝트의 핵심 인프라스트럭처, 보안 인증, 그리고 RAG(Retrieval-Augmented Generation)의 기초가 되는 코어 모듈들을 모아둔 클러스터입니다. Microsoft Entra ID 기반의 사내 계정 통합 인증, API 미들웨어, 외부 API 재시도 로직, 그리고 BigQuery 및 LLM(Gemini/Claude) 연동을 위한 공통 유틸리티를 제공합니다.

## Key Files
- `app/core/entra_auth.py` — Microsoft Entra ID(OIDC)를 활용한 사내 통합 로그인 구현
- `app/core/user_directory.py` — Entra ID로 인증된 Cella 임직원 정보 및 권한 관리
- `app/core/llm.py` — Gemini(Pro/Flash) 및 Claude Opus를 지원하는 이중 LLM 클라이언트 인터페이스
- `app/core/anonymization.py` — HMAC-SHA256 기반의 대화 및 피드백 소유자 가명화(Pseudonymization) 헬퍼
- `app/core/bigquery.py` — 쿼리 실행 및 데이터 적재를 위한 BigQuery 클라이언트
- `app/rag/chunker.py` — RAG 성능 향상을 위한 하이브리드(Semantic + Hierarchical) 청킹 모듈
- `app/rag/indexer.py` — BigQuery 벡터 인덱싱 및 검색 구현
- `app/knowledge/wiki_embed.py` — `text-embedding-004` 모델을 사용한 위키 데이터 임베딩 생성

## Key Concepts
- **Entra ID OIDC**: 기존 자체 비밀번호 저장 방식에서 탈피하여, Microsoft Entra ID를 통해 사내 계정 및 권한을 안전하게 통합 관리합니다.
- **가명화 (Pseudonymization)**: `hmac_sha256`을 이용해 사용자 ID를 결정론적으로 암호화하여, 개인정보를 보호하면서도 사용자의 이전 대화 목록을 안전하게 그룹화합니다.
- **이중 LLM (Dual LLM)**: 메인 채팅에는 Claude Opus를 사용하고, 가벼운 태스크나 특정 백그라운드 작업에는 Gemini Flash를 적절히 분배하여 효율성을 극대화합니다.
- **하이브리드 청킹 (Hybrid Chunking)**: RAG의 검색 정확도를 높이기 위해 의미론적(Semantic) 기준과 계층적(Hierarchical) 구조를 결합하여 텍스트를 분할합니다.

## How It Fits In
이 클러스터는 프로젝트 전반의 뼈대를 형성하며 다른 전문 클러스터들과 긴밀히 연결됩니다:
- **인증 및 마이그레이션**: `user_directory.py` 및 `entra_auth.py`는 사용자 인증(cluster_13)을 담당하며, `user_directory_migration.py`를 통해 데이터베이스 마이그레이션(cluster_16) 흐름과 연결됩니다.
- **데이터 및 검색**: `bigquery.py`는 BigQuery 클라이언트(cluster_06) 및 서킷 브레이커(cluster_12) 개념을 구현하고, `indexer.py`는 BigQuery 벡터 검색(cluster_38)으로 이어집니다.
- **임베딩 및 지식 저장**: `wiki_embed.py`는 Gemini 임베딩(cluster_17)을 활용하며, Notion 내보내기 계획서(`2026-09-07-notion-export-phase1.md`)는 Notion API 연동 및 내보내기 엔진(cluster_35) 설계의 기반이 됩니다.

## Common Questions This Page Answers
- **Q. 사용자의 개인정보를 보호하면서 대화 기록 세션을 유지하는 방법은 무엇인가요?**  
  A. `app/core/anonymization.py`에서 HMAC-SHA256과 솔트(Salt) 값을 조합하여 사용자 ID를 16자리 가명 ID로 결정론적으로 변환함으로써 구현합니다.
- **Q. 외부 API 호출 실패나 구글 시트 적재 오류에 어떻게 대응하나요?**  
  A. `app/core/retrying.py`에 구현된 재시도 메커니즘을 통해 일시적인 네트워크 오류나 API 타임아웃으로 인해 일배치 작업이 완전히 실패하는 것을 방지합니다.
- **Q. 사내 계정 비밀번호 분실 시 사용자가 직접 초기화할 수 있나요?**  
  A. `app/core/password_reset_google.py`를 통해 구글 계정이 연동된 사용자는 관리자 개입 없이 스스로 비밀번호를 복구할 수 있습니다.