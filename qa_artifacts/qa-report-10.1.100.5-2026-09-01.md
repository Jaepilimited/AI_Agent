# QA Report: 비밀번호 재설정 요청 흐름

| 항목 | 결과 |
|---|---|
| 날짜 | 2026-09-01 |
| 대상 | `http://10.1.100.5` (SSH 로컬 터널 경유) |
| 브랜치 / 커밋 | `flow-canvas-team-index` / `f658673` |
| 범위 | 로그인 화면 요청 접수 → Admin AD 사용자 요청 확인 → 임시 비밀번호 발급 |
| 방식 | 운영 Edge 브라우저 점검 + 공개 API 확인 + 인증 관련 회귀 테스트 |
| 상태 | **DONE_WITH_CONCERNS** |
| 검증 가능 범위 건강 점수 | **91/100** |
| 운영 UI | `/login` 데스크톱·모바일, 요청 폼 및 오류 상태 |
| 스크린샷 | 11개 |

## 판정

운영 배포와 공개 요청 경로는 살아 있습니다. SSH 터널을 통해 `/health`는 HTTP 200, `/login`은 HTTP 200, `/`는 `/login`으로 302 응답했습니다. 데스크톱에서는 요청 폼 노출, 빈 값 검증, 존재하지 않는 이름 처리, 선택 메모 입력란, 요청 API의 계정 비노출 응답을 확인했습니다.

다만 모바일에서 요청 제출 버튼에 도달할 수 없는 **High 결함 1건**, 키보드로 요청 트리거에 접근할 수 없는 **Medium 결함 1건**을 재현했습니다. 실제 AD 테스트 사용자와 앱 관리자 세션이 없어 요청 생성부터 임시 비밀번호 발급까지의 운영 종단간 흐름은 확인하지 않았습니다.

## 요약

| 심각도 | 건수 |
|---|---:|
| Critical | 0 |
| High | 1 |
| Medium | 1 |
| Low | 0 |

### 점수 근거

| 범주 | 점수 | 근거 |
|---|---:|---|
| Console | 70 | 비인증 상태의 `/api/auth/me` 401이 매 페이지 로드마다 콘솔 오류 1건으로 기록됨 |
| Links | 100 | 확인한 URL 링크 단절 없음 |
| Visual | 100 | 데스크톱 레이아웃 이상 없음 |
| Functional | 85 | 모바일에서 요청 제출 버튼 접근 불가 |
| UX | 100 | 별도 감점 없음 |
| Performance | 100 | 확인한 운영 응답과 상호작용에서 체감 지연 없음 |
| Content | 100 | 안내·검증 문구 확인 |
| Accessibility | 92 | 요청 트리거가 키보드 포커스 순서에서 제외됨 |

가중 평균: `91.3 → 91`

## 발견된 문제

### ISSUE-001 — 모바일에서 요청 제출 버튼이 화면 밖에 고정되어 접근할 수 없음

- 심각도: **High**
- 범주: Functional
- 환경: Edge, 375×812, 모바일·터치 모드
- 재현 횟수: 2회

재현 절차:

1. `/login`을 모바일 크기로 연다.
2. “비밀번호를 잊으셨나요? 관리자에게 요청”을 누른다.
3. 폼 내부에서 아래 방향으로 스크롤한다.

실제 결과:

- 요청 버튼 위치는 `top 816.5px`, `bottom 864.5px`로 812px 뷰포트 아래에 있습니다.
- 문서 콘텐츠 높이는 923px이지만 `body`의 `overflow-y`가 `hidden`입니다.
- 스크롤 전후 `window.scrollY=0`, 버튼 위치도 그대로여서 제출 버튼에 도달할 수 없습니다.

기대 결과: 모바일에서도 요청 버튼까지 스크롤하거나 화면 안에서 누를 수 있어야 합니다.

증거: [폼을 연 직후](screenshots/prod-reset-request-mobile.png), [스크롤 시도 후](screenshots/prod-reset-request-mobile-scrolled.png)

### ISSUE-002 — “관리자에게 요청” 트리거를 키보드로 열 수 없음

