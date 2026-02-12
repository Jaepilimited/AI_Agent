# SKIN1004 AI Agent - 모델 업그레이드 테스트 리포트

**일자:** 2026.02.11
**작성자:** AI Agent Dev Team
**목적:** Gemini 2.5 → Gemini 3 모델 업그레이드 영향 분석 및 최적 구성 도출

---

## 1. 배경

### 1-1. 기존 구성 (v5.1.0)

| 역할 | 모델 | Model ID |
|---|---|---|
| Search 대화 | Gemini 2.5 Pro | `gemini-2.5-pro` |
| 내부 태스크 (라우팅, SQL 생성, 차트) | Gemini 2.5 Flash | `gemini-2.5-flash` |
| Analysis 복잡 추론 | Claude Opus 4.6 | `claude-opus-4-6` |
| Analysis 경량 태스크 | Claude Sonnet 4.5 | `claude-sonnet-4-5-20250929` |

### 1-2. 업그레이드 동기

- Google Gemini 3 Pro / 3 Flash (preview) 출시
- 추론 능력 향상, 에이전틱 워크플로우 최적화 기대
- 1M 토큰 컨텍스트 윈도우

### 1-3. 사용 가능한 Gemini 모델 (2026.02 기준)

| 모델 | 상태 | 특징 |
|---|---|---|
| Gemini 3 Pro | Preview | 추론 특화, 1M 토큰 |
| Gemini 3 Flash | Preview | 멀티모달, 코딩, 고급 추론 |
| Gemini 2.5 Pro | Stable | 안정적, 검증됨 |
| Gemini 2.5 Flash | Stable | 빠름, 경량 태스크 최적 |
| Gemini 2.0 Flash | Stable | 범용 경량 |
| Gemini 2.0 Flash-Lite | Deprecated | 2026.03.31 종료 예정 |

> Gemini 3.5 Pro는 존재하지 않음. 3 Pro와 3 Flash만 preview로 제공.

---

## 2. 테스트 구성 (3단계)

### Round 1: Gemini 3 전면 적용

모든 Gemini 호출을 3 Pro / 3 Flash로 교체.

```
config.py 변경:
  gemini_model: "gemini-2.5-pro" → "gemini-3-pro-preview"
  gemini_flash_model: "gemini-2.5-flash" → "gemini-3-flash-preview"
```

| 단계 | 모델 |
|---|---|
| 라우팅 분류 | Gemini 3 Flash |
| SQL 생성 | Gemini 3 Flash |
| SQL 검증 | Gemini 3 Flash |
| 답변 포맷팅 | Gemini 3 Flash |
| 차트 생성 | Gemini 3 Flash |
| Search 최종답변 | Gemini 3 Pro |
| Analysis 최종답변 | Claude Opus 4.6 |

### Round 2: 하이브리드 구성

Flash만 2.5로 롤백, 답변 포맷팅/차트를 Pro 3로 승격.

```
config.py 변경:
  gemini_model: "gemini-3-pro-preview" (유지)
  gemini_flash_model: "gemini-3-flash-preview" → "gemini-2.5-flash" (롤백)

sql_agent.py 변경:
  format_answer(): get_flash_client() → get_llm_client(model_type)
  chart generation: get_flash_client() → get_llm_client(MODEL_GEMINI)
```

| 단계 | 모델 |
|---|---|
| 라우팅 분류 | **Gemini 2.5 Flash** (롤백) |
| SQL 생성 | **Gemini 2.5 Flash** (롤백) |
| SQL 검증 | **Gemini 2.5 Flash** (롤백) |
| 답변 포맷팅 | **Gemini 3 Pro / Claude Opus** (승격) |
| 차트 생성 | **Gemini 3 Pro** (승격) |
| Search 최종답변 | Gemini 3 Pro |
| Analysis 최종답변 | Claude Opus 4.6 |

---

## 3. 테스트 쿼리 (공통 6개)

