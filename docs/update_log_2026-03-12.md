# Update Log — 2026-03-12 (v7.4.0 성능 최적화 + 인터랙티브 시각화 + Dev/Prod 분리)

## 변경 사항

### 1. 응답 속도 최적화

#### 1-A. 문제
- Direct LLM 응답 13.8초 → 불필요한 Google Search grounding 호출
- BQ 마케팅 테이블 스키마 순차 로딩 → 서버 시작 느림
- LLM 재시도 sleep 과도 (최대 16초 대기)

#### 1-B. 수정
1. **웹 검색 게이팅** (`app/agents/orchestrator.py`):
   - `_needs_web_search()` 메서드 추가 — 날씨/뉴스/환율 등 실시간 키워드만 웹검색
   - 단순 질문(10자 이하), 데이터/문서 질문은 웹검색 스킵
   - Direct LLM: 13.8s → 2.1s (84% 개선)
2. **스키마 병렬 로딩** (`app/agents/sql_agent.py`):
   - `ThreadPoolExecutor`로 마케팅 테이블 스키마 병렬 fetch
3. **재시도 sleep 캡** (`app/core/llm.py`):
   - `time.sleep(min(delay, 1))` — 최대 1초

#### 1-C. 수정 파일
| 파일 | 변경 |
|------|------|
| `app/agents/orchestrator.py` | `_needs_web_search()` 게이팅 |
| `app/agents/sql_agent.py` | 스키마 병렬 fetch, 토큰 제한 최적화 |
| `app/core/llm.py` | 재시도 sleep 1초 캡 |
| `app/main.py` | startup warmup `asyncio.gather()` 병렬화 |

### 2. 시각화 업그레이드 — Plotly PNG → Chart.js 인터랙티브

#### 2-A. 문제
- 기존 Plotly: 서버사이드 PNG 렌더링 → 느리고 정적, 확대/축소 불가

#### 2-B. 수정
1. **Chart.js 전환** (`app/core/chart.py`):
   - `build_chartjs_config()` → JSON config 반환 (프론트엔드에서 렌더링)
   - 모던 컬러 팔레트 12색, doughnut(pie 대체), gradient fill, rounded bar
   - 그룹 데이터 피벗, 시계열 정렬, Top 9 + 기타 집계
2. **프론트엔드 렌더링** (`app/frontend/chat.js`):
   - `detectAndRenderCharts()` — 테마 대응(다크/라이트), 반응형 높이
   - 애니메이션, 호버 툴팁, 범례 자동 표시
3. **소수점 제거**:
   - 툴팁: `Math.round(val).toLocaleString()`
   - Y축: 정수 포맷 + 천단위 콤마

#### 2-C. 수정 파일
| 파일 | 변경 |
|------|------|
| `app/core/chart.py` | 전면 재작성 — Chart.js config 빌더 |
| `app/frontend/chat.js` | `detectAndRenderCharts()`, 소수점 제거 |
| `app/frontend/chat.html` | Chart.js CDN 추가, cache bust v27 |
| `app/static/style.css` | 차트 컨테이너 스타일, 호버 shadow |

### 3. 월별 트렌드 시각화 강화

#### 3-A. 문제
- 월별 시계열 데이터에 bar 차트가 선택되는 경우 있음 → 트렌드 파악 어려움

#### 3-B. 수정
1. **프롬프트 강화** (`app/core/chart.py`):
   - "월별/추이/트렌드" 키워드 → 무조건 line 차트 지시
   - 시계열에 bar 사용 금지 명시
2. **코드 오버라이드** (`app/agents/sql_agent.py`):
   - 쿼리에 "월별/월간/추이/트렌드" 포함 시 LLM이 bar 선택해도 자동 line 변환
3. **멀티라인 스타일 개선** (`app/core/chart.py`):
   - tension 0.35, pointRadius 5, borderWidth 2.5
   - 그룹 top 10 제한 (가독성), x축 고유값 기준 limit 판단

#### 3-C. 수정 파일
| 파일 | 변경 |
|------|------|
| `app/core/chart.py` | 프롬프트 강화, line 스타일, 그룹 limit |
| `app/agents/sql_agent.py` | trend query → line 오버라이드 |

### 4. 그룹 시계열 피벗 테이블

#### 4-A. 문제
- "2026년 월별 몰별 매출" → 91행 중 1월 8행만 표시 (LLM 토큰 한계로 잘림)

#### 4-B. 수정
1. **`_try_pivot_timeseries()`** (`app/agents/sql_agent.py`):
   - long-format (월, 몰, 매출) → 피벗 마크다운 테이블 (몰 rows × 월 columns)
   - 자동 컬럼 감지 (시간/그룹/값), 합계 열 추가
   - 그룹 총합 기준 내림차순 정렬, top 20 제한
2. **결과**: 91행 → 18그룹 × 4기간 피벗 → LLM이 전체 데이터 표시

#### 4-C. 수정 파일
| 파일 | 변경 |
|------|------|
| `app/agents/sql_agent.py` | `_try_pivot_timeseries()` 함수 추가 |

### 5. Dev/Prod DB 분리

#### 5-A. 문제
- 개발/테스트 시 프로덕션 서버 재시작 필요 → 사용자 서비스 중단

#### 5-B. 수정
1. **Dual DB Backend** (`app/db/mariadb.py`):
   - Port 3000 (프로덕션) → MariaDB
   - Port 3001 (개발) → SQLite (`data/dev.db`)
   - 자동 `%s` → `?` placeholder 변환
   - 첫 실행 시 MariaDB → SQLite 동기화 (ad_users, users, access_groups, user_groups)
2. **코드 변경 자동 반영**: `--reload` 모드로 3000 서버 재시작 불필요

#### 5-C. 수정 파일
| 파일 | 변경 |
|------|------|
| `app/db/mariadb.py` | Dual backend (MariaDB + SQLite) |
| `.gitignore` | `data/dev.db` 추가 |

## 수정 파일 총정리

| 파일 | 수정 유형 |
|------|----------|
| `app/agents/orchestrator.py` | 웹검색 게이팅 |
| `app/agents/sql_agent.py` | 스키마 병렬화, 피벗 테이블, trend 오버라이드 |
| `app/core/chart.py` | Chart.js 전면 재작성, trend line 강화 |
| `app/core/llm.py` | 재시도 sleep 캡 |
| `app/main.py` | startup 병렬화 |
| `app/db/mariadb.py` | Dual DB backend |
| `app/frontend/chat.js` | Chart.js 렌더링, 소수점 제거 |
| `app/frontend/chat.html` | Chart.js CDN, cache bust v27 |
| `app/static/style.css` | 차트 스타일 |

## 테스트 결과

| 항목 | Before | After |
|------|--------|-------|
| Direct LLM 응답 | 13.8s | 2.1s (84%↓) |
| 시각화 방식 | Plotly PNG (정적) | Chart.js (인터랙티브) |
| "월별 몰별 매출" | 1월 8행만 표시 | 전체 18몰 × 4기간 피벗 |
| 차트 소수점 | 소수점 표시 | 정수 + 천단위 콤마 |
| 서버 재시작 | 매 업데이트마다 필요 | --reload 자동 반영 |
