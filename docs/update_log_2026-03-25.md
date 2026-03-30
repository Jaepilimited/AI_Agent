# Update Log — 2026-03-25 (스트리밍 개선 + 보안 + UX + 로드맵)

## 변경 사항

### 1. 스트리밍 개선
- Claude thinking 패턴 프론트에서 필터링 (The user/I should/Let me 등)
- CS/Notion/GWS/Multi 라우트도 스트리밍 적용 (25자 청크 + 35ms 딜레이)
- 스트리밍 렌더링: 300ms throttle 마크다운 렌더링으로 안정화
- 벤치마크: Direct TTFB 1.9s, BQ 15.4s, CS/Notion fake 스트리밍 적용

### 2. ChatGPT급 UX (이전 커밋 포함)
- 스트리밍 커서: 블링킹 오렌지 캐럿
- Stop 버튼: 스트리밍 중 빨간 정지 버튼
- 라우트별 로딩 메시지: "📊 데이터 조회 중...", "📋 Notion 검색 중..."
- 메시지 복사 버튼: hover 시 전체 메시지 복사
- 에러 카드: "다시 시도" 버튼
- favicon: SKIN1004 공식 로고

### 3. 보안 수정
- GWS 기본 이메일 폴백 제거 — 타인 메일 접근 차단
- GWS 미연결 시 Google OAuth 팝업 자동 오픈
- 보안팀 회신 문서 작성 (LLM 데이터 처리 메커니즘 상세)

### 4. 라우팅 개선
- 외부 주제(부동산/주식) BQ 라우팅 방지
- 리뷰 테이블 컬럼명 수정 (Smartstore: collected_date)
- AD 동기화 이름 예외 처리 (배서진→배진서)

### 5. 백엔드 속도 최적화
- SQL 프롬프트 캐싱 (76KB 매번 읽기 → 1회 캐시)
- 차트 병렬 생성 (답변 스트리밍과 동시 실행)
- brand_filter DB 통합 쿼리 (2회→1회)

### 6. 리더미팅 프레젠테이션
- HTML 15슬라이드 + PPTX 파일 제작
- 발표자: 이형섭 (Chris Lee) | DB | 2026년 4월 8일
- URL 공유: /static/presentation.html, /static/roadmap.html

### 7. 프로젝트 로드맵 (간트 차트)
- AI & CRM 프로젝트 4~6월 간트 차트
- 실시간 편집 기능: 행 추가/삭제, 바 드래그 리사이즈, 색상 변경, 순서 이동
- 서버 실시간 저장 (POST /api/save-roadmap)