| # | 모델 | 경로 | 질문 |
|---|---|---|---|
| 1 | Search | BigQuery | 2024년 미국 전체 매출 합계 알려줘 |
| 2 | Search | BigQuery+Chart | 2024년 분기별 미국 매출 알려줘 |
| 3 | Search | Direct | 파이썬 리스트 컴프리헨션 간단히 설명해줘 |
| 4 | Search | GWS Calendar | 이번주 일정 알려줘 |
| 5 | Analysis | BigQuery | 2024년 미국 B2B vs B2C 매출 비중 비교해줘 |
| 6 | Analysis | Direct | 안녕 넌 뭘 할 수 있어? |

---

## 4. 테스트 결과 비교

### 4-1. 응답 시간 비교 (초)

| 테스트 | 기존 (2.5 Pro+Flash) | Round 1 (3 전면) | Round 2 (하이브리드) |
|---|---:|---:|---:|
| Search BQ 전체매출 | 12.1 | **171.6** | **22.4** |
| Search BQ 분기별 | 17.8 | **64.6** | **26.5** |
| Search Direct | ~6.0 | **33.9** | **13.0** |
| Search GWS Calendar | 10.6 | 8.1 | **8.3** |
| Analysis BQ B2B/B2C | 17.6 | **39.1** | **19.1** |
| Analysis Direct | 6.5 | **47.9** | **16.5** |
| **평균** | **11.8** | **60.9** | **17.6** |

### 4-2. 정확도 비교

| 테스트 | 기존 (2.5) | Round 1 (3 전면) | Round 2 (하이브리드) |
|---|---|---|---|
| 전체 매출 합계 | 417.8억 (정확) | 271.5억 (상위20개만) | **417.8억 (정확)** |
| 분기별 매출 | 4분기 전체 | **1분기만 반환** | **4분기 전체 (정확)** |
| 리스트 컴프리헨션 | 정상 | 정상 | **정상** |
| 이번주 일정 | 정상 | 정상 | **정상** |
| B2B vs B2C | 72.1% vs 27.9% | 72.1% vs 27.9% | **72.1% vs 27.9% (정확)** |
| 자기소개 | 정상 | 정상 | **정상** |
| 차트 생성 | 정상 | 일부 생성 | **정상** |

### 4-3. 추가 문제 (Round 1에서만 발생)

| 문제 | 상세 |
|---|---|
| Gmail 타임아웃 | GWS Gmail 검색 182초 → 180초 타임아웃 초과 |
| SQL 생성 품질 저하 | "전체 매출 합계" → 상위 20개 제품별 매출로 해석 (잘못된 SQL) |
| 분기 데이터 누락 | 4분기 전체 요청 → 1분기만 반환 |
| Direct 극단적 지연 | 단순 설명 질문에 34~60초 소요 |

---

## 5. 분석

### 5-1. Gemini 3 Pro/Flash Preview의 한계

1. **속도**: Preview 모델이라 추론 최적화 미완료. 평균 응답시간이 기존 대비 **5.2배 느림**
2. **SQL 생성 품질**: Flash 3가 SQL 생성 시 질문 해석 오류 발생. "합계"를 "제품별 매출"로 해석
3. **안정성**: Gmail API 호출 시 타임아웃 발생. Flash 3의 도구 호출 처리 지연

### 5-2. 하이브리드 구성의 효과

1. **Flash 2.5 롤백 효과**: 라우팅/SQL 생성이 안정적으로 복구. SQL 정확도 100% 회복
2. **Pro 3 포맷팅 효과**: 답변 품질이 향상됨. 더 구조화된 표/분석 제공
3. **속도 최적화**: 평균 17.6초로, 기존(11.8초) 대비 약 49% 증가에 그침
4. **Analysis 경로 복구**: Opus 4.6이 Flash 2.5와 조합되면서 기존 수준(19.1초 vs 17.6초) 회복

### 5-3. 속도 증가 원인 (하이브리드 +49%)