- 심각도: **Medium**
- 범주: Accessibility
- 환경: Edge, 데스크톱
- 재현 횟수: 2회

재현 절차:

1. `/login`을 연다.
2. Tab 키를 반복해 모든 입력 요소와 버튼을 순회한다.
3. 요청 트리거에 포커스가 오는지 확인한다.

실제 결과:

- 트리거는 `DIV`, `role` 없음, `tabIndex=-1`, `href` 없음으로 노출됩니다.
- 12번의 Tab 이동 동안 한 번도 포커스를 받지 않았습니다.
- 마우스 클릭으로는 요청 폼이 열립니다.

기대 결과: 키보드만으로 요청 트리거에 포커스하고 실행할 수 있어야 합니다.

증거: [데스크톱 로그인 화면](screenshots/prod-reset-request-before-click.png)

## 통과한 운영 시나리오

- `/health` HTTP 200
- `/login` HTTP 200, `/` → `/login` 302
- 데스크톱에서 요청 트리거와 요청 폼 노출
- Google 본인 확인 안내, AD 이름·소속 팀 안내, 선택 메모, 요청 버튼 노출
- 이름·소속 팀 없이 요청 시 검증 문구 노출, 요청 POST 없음
- 존재하지 않는 합성 이름 검색 시 검색 API HTTP 200, 팀 선택 비활성, 요청 POST 없음
- 존재하지 않는 합성 사용자 ID 요청 API는 HTTP 200과 동일한 접수 안내를 반환해 계정 존재 여부를 노출하지 않음
- 모바일에서 가로 오버플로 없음
- 화면 상호작용 중 예외나 실패한 프런트 요청 없음. 비인증 `/api/auth/me` 401만 반복 확인됨

증거: [데스크톱 초기](screenshots/prod-login-desktop.png), [요청 폼](screenshots/prod-reset-request-after-click.png), [빈 값 검증](screenshots/prod-reset-empty-submit.png), [없는 이름 검증](screenshots/prod-reset-nonexistent-submit.png), [모바일 초기](screenshots/prod-login-mobile.png)

## 자동 회귀 테스트

```text
python -m pytest \
  tests/test_auth_login_latency.py \
  tests/test_auth_config_check.py \
  tests/test_google_auth_routes.py \
  tests/test_password_reset.py \
  tests/test_password_reset_google.py \
  tests/test_password_reset_request.py \
  tests/frontend/test_forced_password_change.py \
  tests/frontend/test_password_reset_request.py -q

81 passed, 1 warning in 13.04s
```

- 기능 전용 두 파일 별도 실행: `12 passed in 2.72s`
- 경고 1건은 테스트용 짧은 HMAC 키에 대한 라이브러리 경고이며 실패는 아닙니다.

## 콘솔 상태

- 고유 콘솔 오류: 1건
- 내용: 비인증 상태 확인용 `GET /api/auth/me`가 HTTP 401을 반환하며 브라우저가 `Failed to load resource`를 기록
- 페이지 예외: 0건
- 요청 폼 상호작용 뒤 새로 발생한 오류: 0건

## 운영에서 확인하지 못한 시나리오

1. 유효한 AD 테스트 사용자의 요청 생성과 성공 안내
2. 동일 사용자의 중복 요청이 새 행을 만들지 않는지
3. Admin → AD 사용자 탭 상단의 대기 요청 배너
4. 가입 전 사용자의 가입 안내 분기
5. 임시 비밀번호 발급과 요청 닫기의 단일 처리
6. 임시 비밀번호 로그인 후 비밀번호 변경 강제 흐름

미확인 사유는 실제 직원에게 영향을 주지 않는 AD 테스트 사용자와 앱 관리자 세션이 제공되지 않았기 때문입니다. 서버 인증값과 사용자 자격증명은 보고서·스크린샷·콘솔 출력에 기록하지 않았습니다.

## 우선 확인할 결함

1. ISSUE-001: 모바일 요청 제출 버튼 접근 불가
2. ISSUE-002: 키보드 요청 트리거 접근 불가
3. 앱 관리자 세션에서의 요청 처리 종단간 결과 미확인
