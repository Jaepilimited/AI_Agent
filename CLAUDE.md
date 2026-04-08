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

## 캐시 버전

- CSS/JS 변경 시 `chat.html`의 `?v=` 번호 증가 필수
- 현재: style.css?v=134, chat.js?v=160
