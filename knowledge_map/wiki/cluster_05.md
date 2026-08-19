# Cluster 05

> Auto-generated 2026-08-19T03:00:36.499205+09:00 · Files: 13

## Purpose
SKIN1004 AI Agent의 핵심 비즈니스 로직, 데이터 정합성 유지, 그리고 시스템의 자율적 성장과 품질 관리를 담당하는 코어 엔진 클러스터입니다. 실측 데이터 기반의 피드백 루프를 구축하고, BigQuery 스키마 변화 감지 및 전성분/모델 초상권 등 민감한 도메인 지식을 안전하게 처리하여 답변의 신뢰성을 극대화합니다.

## Key Files
- `app/agents/skill_memory.py` — 피드백을 반영하여 우수 답변(👍)은 few-shot으로, 부정 피드백(👎)은 회피 패턴으로 시스템 프롬프트에 주입하는 Hermes 스타일 스킬 메모리
- `app/core/feedback_inbox.py` — 기존에 방치되던 부정 피드백(👎) 코멘트를 수집하여 시스템 개선의 입력으로 전환하는 피드백 처리기
- `app/core/golden_runner.py` — 배포 전이나 매일 아침 라우팅 오분류 및 맥락 유실 등의 회귀(Regression)를 잡아내는 골든셋 테스트 러너
- `app/core/ingredients.py` — "나이아신아마이드 미포함 제품" 등 성분 기준 조회 오류를 방지하기 위해 스프레드시트에서 제품 전성분을 적재하고 조회하는 모듈
- `app/core/model_rights.py` — 초상권 침해로 인한 수백만 원 규모의 벌금 리스크를 방지하기 위해 모델 사진의 사용 가능 매체·지역·기간을 조회하는 모듈
- `app/core/schema_watch.py` — BigQuery 테이블 구조 변경(예: 국내/해외 리뷰 테이블 통합)을 감지하여 앱의 데이터 정합성 유실을 막는 스키마 감시 도구
- `app/core/value_lists.py` — 메가와리 기간, 국가 목록 등 프롬프트 내 하드코딩된 값이 낡아 발생하는 오답을 막기 위해 데이터베이스에서 실시간 `DISTINCT` 값을 추출하는 모듈
- `app/core/usage_meter.py` — LLM 및 BigQuery 사용량을 계측하여 운영 비용 대비 가치(ROI)를 정량적으로 증명하는 미터링 도구
- `app/core/quality_monitor.py` — 최근 24시간 동안의 라우트별 답변 정확도(👍 비율), 컨텍스트 길이, 응답 속도를 모니터링하는 도구
- `app/core/growth_report.py` — SQL 캐시 히트율, 신규 SQL 패턴, 스킬 메모리 성장 등 시스템의 자율 성장 지표를 측정하는 주간 보고서 생성기
- `app/core/response_formatter.py` — 일관된 마크다운 렌더링과 시각적 위계 확보를 위한 응답 포스트 프로세서
- `app/knowledge/wiki_communities.py` — 지식 그래프에서 Louvain 알고리즘을 통해 커뮤니티를 감지하고 지식 구조를 체계화하는 모듈
- `app/reports/store.py` — 원가, 마진 등 민감 정보가 포함된 보고서에 대해 작성자와 지정된 수신자만 접근할 수 있도록 제한하는 권한 관리 모듈

## Key Concepts
- **Hermes-style Skill Memory** — 사용자의 피드백을 기반으로 긍정 패턴은 Few-shot 예시로, 부정 패턴은 금지 규칙으로 프롬프트에 동적 주입하여 에이전트의 성능을 지속적으로 개선합니다.
- **실시간 데이터 동기화 (Value Lists & Schema Watch)** — 프롬프트 내 정적 텍스트 관리의 한계를 극복하기 위해, 실제 DB의 `DISTINCT` 값과 BigQuery 스키마 변경 사항을 실시간으로 추적하여 오답률을 낮춥니다.
- **민감 도메인 보호 (Ingredients & Model Rights)** — 전성분 매칭 오류나 모델 초상권 만료와 같이 기업에 직접적인 금전적 손실을 줄 수 있는 비즈니스 리스크를 방어합니다.

## How It Fits In
본 클러스터는 시스템의 안정성과 비용 효율성, 그리고 품질 관리를 위한 중추 역할을 합니다.
- `app/core/feedback_inbox.py`, `app/core/growth_report.py`, `app/core/response_formatter.py`는 **cluster_29**의 스키마 마이그레이션, 성장 스냅샷, 응답 포맷팅 개념을 구현하여 시스템의 구조적 진화를 돕습니다.
- `app/core/golden_runner.py`와 `app/core/usage_meter.py`는 **cluster_08**의 골든셋 회귀 테스트 및 사용량 계측 메커니즘을 구체화하여 운영 비용과 답변 품질을 동시에 통제합니다.

## Common Questions This Page Answers
- 사용자가 남긴 👎 피드백과 코멘트는 어떻게 시스템 개선에 반영되나요?
- 메가와리 일정이나 국가 목록처럼 자주 변하는 데이터를 프롬프트에서 어떻게 최신 상태로 유지하나요?
- 모델 초상권 만료나 전성분 오답으로 인한 비즈니스 리스크를 어떻게 기술적으로 방어하고 있나요?
- 에이전트 운영 비용(LLM, BigQuery)과 시스템의 자율적 성장 지표는 어떻게 측정하나요?