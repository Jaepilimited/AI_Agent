# Cluster 13

> Auto-generated 2026-09-09T03:00:59.448358+09:00 · Files: 6

## Purpose
이 클러스터는 SKIN1004 AI Agent 프로젝트에서 Microsoft Entra ID(구 Azure AD)를 활용한 사용자 인증 및 셀라(Cella) 전체 직원 디렉터리 조회 시스템의 설계와 구현 계획을 다룹니다. 또한, 공유드라이브 내 COA(시험성적서) 및 MSDS(물질안전보건자료) 롯트(Lot) 번호 일괄 검색 기능의 구현 계획과 최근 업데이트 로그를 포함하고 있습니다.

## Key Files
- `docs/ENTRA_DIRECTORY_RELEASE_20260908.md` — Entra ID 사용자 관리 전환 및 릴리스 정보
- `docs/IT_REQUEST_ENTRA_DIRECTORY_20260908.md` — 셀라 Entra 전체 직원 조회를 위한 IT 요청 및 설정 가이드
- `docs/superpowers/plans/2026-09-08-entra-directory.md` — Entra Directory 연동 기능의 단계별 구현 계획
- `docs/superpowers/specs/2026-09-08-entra-directory-design.md` — Entra 로그인 및 셀라 소유 사용자 디렉터리 아키텍처 설계서
- `docs/superpowers/plans/2026-08-28-coa-lot-finder.md` — 공유드라이브 내 COA/MSDS 롯트 일괄 찾기 기능 구현 계획
- `docs/update_log_2026-09-03.md` — COA 파인더, 비밀번호 복구, 요청 처리 개선 사항이 포함된 업데이트 로그

## Key Concepts
- **Entra Directory** — Microsoft Entra ID API를 통해 셀라(Cella) 임직원 계정 정보를 동기화하고, AI Agent 내에서 사용자 디렉터리를 조회 및 관리하는 체계입니다.
- **COA/MSDS Lot Finder** — 제품의 롯트(Lot) 번호를 기반으로 구글 공유드라이브에 저장된 COA 및 MSDS 문서를 자동으로 탐색하고 매칭해 주는 기능입니다.
- **Silent Failure Prevention** — 에이전트 요청이 아무런 응답 없이 종료되는 현상(조용히 죽던 요청)을 개선하여 시스템 안정성을 높이는 예외 처리 메커니즘입니다.

## How It Fits In
이 클러스터는 사용자 인증 및 조직도 데이터를 관리하는 핵심 보안/인프라 레이어에 해당합니다. 
- `docs/ENTRA_DIRECTORY_RELEASE_20260908.md` 파일은 데이터베이스 연결 효율화를 위해 **cluster_08**의 `concept:mariadb_connection_pool`을 참조하여 설계되었습니다. Entra ID를 통해 인증된 사용자 정보는 MariaDB 커넥션 풀을 거쳐 안전하고 신속하게 데이터베이스에 반영됩니다.

## Common Questions This Page Answers
- 셀라(Cella) 전체 직원 정보를 Microsoft Entra ID와 어떻게 연동하고 동기화하나요?
- 구글 공유드라이브에서 특정 제품의 COA 및 MSDS 롯트 번호를 일괄적으로 검색하는 로직은 어떻게 설계되어 있나요?
- Entra ID 로그인 전환 과정에서 필요한 IT 설정 및 권한 요구사항은 무엇인가요?