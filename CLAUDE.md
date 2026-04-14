# SKIN1004 AI Agent — 개발 규칙

## 배포 규칙 (최우선)

- **3000 = 프로덕션 (skin1004-prod)**: 사용자가 사용 중. 직접 수정/reload/restart 절대 금지
- **3001 = 개발 (skin1004-dev)**: 모든 코드 변경은 여기서만 테스트
- **배포 흐름**: 코드 수정 → `pm2 restart skin1004-dev` → 3001에서 검증 → 주인님 확인 후 `pm2 reload skin1004-prod`
- 프로덕션 반영은 반드시 주인님의 명시적 허락 후에만 실행
- `pm2 reload` 사용 (restart 아님 — 무중단 반영)
- 프로덕션 서버 kill, stop, delete 절대 금지

## 서버 관리

- PM2: `ecosystem.config.js` (windowsHide: true)
- 프로덕션: `pm2 reload skin1004-prod` (주인님 허락 후)
- 개발: `pm2 restart skin1004-dev`
- 상태 확인: `pm2 status`
- 로그: `pm2 logs skin1004-prod --lines 30 --nostream`

## 노션 데이터 규칙

- 사용자가 **노션을 명시적으로 언급하지 않는 한** 노션 데이터를 답변에 포함하지 않음
- 노션 데이터는 **노션 트리 기능**(채팅, System Status, @@ 데이터소스 선택)에서만 활용
- BigQuery 질문에 노션 데이터를 섞지 않을 것

## 메가와리 기간 (큐텐 Qoo10 전용)

- **2023년**: Q1(3/1~3/12), Q2(6/1~6/12), Q3(9/1~9/12), Q4(11/22~12/3)
- **2024년**: Q1(3/1~3/12), Q2(6/1~6/12), Q3(8/31~9/12), Q4(11/15~11/27)
- **2025년**: Q1(2/28~3/12), Q2(5/31~6/12), Q3(8/31~9/12), Q4(11/21~12/3)
- **2026년**: Q1(2/27~3/11)
- 메가와리 질문 시 `Mall_Classification LIKE '%Q10%'` 필터 필수

## 캐시 버전

- CSS/JS 변경 시 `chat.html`의 `?v=` 번호 증가 필수
- 현재: style.css?v=139, chat.js?v=202
