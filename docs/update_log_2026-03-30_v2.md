# 업데이트 로그 — 2026-03-30 (오후)

## QA 마케팅 6,500문항 100% 달성 + ChatGPT 갭 수정 + PM2 도입

### 배경
- QA v3 파이프라인 6,500문항(13테이블 x 500문항) 테스트 진행
- 리테스트 4회 iteration으로 WARN 2건 → 0건 달성
- ChatGPT 대비 기능 갭 해소 (코드블록, 대화관리, 접근성)
- 프로덕션 안정성을 위한 PM2 프로세스 매니저 도입

---

## 변경 사항

### 1. [성능] QA 마케팅 v3 — 6,500/6,500 = 100% OK

**리테스트 히스토리:**
| Iteration | 테스트 | 개선 | 잔여 WARN/FAIL |
|-----------|--------|------|----------------|
| 1 | 1,368 | 1,361 | 16 |
| 2 | 16 | 12 | 4 |
| 3 | 4 | 2 | 2 |
| 4 | 2 | 2 | **0** |

**마지막 2건 해결 내역:**

- **MC-326** (67.2s → 51.1s OK): "요즘 2025년 팀별 나라별 마캐팅 지출 상세 좀"
  - 원인: "마캐팅" 오타가 키워드 매칭 실패 → 스키마 미로딩 → SQL 생성 실패 → 웹검색 fallback (느림)
  - 수정: `_DATA_KEYWORDS`, `MARKETING_TABLES`에 "마캐팅" 오타 변형 + "지출" 키워드 추가

- **RT-168** (70.7s → 40.8s OK): "스마트스토어 Centella Ampoule 리뷰 최근 5건"
  - 원인 1: `_BIZ_CONTEXT`에 "리뷰", "스마트스토어" 미포함 → direct 라우트로 잘못 분류 → 웹검색
  - 원인 2: Smartstore product_name이 한글("센텔라 앰플")인데 영문 LIKE 사용 → 0건 반환
  - 원인 3: 프롬프트에서 `collect_date` 오류 (Smartstore는 `collected_date`)
  - 수정: 라우팅 키워드 추가 + 프롬프트에 한글 제품명 매핑 테이블 + collected_date 예시 쿼리

### 2. [성능] SQL 에이전트 속도 최적화 (`f60252e`)
- QA 60초 벽 돌파를 위한 SQL 생성/실행 파이프라인 최적화
- 스키마 병렬 로딩, 캐시 활용 강화

### 3. [기능] ChatGPT 갭 수정 — 코드블록/대화관리/접근성 (`faa2a53`, `fa644d0`)
- 코드블록 Copy 버튼 ChatGPT 동일 UX
- 아바타 표시, 대화 삭제 확인 모달
- 타이포그래피 개선, 키보드 접근성

### 4. [기능] 표/차트 개별 복사 버튼 (`34d96d6`)
- 표, 차트 영역에 개별 Copy 버튼 추가
- clipboard API + execCommand fallback 지원

### 5. [운영] PM2 프로세스 매니저 도입 (`6e77f7c`)
- 서버 크래시 시 자동 재시작
- 로그 관리 및 모니터링

### 6. [버그] QA 파이프라인 프로덕션 서버 kill 버그 수정 (`afd9057`)
- QA 테스트 실행 시 프로덕션 서버(port 3000)를 kill하는 치명적 버그 수정
- 포트 3001에서만 테스트 실행하도록 안전장치 강화

---

## 테스트 결과

| 항목 | 결과 |
|------|------|
| QA v3 전체 (6,500문항) | **100% OK** (WARN 0, FAIL 0) |
| MC-326 응답시간 | 67.2s → 51.1s |
| RT-168 응답시간 | 70.7s → 40.8s |

---

## 수정 파일

| 파일 | 변경 내용 |
|------|-----------|
| `app/agents/orchestrator.py` | _DATA_KEYWORDS에 "마캐팅","지출" 추가, _BIZ_CONTEXT에 "리뷰","평점","별점","스마트스토어","네이버스토어" 추가 |
| `app/agents/sql_agent.py` | MARKETING_TABLES 키워드에 "마캐팅" 오타 변형 추가 |
| `prompts/sql_generator.txt` | Smartstore 한글 product_name 매핑 테이블, product_name_eng 컬럼, RV4 예시 쿼리, collected_date 수정 |
| `scripts/qa_marketing/results_v3_aggregate.json` | MC-326, RT-168 → OK 업데이트 |
| `scripts/qa_marketing/retest_v3_log.json` | iteration 4 추가 (0 remaining) |
