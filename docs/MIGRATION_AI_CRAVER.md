# AI Craver 서버 마이그레이션 런북 (2026-07-27 ~ 07-30)

## ✅ 컷오버 완료 (2026-07-30)

**신규 주소: `http://10.1.100.5`** (도메인·SSL 미사용 결정 — IP+HTTP 운영)

기존 프로덕션(172.16.1.250:3000)은 **정지하지 않고** 앱 레벨 307 리다이렉트로 신규 서버로
넘긴다. 데이터가 두 DB로 갈라지는 것을 막기 위한 장치다.

| 항목 | 상태 |
|---|---|
| 최종 DB 동기화 | 7개 테이블 행수 전부 일치 (users 57 / messages 6,413 등) |
| 리다이렉트 | `/`·`/login`·`/frontend/*`·`/api/*`·`/v1/*` → 307 → 10.1.100.5 |
| 제외 경로(통과) | `/health`(watchdog), `/auth/google`·`/settings`(CRM OAuth 프록시) |
| 기존 prod | online 유지 — **롤백 대비 1주일 보존, kill/stop 금지** |

### 되돌리는 방법 (롤백)
```
# 172.16.1.250 에서
#   .env 의 MIGRATED_REDIRECT_URL 줄을 삭제(또는 빈 값)
pm2 restart skin1004-prod
```
그러면 기존 서버가 즉시 원래대로 서비스한다. 신규 서버는 그대로 둬도 무해하다.

### ⚠️ 사용자 영향: 전원 1회 재로그인
쿠키는 호스트 단위로 저장되므로 `172.16.1.250` 의 쿠키가 `10.1.100.5` 로 전달되지 않는다.
JWT 시크릿이 동일해도 쿠키 자체가 안 가므로 재로그인이 필요하다(IP 변경 이관의 불가피한 비용).
계정·비밀번호·대화 내역은 모두 보존된다.

### 구현 시 실제로 겪은 함정
`os.getenv("MIGRATED_REDIRECT_URL")` 로 읽으면 **항상 빈 값**이다. 이 프로젝트는
pydantic-settings 가 `.env` 를 직접 읽고 `os.environ` 에 넣지 않는다. 반드시
`get_settings().migrated_redirect_url` 로 접근할 것 (`app/config.py` 에 필드 추가됨).

### DB_PC(172.16.1.250) 독립성 — 실측 확인 (2026-07-30)

**이관의 핵심 목적이었던 "DB_PC가 꺼져도 서비스 유지"는 달성됐다.**

WAS 앱 프로세스의 실제 TCP 연결 상대를 `ss -tnp` 로 확인한 결과:

| 연결 상대 | 건수 | 용도 |
|---|---|---|
| 10.1.50.2 | 14 | Proxy (외부 API) |
| 10.1.200.5 | 5 | DB |
| 10.1.100.5 | 2 | Web |
| **172.16.1.250** | **0** | **의존 없음** |

(`ss` 에 잡힌 172.16.1.250 연결 1건은 관리자 SSH 세션이었다 — 앱과 무관)
호스트명도 `web-ai-01`/`was-ai-01`/`app-ai-01` 로 IT 관리 VM이다.

**DB_PC 를 꺼도 `http://10.1.100.5` 는 정상 동작한다.** 다만 아래 두 가지는 함께 멈춘다:
1. 옛 주소(172.16.1.250:3000) 리다이렉트 — 사용자가 새 주소로 정착하면 무관해짐
2. CRM(172.16.1.250:3100) — CRM 전용 서버 이관 전까지는 DB_PC 필요

### DB_PC 예약 작업 정리 (2026-07-30)

APP 서버 크론과 **중복 실행되던 2건을 비활성화**했다(삭제 아님 — 롤백 시 되살리려고).
중복 상태로 두면 AD 동기화가 하루 두 번 돌아 **잔디 알림도 두 번** 발송된다.

| 작업 | 처리 | 사유 |
|---|---|---|
| `SKIN1004-AD-Sync-Daily` | **Disabled** | APP 크론 `0 22 * * *` 와 동일 |
| `SKIN1004-Graphify-Daily` | **Disabled** | APP 크론 `0 3 * * *` 와 동일 |
| `SKIN1004-Watchdog` | 유지 | 기존 prod(리다이렉트) 감시용 |
| `SKIN1004-PM2-AutoStart` | 유지 | DB_PC 재부팅 시 PM2 기동 |
| `SKIN1004-Git-Push-Daily` | 유지 | 저장소 백업. 신규 이전 여부는 미결 |
| `SKIN1004-QA-100`, `Nightly-Debug` | 유지 | 수동/이벤트 트리거, 중복 아님 |
| `skin1004-ad-sync` | 그대로 | CRM 쪽 스크립트, 이미 Disabled |

되살리려면: `schtasks /change /tn SKIN1004-AD-Sync-Daily /enable`