- 답변 포맷팅에 Flash 대신 Pro 3를 사용 → 이 단계에서 약 5~10초 추가
- Pro 3 preview의 추론 시간이 2.5 Pro보다 아직 느림
- Stable 버전 출시 시 속도 개선 기대

---

## 6. 최종 구성 (확정)

| 역할 | 모델 | Model ID | 선택 이유 |
|---|---|---|---|
| 라우팅 분류 | Gemini 2.5 Flash | `gemini-2.5-flash` | 빠르고 안정적 |
| SQL 생성 | Gemini 2.5 Flash | `gemini-2.5-flash` | SQL 정확도 검증됨 |
| SQL 검증 | Gemini 2.5 Flash | `gemini-2.5-flash` | 규칙 기반, 속도 중요 |
| 답변 포맷팅 | **model_type 기반** | Pro 3 또는 Opus 4.6 | 답변 품질 향상 |
| 차트 생성 | Gemini 3 Pro | `gemini-3-pro-preview` | 차트 구성 품질 향상 |
| Search 최종답변 | Gemini 3 Pro | `gemini-3-pro-preview` | 추론 능력 향상 |
| Analysis 최종답변 | Claude Opus 4.6 | `claude-opus-4-6` | 심층 분석 최강 |
| Analysis 경량 | Claude Sonnet 4.5 | `claude-sonnet-4-5-20250929` | 빠른 Claude 응답 |

### 변경된 파일

| 파일 | 변경 내용 |
|---|---|
| `app/config.py` | `gemini_model` → `gemini-3-pro-preview`, `gemini_flash_model` → `gemini-2.5-flash` |
| `app/core/llm.py` | 독스트링 업데이트 (Gemini 3 Pro + Claude Opus 4.6/Sonnet 4.5) |
| `app/agents/sql_agent.py` | `format_answer()` → `get_llm_client(model_type)`, 차트 → `get_llm_client(MODEL_GEMINI)` |

---

## 7. 성능 요약

### 기존 vs 최종 하이브리드

| 지표 | 기존 (2.5 전체) | 최종 (하이브리드) | 변화 |
|---|---|---|---|
| 평균 응답시간 | 11.8초 | 17.6초 | +49% |
| BigQuery 정확도 | 100% | 100% | 동일 |
| 차트 생성 | 정상 | 정상 | 동일 |
| 답변 품질 | 양호 | **향상** (더 구조화된 분석) |
| GWS 안정성 | 정상 | 정상 | 동일 |

### 구성별 총평

| 구성 | 속도 | 정확도 | 품질 | 안정성 | 총평 |
|---|---|---|---|---|---|
| 2.5 Pro + Flash (기존) | ★★★★★ | ★★★★★ | ★★★★ | ★★★★★ | 빠르고 안정적 |
| 3 Pro + Flash (전면) | ★★ | ★★★ | ★★★★ | ★★★ | **Preview라 부적합** |
| **하이브리드 (최종)** | ★★★★ | ★★★★★ | ★★★★★ | ★★★★★ | **최적 균형** |

---

## 8. 향후 계획

1. **Gemini 3 Stable 출시 시**: Flash 3 stable로 전면 교체 재검토
2. **Pro 3 속도 개선 모니터링**: Preview → Stable 전환 시 포맷팅 속도 개선 기대
3. **Sonnet 4.5 활용 확대**: Analysis 경량 태스크(라우팅, 간단한 답변)에 Sonnet 적용 검토
4. **벤치마크 자동화**: 모델 변경 시 자동 비교 테스트 스크립트 구축

---

## 부록: 테스트 데이터 파일

| 파일명 | 내용 |
|---|---|
| `test_gemini3.txt` | Round 1 (Gemini 3 전면) Search 모델 테스트 |
| `test_analysis_v2.txt` | Round 1 Analysis 모델 테스트 |
| `test_gemini3_comparison.txt` | Round 1 직접 실행 테스트 |
| `test_hybrid_comparison.txt` | Round 2 (하이브리드) 최종 비교 테스트 |
