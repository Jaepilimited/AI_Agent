# Update Log — 2026-03-19 (v8.2 코드 정리 + 미해결 이슈 해결 + 사업부 매핑 + 스트리밍 안정화)

## 변경 사항

### 1. 코드 리뷰 & 정리 (/simplify)
- **중복 호출 제거**: `_handle_bigquery`에서 `_build_conversation_context()` 이중 호출 → 이미 전달된 `conversation_context` 재사용
- **미사용 태그 제거**: BQ 응답에 붙던 `<!-- ROUTE:BQ -->` 제거 (프론트에서 안 쓰고 기존 `<!-- source:... -->` 패턴과 충돌)
- **디버그 엔드포인트 제거**: `/debug/route` 제거 (private 메서드 노출, 인증 없음)
- **HTML 잔여물 정리**: chat.html 마키 제거 후 남은 빈 줄 정리

### 2. Notion 미해결 이슈 분석 & 해결
- **크레이버코퍼레이션 매출 조회 실패**: "크레이버코퍼레이션 연간 매출" → Brand 매핑 없어서 0건 반환
  - sql_generator.txt에 "크레이버", "크레이버코퍼레이션", "Craver" → Brand 필터 없이 전체 조회 규칙 추가
  - Admin AD 그룹별 brand_filter가 서버에서 자동 주입되는 구조 반영
- **LIN 약어 조회 실패**: "LIN 라인 SKU별 매출" → 인식 못함 vs "랩인네이처 라인" → 정상
  - 제품 라인 매핑에 "LIN" 약어 + "랩인네이처" 정확한 한국어 표기 추가
- **Notion 페이지 업데이트**: 미해결 5개 블록 삭제 → 해결 완료 토글 2개 추가

### 3. 매출 조회 기본값 변경
- **ETC 브랜드 기본 제외**: 매출 조회 시 `Brand != 'ETC'` (아워바이오) 기본 적용
- 사용자가 "ETC 포함", "아워바이오", "전 브랜드" 명시 시에만 ETC 포함

### 4. 사업부/팀 조직 구조 매핑 추가
- **사업부별 매출 조회** 지원: CASE WHEN으로 사업부 그룹핑
  - B2B 사업부: Team_NEW IN ('B2B1', 'B2B2')
  - GM 사업부 (글로벌마케팅): Team_NEW IN ('CBT', 'GM_EAST1', 'GM_EAST2', 'GM_Ecomm', 'GM_MKT', 'JBT', 'KBT')
  - PR 사업부 (브랜드커머스): Team_NEW IN ('BCM')
  - DD 사업부: Team_NEW IN ('DD_DT1', 'DD_DT2')
- "전사 실적" = 모든 팀 합산, "실적" = Sales1_R, 주문수량 = Order_Count

### 5. 스트리밍 중 대화 전환 UI 오류 수정
- **AbortController 도입**: 스트리밍 중 다른 대화 클릭 시 진행 중인 스트림 abort
- **loadConversation 안전 처리**: 스트리밍 감지 → abort → 정상 대화 로드
- **AbortError 정리**: typing indicator 제거, 에러 메시지 미표시, isStreaming 상태 초기화

### 6. QA 100 테스트 실행 (9개 테이블 × 100문항)
- **99.6% PASS** (896/900), 평균 41.8s
- Google Sheets '260319' 탭에 결과 업로드 (질문, 답변, 사용 쿼리, 상태, 응답시간)

### 7. 라우팅 키워드 보강
- orchestrator `_SKIN1004_TERMS`에 "크레이버" 추가 → BQ 라우팅 정상 인식

## 수정 파일

| 파일 | 변경 내용 |
|------|-----------|
| `app/agents/orchestrator.py` | 중복 context 호출 제거, ROUTE:BQ 태그 제거, 크레이버 키워드 추가 |
| `app/api/routes.py` | /debug/route 엔드포인트 제거 |
| `app/frontend/chat.html` | 빈 줄 정리, cache bust v105 |
| `app/frontend/chat.js` | AbortController 추가, 스트리밍 중 대화전환 안전 처리 |
| `prompts/sql_generator.txt` | 크레이버 매핑, LIN 약어, ETC 제외, 사업부 구조 매핑 |