### 컷오버 후 남은 일
- [ ] 사용자 공지 발송 (재로그인 안내 포함)
- [ ] 1주일 관찰 후 기존 prod 정리 여부 판단
- [ ] **GWS: GCP 콘솔 승인된 리디렉션 URI 에 아래 한 줄 추가하면 끝. 도메인 불필요.**
      ```
      http://10.1.100.5.nip.io/auth/google/callback
      ```
      `app/api/auth_routes.py:_get_redirect_uri()` 가 생 IP 를 `<IP>.nip.io` 로 자동 변환한다
      (Google 은 생 IP redirect URI 를 거부하지만 nip.io 는 정상 도메인으로 인식).
      기존 prod 도 `http://172.16.1.250.nip.io:3000/...` 로 이렇게 동작해 왔다.
      실측 확인(2026-07-30): Host `10.1.100.5` → `http://10.1.100.5.nip.io/auth/google/callback`.
      기존 항목은 롤백 대비로 남겨둘 것.
      → **따라서 도메인·SSL 요청은 불필요**(초안 `docs/IT_회신_2026-07-30_도메인SSL요청.txt` 는
        HTTPS 로 갈 경우에만 사용).
- [ ] 미커밋 변경 커밋해 배포 기준점 만들기

---


현행: 단일 Windows PC 172.16.1.250 (앱+MariaDB+배치 올인원, PM2)
목표: 5-tier 분리 — Web 10.1.100.5 / WAS 10.1.150.5 / APP 10.1.150.105 / DB 10.1.200.5 / Proxy 10.1.50.5

