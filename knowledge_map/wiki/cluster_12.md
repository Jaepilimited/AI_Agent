# Cluster 12

> Auto-generated 2026-09-09T03:00:59.448358+09:00 · Files: 39

## Purpose
본 클러스터는 SKIN1004 AI Agent 프로젝트의 개발 역사, 릴리즈 변경 사항(Changelog), 업데이트 로그 및 주요 기능의 상세 설계 사양서(Specs)와 구현 계획서(Plans)를 포함하는 **종합 기술 문서 저장소**입니다. 시스템의 성능 최적화, 데이터 마이그레이션, 라우팅 아키텍처 개선 등 프로젝트의 진화 과정을 상세히 기록하고 있습니다.

## Key Files
- `docs/ROUTING_TRIGGERS.md` — 사용자 질문이 어떤 데이터 소스(BigQuery, Google Drive 등)로 라우팅되는지 정의한 트리거 지도
- `docs/superpowers/specs/2026-09-07-router-source-gate-design.md` — 라우터 1단계에서 외부 소스 필요 여부를 판정하는 Source Gate 설계서
- `docs/superpowers/specs/2026-04-17-integrated-ad-migration-design.md` — 마케팅 광고 데이터 테이블을 Wide 포맷에서 Long 포맷으로 전환하는 마이그레이션 설계서
- `docs/superpowers/plans/2026-04-20-bigquery-performance.md` — BigQuery 응답 속도 개선을 위한 파티셔닝 및 클러스터링 구현 계획서
- `docs/update_log_2026-02-23_cs.md` — CS Agent v1.0 출시 및 오케스트레이터 라우팅 적용 기록

## Key Concepts
- **Source Gate** — `2026-09-07-router-source-gate-design.md`에서 정의된 개념으로, LLM이 외부 데이터 소스(SQL, Drive 등)를 조회할 필요가 있는지 1차적으로 판정하여 불필요한 API 호출과 지연 시간을 줄이는 필터링 레이어입니다.
- **Wide-to-Long Migration** — `2026-04-17-integrated-ad-migration-design.md`에 기록된 설계로, 여러 매체의 광고 데이터를 효율적으로 쿼리하기 위해 테이블 구조를 정규화(Long Format)한 작업입니다.
- **Durable Answer Jobs** — `2026-07-16-durable-answer-jobs.md`에서 다루는 개념으로, 시간이 오래 걸리는 대규모 쿼리나 분석 작업을 백그라운드에서 안정적으로 처리하고 결과를 보관하는 비동기 작업 관리 시스템입니다.

## How It Fits In
본 클러스터는 프로젝트 전반의 아키텍처 변화와 기능 추가를 기록하는 허브 역할을 합니다.
- **라우팅 시스템 연계**: `2026-09-07-router-source-gate-design.md` 설계서는 **cluster_36**의 `router_source_gate` 개념을 구체적으로 구현합니다.
- **오케스트레이터 및 인증**: `update_log_2026-02-06.md` 및 `update_log_2026-02-23_cs.md`는 **cluster_38**의 `dual_llm_architecture`, `google_workspace_oauth2`, `orchestrator_routing` 아키텍처가 실제 시스템에 어떻게 반영되었는지 증명합니다.
- **프롬프트 최적화**: `update_log_2026-03-17.md`는 **cluster_02**의 `prompt_fragments` 구조를 활용하여 Enterprise Output의 품질을 높인 과정을 보여줍니다.

## Common Questions This Page Answers
- **BigQuery의 응답 속도를 개선하기 위해 어떤 전략을 사용했나요?**
  - `2026-04-20-bigquery-performance-design.md` 및 관련 계획서에 따라 파티션 필터 강제 적용, 쿼리 캐싱, 그리고 테이블 클러스터링을 통해 속도를 대폭 개선했습니다.
- **광고 데이터 테이블 구조는 어떻게 변경되었나요?**
  - `2026-04-17-integrated-ad-migration-design.md`에 따라 기존의 분산된 Wide 테이블들을 하나의 통합 Long 포맷 테이블로 마이그레이션하여 쿼리 복잡도를 낮추고 유지보수성을 확보했습니다.
- **사용자 질문이 들어왔을 때 불필요한 데이터베이스 조회를 어떻게 방지하나요?**
  - `2026-09-07-router-source-gate-design.md`에 설계된 1단계 Source Gate가 질문의 의도를 분석하여, 단순 대화나 일반 지식 질문은 외부 소스 조회 없이 즉시 답변하도록 라우팅합니다.