노션 정리: [프로젝트 서버 마이그레이션 > 2-5 체크리스트](https://app.notion.com/p/skin1004/3872b4283b0080c8838cc5f955c6f638)

## 0-0b. 개통 2차 검증 (2026-07-29 — IT 1차 적용 후 실측)

**SSH 3대 개통 확인. 남은 블로커는 Proxy 3128.**

접속: `jeffrey` / 비밀번호는 노션 AI Craver 페이지 참조. sudo는 비밀번호 입력 방식으로 가능.

| 확인 항목 | 결과 |
|---|---|
| SSH 22 — Web·WAS·APP | ✅ 접속 성공 (DB·Proxy는 설계대로 차단) |
| OS / Python | Ubuntu 24.04.4 LTS / **python3.12.3 (3.11 없음, apt 후보도 없음)** |
| sudo | 비밀번호 입력 시 가능 (NOPASSWD 아님) |
| DNS | 172.16.1.13 (AD 서버가 DNS 겸함) |
| 디스크 / 메모리 | 21G 중 20G 여유 / 2048MB — 충분 |
| WAS·APP → DB 3306 | ✅ TCP 연결 OK |
| APP → AD 389·636 | ✅ OK (WAS·Web은 차단 — 설계대로) |
| 타임존 | WAS·Web = Asia/Seoul ✅ / **APP = Etc/UTC ❌** |
| NTP | NTPSynchronized=yes 이나 **NTP service inactive** (지속 동기화 없음) |
| **Proxy 3128** | ❌ **WAS·APP·Web 3대 모두 차단** |
| 외부 도메인 17종 | ❌ 전부 실패(000) — 3128 차단으로 검증 불가 |
| Qdrant 6333 | ❌ 검증 불가 (동일 사유) |
| DB 계정 `ai` 로그인 | ❌ Access denied for 'ai'@'10.1.150.5' (TCP는 연결됨) |

**APP 타임존이 UTC인 것이 특히 중요**: AD sync 22:00·지식맵 03:00 크론이 APP에서 돌기
때문에 그대로 두면 각각 익일 07:00·12:00(KST)에 실행된다.

**uca1400 컬레이션 건**: 신규 DB가 MariaDB 10.11.14로 확인됨. uca1400 계열은 MariaDB 11.4
도입이라 10.11에서는 미지원일 가능성이 높으나, DB 로그인이 막혀 실검증은 못 했다.
미지원이어도 덤프에서 `utf8mb4_uca1400_ai_ci` → `utf8mb4_unicode_ci` 치환으로 해결 가능
(대상 9개 테이블). 이전 회신의 "MariaDB 10.10 이상이면 그대로 이관 가능"은 오류였다.

검증 스크립트: 세션 스크래치패드 `verify_all.py`, `verify_db.py` (paramiko 사용)

### 자체 조치 완료 (2026-07-29)

1. **APP 서버 타임존 교정** — `timedatectl set-timezone Asia/Seoul` 실행. 3대 모두 KST 확인.
2. **Python 3.12 채택 확정 — deadsnakes PPA 요청 철회.** 근거:
   - 3.12 휠이 없는 고정 패키지는 `pandas 2.0.1`, `pyodbc 4.0.39` 둘뿐인데
     **app/·scripts/ 어디서도 import하지 않는다**(transitive 의존일 뿐).
   - `cryptography`·`polars`·`uuid_utils`는 abi3 휠이라 3.12 호환. `protobuf`는 순수 파이썬.
   - 3.12에서 제거된 stdlib(`distutils`/`imp`/`asynchat`/`asyncore`/`smtpd`) 미사용 확인.
   - app/ 71개 파일 AST 파싱 전부 정상.
   - 단, 최종 검증은 Proxy 개통 후 실제 `pip install`로 확인 필요.
3. **DB 덤프 + 컬레이션 변환 리허설 완료**
   - 39개 테이블 중 **AI Agent 25개만 덤프**(crm_* 14개 제외 — CRM 별도 트랙), 104.1 MB
   - `utf8mb4_uca1400_ai_ci` 발견 2건(`message_feedback`, `sql_cache`) →
     `utf8mb4_unicode_ci` 치환 후 잔여 0건 확인. 변환본 생성 완료.
   - 컷오버 시 최신 덤프로 동일 절차 재실행할 것. 스크립트: 스크래치패드 `prep_dump.py`

### 🔑 Proxy 없이 구축하는 우회 경로 (2026-07-29 확립)

SSH가 열렸으므로 **Proxy 3128 없이도 WAS/APP 구축을 진행할 수 있다.** 서버 실측 상태:

| 항목 | 상태 | 대응 |
|---|---|---|
| `git` 2.43.0 | 있음 | 단 github.com 차단 → **SFTP로 코드 직접 전송** |
| `venv` 모듈 | 있음 (생성 성공) | — |
| `pip` / `ensurepip` | **없음** | 로컬에서 받은 pip 휠로 부트스트랩 |
| `gcc` / `make` | **없음** | 소스 빌드 불가 → **manylinux 휠만 사용** |
| `node` / `npm` | **없음** | PM2 불가 → **systemd 전환** (`deploy/ai-craver.service`) |
| `rsync` | 없음 | `curl`만 있음 → paramiko SFTP 사용 |
| `nginx` | 없음 | apt 필요 → **Web 서버만 Proxy 대기** |
| systemd | 255 | 정식 서비스 등록 가능 |

**오프라인 설치 절차**
1. 로컬(172.16.1.250)에서 리눅스 휠 확보 — 인터넷이 되는 쪽에서 받는다:
   ```
   pip download --dest wheels -r requirements_linux.txt \
     --platform manylinux2014_x86_64 --platform manylinux_2_17_x86_64 \
     --platform manylinux_2_28_x86_64 --python-version 3.12 --only-binary=:all:
   ```
   - `python-louvain`은 **sdist만 배포**해서 위 명령이 실패한다. 제외 후 별도로
     `pip wheel --no-deps python-louvain` (순수 파이썬이라 결과 휠은 플랫폼 무관).
   - `pip`·`setuptools`·`wheel`도 함께 받아둔다 (서버에 ensurepip이 없다).
2. SFTP로 `wheels/`와 코드·`data/` 전송
3. 서버에서:
   ```
   python3 -m venv --without-pip venv
   venv/bin/python wheels/pip-*.whl/pip install --no-index --find-links wheels pip setuptools wheel
   venv/bin/pip install --no-index --find-links wheels -r requirements_linux.txt
   ```
4. systemd 등록: `sudo cp deploy/ai-craver.service /etc/systemd/system/ && sudo systemctl enable --now ai-craver`

**여전히 Proxy가 필요한 것**: 런타임 외부 API 호출(Claude/Gemini/BigQuery/Notion/Qdrant),
Web 서버 nginx 설치(apt). 즉 **구축은 지금 진행 가능, 서비스 기동은 Proxy 개통 후.**

### ✅ WAS 구축 완료 — Proxy 없이 서비스 기동 성공 (2026-07-29)

**우회 경로가 가설이 아니라 사실로 확인됨.** 프록시·PyPI·GitHub·npm 전부 없이 완료.

| 검증 항목 | 결과 |
|---|---|
| pip 부트스트랩 (ensurepip 없음) | ✅ `python venv/bin/python <pip.whl>/pip install` 로 성공 |
| 오프라인 설치 | ✅ 휠 209개 → **패키지 206개 설치** |
| 핵심 모듈 import 22종 | ✅ 전부 성공 |
| `app.main` 로드 | ✅ **라우트 88개** |
| systemd 서비스 | ✅ `ai-craver` active + **enabled**(부팅 자동시작) |
| 포트 3000 리스닝 | ✅ `0.0.0.0:3000` |
| `/health` | ✅ **HTTP 200** |
| **Web → WAS:3000** | ✅ **HTTP 200** (이전 "판정 불가" 항목 해소) |

**실측 리소스** (2 vCPU / 2 GiB / 20 GiB 스펙 대비 — 여유 충분)
- 앱 RSS **292 MB** / 시스템 2048 MB 중 311 MB 사용, 1736 MB 여유
- 디스크 21G 중 **1.2G 사용(6%)** — venv 450 MB 포함

**설치 시 발견한 requirements 누락 (3건, 양쪽 파일에 추가 완료)**
`PyMySQL`·`DBUtils`(`app/db/mariadb.py`가 사용 — 없으면 DB 연결 불가),
`tzlocal`(APScheduler 의존 — 없으면 **앱 기동 자체가 실패**), `portalocker`(qdrant-client 의존).
`gspread`는 코드에서 미사용이라 `gspread-dataframe` 제거(pandas 의존 제거 효과).

**남은 경고 2건** (기존 프로덕션에도 동일, 동작에는 지장 없음)
- `fastapi 0.109.0` 은 `starlette<0.36` 을 요구하나 `starlette 0.52.1` 설치됨
- `google-genai 1.61.0` 은 `google-auth>=2.47` 을 요구하나 `google-auth 2.26.2` 설치됨

**남은 오류는 DB 인증뿐** — `Access denied for 'ai'@'10.1.150.5'`. IT의 계정 권한 부여 대기.

**재현 스크립트** (스크래치패드): `deploy_offline.py`(코드·휠 전송 + .env 생성),
`install_now.py`/`install2.py`(오프라인 설치), `finalize2.py`(보완설치·키전송·기동)

### ✅ DB 연결 성공 + 이전 판단 2건 정정 (2026-07-29 오후)

**DB 접속 실패는 방화벽·권한이 아니라 우리 쪽 비밀번호 오류였다.**
노션 "AI Craver" 페이지의 DB 비밀번호를 한 글자 잘못 읽어(중간에 없는 글자를 하나 더 붙여서)
계속 실패하고 있었다. **비밀번호 값은 이 저장소에 적지 말 것** — 노션 페이지를 직접 확인한다.
IT가 지적해 재확인 후 해결. 계정 권한은 처음부터 `ai`@`%` ALL PRIVILEGES 로 열려 있었다.

- WAS `.env` 의 `MARIADB_PASSWORD` 교정 → 서비스 재기동 → **Access denied 0건**, `/health` 200
- **앱이 신규 DB에 테이블 12개 자동 생성 확인** (agent_skills, wiki_* 등) — DB 계층 완전 동작

**⚠️ 정정 1 — uca1400 컬레이션은 문제가 아니었다.**
신규 DB(MariaDB 10.11.14-0ubuntu0.24.04.1)에서 `SHOW COLLATION LIKE 'utf8mb4_uca1400%'`
결과 **184종 지원**. uca1400 은 MariaDB 11.4 도입이라던 판단은 오류였고(실제로는 10.10부터),
**덤프 컬레이션 변환은 불필요**하다. 현행 덤프를 그대로 복원하면 된다.
(변환본을 만들어 둔 것은 무해하나 쓸 필요 없음)

**⚠️ 정정 2 — 신규 DB 서버 문자셋은 utf8mb4 / utf8mb4_unicode_ci 확인.** 이관 대상 DB `ai` 는
검증 시점 테이블 0개였다(빈 DB).

### ⛔ Proxy 3128 — 방화벽 드롭 확정 (2026-07-29 오후)

IT가 "Proxy 경유 접근 이력이 없다"고 회신했는데, **이력이 없는 것이 정상**이다.
패킷이 Squid에 도달조차 못 하기 때문이다. 근거:

| 테스트 | 결과 | 의미 |
|---|---|---|
| WAS·APP → 10.1.50.5 의 3128/443/8080/3129 | 전부 **TIMEOUT** | REFUSED가 아니라 무응답 = 경로상 drop |
| WAS·APP → 10.1.50.5 ICMP | 무응답 | 프록시 호스트 자체에 도달 불가 |
| 같은 WAS → 10.1.200.5:3306 | **OK** | 서버의 네트워크 스택·라우팅은 정상 |

TIMEOUT과 REFUSED의 구분이 핵심이다. REFUSED면 호스트까지는 도달한 것이라 Squid 로그에
흔적이 남을 수 있지만, TIMEOUT은 중간 방화벽에서 폐기된 것이라 로그가 남지 않는다.

### 🎉 전 구간 구축·기능 검증 완료 (2026-07-29~30)

**프록시 실주소는 `10.1.50.2:3128`** — 노션 설계서(10.1.50.5)와 IT 메일(10.5.50.2) 모두 오기였다.
이 값으로 바꾸자 외부 통신이 전부 통과했다.

| 구간 | 결과 |
|---|---|
| WAS 앱 | systemd active·enabled, `/health` 200 |
| APP 서버 | 패키지 208개, 크론 2건 등록, **AD dry-run 423명 조회** |
| Web nginx | 1.24.0, `0.0.0.0:80`, 리버스 프록시 200 |
| **Office(10.10.x.x) → Web** | ✅ 사용자 PC 브라우저 접속 확인 (2026-07-30) |
| DB 데이터 | 25테이블 복원, users 57·ad_users 476·messages 6,400 |
| 프록시 경유 외부 API | Anthropic·Gemini·BigQuery·googleapis·Notion·Qdrant 전부 통과 |

**실기능 검증** (서버 내부에서 JWT 발급해 실제 라우트 호출)
- 인증 `/api/auth/me` 200 / 대화목록 533건
- **BigQuery**: "2026년 6월 전체 매출" → 6.2초, 1,036.98억원 실제 수치 반환
- **노션(Qdrant)**: "연차 규정" → 4.9초, 실제 사내 규정 문서 반환
- **SSE 스트리밍**: 첫 청크 0.04초

Web 접근 정책은 서버 대역(172.16.1.250·WAS·APP)에서는 차단되고 Office 대역만 허용된
구성이다. 서버에서 테스트하면 실패하므로 **Web 접속 검증은 반드시 사무실 PC에서** 할 것.

### ⚠️ 컷오버 전 결정/조치 필요

1. ~~JWT_SECRET_KEY 교체~~ → **교체하지 않기로 결정 (2026-07-30 임재필 님)**.
   `config.py` 커밋된 기본값(28바이트, RFC 7518 권고 32바이트 미달)을 그대로 사용한다.
   기존 프로덕션과 동일한 시크릿이므로 컷오버 시 재로그인이 불필요하다는 이점이 있다.
   과거 결정([[project-jwt-secret-decision]])과 일관됨.
   - `ANON_SALT` 는 **정상** — `.env` 에 64바이트 고유값 설정 확인(2026-07-30). 조치 불필요.
   - 교체하면 기존 쿠키 무효화 → 컷오버 시 전원 1회 재로그인
   - 유지하면 컷오버는 매끄럽지만 공개된 시크릿 위험 잔존
   - 과거 "교체 보류" 결정 이력이 있어 임의 변경하지 않음. **컷오버 시점에 함께 교체 권장.**
2. **최종 DB 재덤프**: 현재 복원본은 07-29 시점. prod 가 계속 돌아 delta 존재
   (conversations −5, messages −11, knowledge_wiki −462). 컷오버 직전 재실행.
3. **도메인·SSL — 필수 아님으로 결정 (2026-07-30)**. IP+HTTP(`http://10.1.100.5`)로 운영한다.
   이를 위해 WAS `.env` 를 교정했다(2026-07-30 적용·검증 완료):
   - `CORS_ORIGINS` 에 `http://10.1.100.5` 추가
   - `GOOGLE_OAUTH_REDIRECT_URI` 를 `http://10.1.100.5/auth/google/callback` 로 변경
   - `COOKIE_SECURE=false` 명시 (HTTP 에서 쿠키가 설정되도록 — true 면 로그인 자체가 불가)

   ⚠️ **GWS(Gmail·Drive·캘린더)만 예외**: 위 리디렉트 URI 를 **GCP 콘솔의 승인된 리디렉션 URI**
   에도 등록해야 동작한다(사람이 콘솔에서 직접). 또한 Google 은 `http://` + 생 IP 를
   리디렉션 URI 로 거부할 수 있으므로, **GWS 를 계속 쓰려면 결국 도메인이 필요**하다.
   `data/gws_tokens/` 에 토큰 10개 → 약 10명이 사용 중. BigQuery·노션·CS 라우트는 영향 없음.

   도메인이 나오면 `deploy/nginx-ai-craver.conf`(HTTPS 버전)로 교체하고 위 3개 값을 되돌린다.
   요청 초안은 `docs/IT_회신_2026-07-30_도메인SSL요청.txt` 에 보관(미발송).
4. **이중 배포 주의**: 신규 서버는 git 저장소가 아니라 SFTP 전송본이다. 로컬 수정은
   `pm2 restart skin1004-prod`(기존)와 `python scripts/deploy_new_server.py was`(신규)를
   **둘 다** 해야 반영된다. 패키지 변경 시엔 휠 재배포가 별도로 필요.

### 자체 조치 불가 (IT 대기)
- Proxy 3128 — 3대 모두 차단. **이관 전체가 여기서 멈춤**(패키지 설치 불가)
- DB 계정 `ai` 권한 — WAS/APP 출발지 미허용
- NTP — 172.16.1.13·10.1.50.5 모두 UDP 123 무응답, 사내 NTP 서버 주소 필요

## 0-0. 개통 상태 (2026-07-28 실측)

**현재 이관 불가 — 172.16.1.250에서 신규 서버 5대 전부 네트워크 도달 안 됨.**

| 서버 | IP | ICMP | 22 | 기타 포트 |
|---|---|---|---|---|
| Web | 10.1.100.5 | ✗ | ✗ | 443 ✗ / 80 ✗ |
| WAS | 10.1.150.5 | ✗ | ✗ | 3000 ✗ |
| APP | 10.1.150.105 | ✗ | ✗ | — |
| DB | 10.1.200.5 | ✗ | ✗ | 3306 ✗ |
| Proxy | 10.1.50.5 | ✗ | ✗ | 3128 ✗ |

### IT 회신 주장별 검증 결과

| IT 주장 | 검증 방법 | 결과 |
|---|---|---|
| 3대에 172.16.1.250發 SSH·ICMP 오픈 | ping + TCP22 ×5 | ❌ **불일치 — 5대 전부 차단** |
| DB 직접접근 제한 | TCP 3306 | ➖ 차단은 맞으나 전대역 차단이라 의도된 정책인지 구분 불가 |
| Proxy는 was-ai만 통신 가능 | .250→3128 | ➖ 차단됨 (설계 의도와는 일치) |
| Google·Claude·notion·qdrant 오픈 | WAS에서 curl 필요 | ⬜ 검증 불가 (SSH 선행) |
| 전 서버 Ubuntu / 스펙 / App AD 정책 | SSH 필요 | ⬜ 검증 불가 (SSH 선행) |

즉 **IT 회신 중 로컬에서 검증 가능한 유일한 항목이 실패했고, 나머지는 그 항목이 풀려야 검증 가능**하다.

대조군(GCP 34.64.99.179:22, 로컬 3000)은 정상 연결되므로 측정 오류 아님.
10.x 대역은 기본 게이트웨이 172.16.1.1로 라우팅은 잡혀 있어 **경로 부재가 아니라 방화벽 차단**.

**추정 원인**: 설계서([노션 AI Craver](https://app.notion.com/p/39f2b4283b008005b26acedaa63e70e8))의
방화벽 표에 SSH 허용 출발지가 **"jeffrey IP"** 로 되어 있다. IT 메일은 "172.16.1.250에서 오픈"이라
했으나, 사내 임직원 접속 로그상 사무실 대역은 10.10.x.x이고 172.16.1.250은 서버 대역이므로
**사무실 PC 기준으로 등록되고 프로덕션 서버가 누락됐을 가능성**이 높다.

⚠️ **172.16.1.250 → WAS/APP SSH는 반드시 필요하다.** DB 덤프와 `data/`(노션 벡터, gws_tokens)가
모두 이 서버에 있어 이관 트래픽이 여기서 나가야 한다. 노트북에서 SSH만 되는 것으로는 부족.

### 이관 데이터 규모 (2026-07-28 실측 — IT 제공 스펙과 대조)

| 항목 | 실측 | 대상 스펙 | 판정 |
|---|---|---|---|
| prod 프로세스 RSS | 398 MB | WAS 2 GiB | ✅ 여유 있음 |
| `data/` + `knowledge_map/` | 19.4 MB | WAS 20 GiB | ✅ |
| `skin1004_ai` DB 실크기 | 190.3 MB (39 tables) | DB 30 GiB | ✅ |
| HF 모델 캐시 | 6.1 GB | — | ⛔ 복사 불필요 (아래 2단계 주석 참조) |

**결론: 현 스펙(WAS 2vCPU/2GiB/20GiB, DB 4GiB/30GiB)으로 충분하며 증설 요청 불필요.**
단 WAS 20GiB 중 Python 패키지가 3~4GB를 차지하므로 로그 로테이션은 설정할 것.

### 설계서 ↔ IT 메일 불일치 (2026-07-28)

| 항목 | 노션 설계서 | IT 메일 회신 | 확인 필요 |
|---|---|---|---|
| SSH 출발지 | jeffrey IP | 172.16.1.250 | 실제 등록된 출발지 IP |
| Proxy 아웃바운드 | Google, Claude | +Notion, Qdrant | 실제 적용된 화이트리스트 |
| WAS→Proxy 포트 | HTTPS(443) | Squid 3128 | **3128 허용 여부** (443만 열려있으면 전부 실패) |
| Proxy 사용 주체 | WAS/APP 둘 다 | was-ai만 | APP도 필요 (22:00 AD sync의 잔디 웹훅) |

설계서에서 이미 확인된 것(추가 요청 불필요): APP→AD TCP 389/636, WAS/APP→DB MySQL,
Web→WAS TCP 3000, Office→Web HTTPS.

🔐 노션 페이지에 서버 계정·DB 비밀번호가 평문으로 기재돼 있다. **이 저장소에 옮겨 적지 말 것**
(git remote로 유출됨). 접속 계정은 `jeffrey`, 비밀번호는 노션 페이지 참조.

## 0. 선행 조건 (개통 전 반드시 확정)

1. **Proxy 화이트리스트 보완** — 현재 표기는 Google·Claude(·Notion)뿐. 추가 필요:
   - `*.aws.cloud.qdrant.io:6333` (Qdrant Cloud — 포트 6333 주의) 또는 Qdrant 내부 self-host 결정
   - `api.notion.com:443` 이 방화벽 정책에 실제 반영됐는지 확인 (구성도엔 있고 표엔 없음)
   - Tavily는 미사용 레거시로 확인(2026-07-27) — 오픈 불필요. 웹검색은 Gemini Google
     Search grounding이라 Google 443에 포함됨.
   - HuggingFace: 2026-07-28 재검토 결과 **불필요** (런타임 임베딩이 Gemini API로 전환돼 있음)
   - **PyPI(`pypi.org`, `files.pythonhosted.org`)·apt(`archive.ubuntu.com`)·`github.com` 추가 필요**
     — 설계서·IT 회신 어디에도 없다. 이게 없으면 SSH가 열려도 패키지 설치가 불가해 배포 자체가 막힌다.
   - **`wh.jandi.com` 추가 필요** — `scripts/sync_ad_users.py:32,516,528,543`이 22:00 AD sync 결과를
     잔디로 알린다. 설계서·IT 회신 어디에도 없다. (미비 시 알림만 실패, 동기화는 정상)
   - **Google 실사용 도메인**(코드 grep 기준): `www.googleapis.com`, `oauth2.googleapis.com`,
     `accounts.google.com`, `gmail.googleapis.com`, `drive.google.com`, `docs.google.com`
     + SDK 내부 호출 `bigquery.googleapis.com`, `generativelanguage.googleapis.com`,
     `sheets.googleapis.com`. GWS 기능은 실사용 중(`data/gws_tokens/` 토큰 10개).
2. **Proxy 방식 확인**: NAT/라우팅이면 코드 무변경. forward proxy(squid 등)면
   `deploy/ecosystem.linux.config.js`의 `HTTPS_PROXY`/`NO_PROXY` 주석 해제 + 값 설정.
3. ~~새 서버 OS/스펙 확인 — GPU 유무~~ → **해소(2026-07-28)**: 전 서버 Ubuntu, WAS 2vCPU/2GiB.
   얼굴검색이 미사용 상태로 확인돼 로컬 torch가 불필요하므로 **GPU도 메모리 증설도 필요 없다.**
4. 사내 DNS 이름 + SSL 인증서 발급 (예: `ai.craver.local` — 확정 후 nginx/env의 도메인 교체).

## 1단계 — DB Server (10.1.200.5)

기존 MariaDB `skin1004_ai` → 신규 MySQL `ai` (DB명 변경 주의).

```bash
# 기존 Windows 서버에서 (서비스 무중단 핫 덤프)
mysqldump -u root -p --single-transaction --routines --triggers \
  --default-character-set=utf8mb4 skin1004_ai > skin1004_ai.sql

# MariaDB 전용 컬레이션이 섞여 있으면 복원 실패 가능 — 사전 점검
grep -o "utf8mb4_uca[0-9a-z_]*" skin1004_ai.sql | sort -u   # 나오면 utf8mb4_unicode_ci로 치환

# 신규 DB 서버로 복원 (DB명이 ai 로 바뀌므로 --databases 없이 덤프한 파일을 지정 DB에 복원)
mysql -h 10.1.200.5 -u ai -p ai < skin1004_ai.sql

# 검증: 테이블 수/행 수 대조
mysql -h 10.1.200.5 -u ai -p -e "SELECT table_name, table_rows FROM information_schema.tables WHERE table_schema='ai';"
```

- 컷오버 직전 최종 덤프로 한 번 더 동기화한다 (이관 기간 중 쌓인 대화/사용자 반영).
- DB 서버는 WAS·APP IP만 3306 허용, 그 외 전면 차단 (설계 준수 확인).

## 2단계 — WAS (10.1.150.5)

```bash
# 코드 배포
git clone <repo> /home/jeffrey/AI_Agent   # 또는 rsync
cd /home/jeffrey/AI_Agent

# 전용 venv (Windows에서 전역 환경 공유로 사고 이력 있음 — 반드시 venv)
python3.11 -m venv venv
# ⚠️ requirements.txt(Windows용)가 아니라 requirements_linux.txt 를 쓸 것.
#    requirements.txt 에는 pywin32·pywinpty 가 있어 Ubuntu에서 설치 실패한다.
venv/bin/pip install -r requirements_linux.txt

# 얼굴검색(face_search)은 이관 대상에서 제외 — 2026-07-28 실측 기준 미사용 상태.
#   근거: data/에 face_clip 인덱스(npy) 없음, InsightFace/CLIP 모델 캐시도 없음(~/.insightface,
#         ~/.cache/torch 모두 부재) = 현 프로덕션에서 한 번도 구동된 적 없음.
#   따라서 torch/CLIP 설치 불필요 → WAS 2GiB로 충분하며 메모리 증설 요청도 불필요.
#   추후 이 기능을 살릴 경우에만 8GiB 이상 증설 후:
#   venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
#   venv/bin/pip install insightface

# 환경 설정
cp deploy/env.was.example .env   # <...> 값 채우기 (JWT/ANON_SALT는 신규 발급)
# BigQuery 서비스 계정 키 복사 → /home/jeffrey/secrets/ (chmod 600, WAS에만 보관)

# 데이터 이전 (기존 서버에서)
#  - data/ 전체: notion 벡터 JSON, face_clip 인덱스(npy), gws_tokens/, notion_status_block_id.json
#  - prompts/ 는 repo에 포함되어 있으므로 별도 이전 불필요
rsync -av data/ jeffrey@10.1.150.5:/home/jeffrey/AI_Agent/data/

# HuggingFace 모델 캐시 — 2026-07-28 실측 결과 복사 불필요로 판단(6.1GB 절약).
#   캐시 내용: bge-m3 4.3GB + all-MiniLM-L6-v2 1.9GB
#   그러나 채팅 서비스 임베딩은 Gemini API(gemini-embedding-001)를 쓰고(app/agents/qdrant_agent.py),
#   로컬 bge-m3는 app/rag/indexer.py(레거시 BigQuery RAG), MiniLM은 qdrant_db/ 오프라인
#   스크립트에서만 사용 → WAS 런타임에 불필요.
#   레거시 인덱싱을 신서버에서 돌릴 일이 생기면 그때 복사할 것:
#   rsync -av ~/.cache/huggingface/ jeffrey@10.1.150.5:/home/jeffrey/.cache/huggingface/

# 기동
pm2 start deploy/ecosystem.linux.config.js --only skin1004-prod
pm2 save && pm2 startup   # 재부팅 자동 시작
curl -s http://localhost:3000/health   # 헬스체크 (없으면 / 로 확인)
```

- APScheduler 잡(팀리소스 01:00·위키 :15·Qdrant 05:00·품질 00:05·주간리포트 월 00:10)은
  앱 내부(app/main.py)에서 실행되므로 **WAS에서 자동으로 함께 돈다**. 별도 작업 불필요.
- 주의: Windows의 `pm2 reload` 금지 규칙은 fork 모드 고아 프로세스 문제였음. Linux에서도
  1차 이관에서는 동일하게 `pm2 restart` 사용 (검증 후 완화 검토).

## 3단계 — App Server (10.1.150.105)

```bash
# 코드 + venv 동일하게 구성 (scripts/ 실행용)
# .env는 WAS와 동일 + AD_SERVER/AD_USER/AD_PASSWORD/AD_SEARCH_BASE 추가 (LDAP은 App만)

# 크론 등록 (AD sync 22:00, knowledge map 03:00)
crontab deploy/crontab.app-server

# 검증
venv/bin/python scripts/sync_ad_users.py --dry-run   # LDAP 389/636 연결 확인
```

- Windows Task Scheduler 등록 작업 전수조사 후 누락분 이전:
  `schtasks /query /fo LIST | findstr SKIN1004` (기존 서버에서 실행).
  현재 파악: AD-Sync-Daily 22:00, Knowledge-Map 03:00, Watchdog(상시).
- watchdog(scripts/server_watchdog.py)은 WAS의 pm2를 감시하므로 **WAS에 배치**하는 게 맞다.
  systemd 서비스 또는 크론(@reboot)으로 등록. pm2 안에는 넣지 말 것 (2026-07-12 결론).

## 4단계 — Proxy 경유 검증 (10.1.50.5)

WAS에서 외부 API 도달 확인 (Proxy 경유):

```bash
curl -sS https://api.anthropic.com/v1/models -H "x-api-key: $ANTHROPIC_API_KEY" -o /dev/null -w "%{http_code}\n"
curl -sS https://api.notion.com -o /dev/null -w "%{http_code}\n"
curl -sS https://bigquery.googleapis.com -o /dev/null -w "%{http_code}\n"
curl -sS "$QDRANT_URL/collections" -H "api-key: $QDRANT_API_KEY" -o /dev/null -w "%{http_code}\n"   # 6333 오픈 확인
```

## 5단계 — Web Server (10.1.100.5)

```bash
sudo cp deploy/nginx-ai-craver.conf /etc/nginx/sites-available/ai-craver.conf
# server_name·SSL 경로 교체 후
sudo ln -s /etc/nginx/sites-available/ai-craver.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

- **SSE 스트리밍 검증 필수**: 채팅 질문 하나 보내서 토큰이 실시간으로 흘러오는지 확인.
  뭉텅이로 오면 `proxy_buffering off` 누락이다.
- HTTPS 전환에 맞춰 WAS `.env`의 `COOKIE_SECURE=true`, `CORS_ORIGINS` 확인.

## 6단계 — 컷오버

1. 새 스택 전체 기동 상태에서 QA: `scripts/qa_team_150.py`, `scripts/test_output_quality.py`
2. 최종 DB 덤프 → 재복원 (1단계 참조)
3. 사용자 공지 → 사내 DNS/안내 URL 전환
4. 기존 172.16.1.250: maintenance.html 안내로 전환(또는 새 주소 리다이렉트),
   **1주일 롤백 대기 후 정리** (skin1004-prod 즉시 삭제 금지)
5. CLAUDE.md·메모리의 배포 규칙(서버 주소·pm2 명령) 갱신

## ⚠️ CRM 연동 경로 (별도 트랙 — 이번 이관 범위 밖)

> **2026-07-28 임재필 님 확인: CRM은 IT팀에서 전용 서버를 별도 할당받아 따로 진행한다.**
> 따라서 이번 AI Craver 이관 요청에서는 CRM 관련 항목을 제외한다
> (172.16.1.250 → 신규 DB 3306 방화벽 요청도 IT 회신에서 삭제함).
> 아래 내용은 CRM 트랙 진행 시 참고용으로 남긴다.
>
> 현재 `skin1004_ai` DB에 `crm_*` 테이블 14개(23,826행 / 26.6MB)가 동거 중이나
> **AI Agent 코드는 이 테이블을 전혀 참조하지 않는다**(2026-07-28 grep 확인).
> CRM 전용 서버로 이관될 때 해당 테이블만 분리해 가면 되고, AI Agent 이관과는 독립적이다.


AI Agent 앱에 **CRM(172.16.1.250:3100)용 OAuth 콜백 프록시가 얹혀 있다.**

- `app/api/auth_api.py:481` `GET /auth/google/callback` → CRM으로 302 리디렉트
- `app/main.py:194` `GET /settings` → CRM 설정 페이지로 302 리디렉트
- 외부 도메인 `track.skin1004.app`이 이 앱(3000)으로 들어와 CRM(3100)으로 넘기는 구조

둘 다 **브라우저 리디렉트(302)** 이므로 WAS→CRM 서버측 방화벽은 불필요하다(사용자 브라우저가
직접 172.16.1.250:3100에 접속). 단 **앱이 새 서버로 옮겨가면 `track.skin1004.app`의 라우팅 대상도
함께 옮겨야 CRM OAuth 플로우가 유지된다.** 컷오버 전 IT팀과 도메인 전환 시점을 맞출 것.
CRM 자체는 기존 Windows 서버에 잔류하므로 172.16.1.250을 계속 살려둬야 한다(1주일 롤백 대기와 별개).

## 검증 체크리스트 (컷오버 전)

- [ ] 로그인 (AD 계정) / 신규 가입
- [ ] BigQuery 매출 질문 + 차트 렌더링 (chart_base_url이 https 도메인으로 잡히는지)
- [ ] 노션 트리 질문 (Qdrant 경로)
- [ ] CS / 얼굴검색 (모델 캐시 로드 확인 — 첫 요청이 외부망 다운로드를 시도하면 실패)
- [ ] SSE 스트리밍 실시간성
- [ ] GWS 기능 (OAuth 리디렉션 새 도메인)
- [ ] 22:00 AD sync 크론 로그, 03:00 knowledge map 로그
- [ ] pm2 status ↺ 카운터 안정
