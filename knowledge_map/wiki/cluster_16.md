# Cluster 16

> Auto-generated 2026-09-09T03:00:59.448358+09:00 · Files: 5

## Purpose
본 클러스터는 SKIN1004 AI Agent의 **출근 브리핑(Personal Briefing)** 생성 및 전달, 그리고 이에 필요한 **근무일 판정 및 환율 계산**을 담당하는 핵심 비즈니스 로직 모듈로 구성되어 있습니다. 외부 네트워크가 차단된 WAS 환경의 제약을 극복하기 위해 DB_PC와의 릴레이 및 대기열 구조를 활용하여 잔디(Jandi) 메시지 발송과 환율 조회를 안전하게 수행합니다.

## Key Files
- `app/core/fx_rates.py` — DB_PC가 수집하여 저장한 오늘의 환율 데이터를 브리핑 모듈이 읽을 수 있도록 제공하는 환율 관리 모듈
- `app/core/jandi_briefing.py` — 프록시 제약으로 직접 발송이 불가능한 환경에서, 사용자별 웹훅과 발송 대기열을 통해 잔디로 브리핑을 전달하는 모듈
- `app/core/logistics_fx.py` — BigQuery의 사내 환율표 데이터를 기반으로 수출 물류 금액을 한화(KRW)로 환산하고 적용된 월말환율 정보를 명시하는 모듈
- `app/core/personal_briefing_store.py` — MariaDB를 기반으로 사용자별 브리핑 스냅샷 데이터를 저장하고 관리하는 데이터 스토어
- `app/core/workday.py` — 구글 '대한민국 공휴일' 캘린더 API 등을 활용하여 정확한 근무일을 판정하고 브리핑 대상 메일 조회 구간을 결정하는 모듈

## Key Concepts
- **환율 릴레이 (FX Relay)** — WAS 서버가 외부 환율 API에 직접 접근할 수 없으므로, 외부 통신이 가능한 DB_PC가 환율을 수집하여 서버에 전달하고 이를 활용하는 방식입니다.
- **잔디 대기열 (Jandi Queue)** — WAS에서 `wh.jandi.com`으로의 직접 아웃바운드 통신이 차단되어 있기 때문에, SSH 터널링이 열려 있는 DB_PC(172.16.1.250)를 경유하여 잔디 웹훅을 안전하게 릴레이 발송하는 구조입니다.
- **정확한 근무일 판정** — 하드코딩된 공휴일 목록 대신 구글 공식 대한민국 공휴일 데이터를 참조하여, 메가와리(Megawari) 기간 등 민감한 시기에 LLM이나 시스템이 잘못된 날짜 구간의 메일 및 데이터를 조회하지 않도록 방지합니다.

## How It Fits In
- **Cluster 34 (`concept:jandi_webhook`)** — `app/core/jandi_briefing.py`는 잔디 웹훅 송신 표준 규격을 구현하여 사내 협업 툴로 브리핑을 안전하게 전달합니다.
- **Cluster 03 (`concept:personal_briefing_store`)** — `app/core/personal_briefing_store.py`는 수집 및 정제된 개인화 브리핑 데이터를 MariaDB 스토어에 안전하게 적재하여 로그인 시 즉시 노출될 수 있도록 지원합니다.

## Common Questions This Page Answers
- **Q. 수출 자료 다운로드 시 한화 환산이 안 되거나 환율 정보가 누락되면 어떻게 하나요?**  
  A. `app/core/logistics_fx.py`를 통해 BigQuery에 적재된 사내 월말환율 데이터를 조회하여 한화로 변환하며, 계산 시 어떤 환율 기준을 사용했는지 사용자에게 명확히 안내합니다.
- **Q. WAS 서버에서 잔디 웹훅 호출 시 403 에러가 발생하는 이유는 무엇인가요?**  
  A. 사내 프록시 보안 정책으로 인해 WAS에서 외부 잔디 서버로의 직접 연결이 차단되어 있습니다. `app/core/jandi_briefing.py`가 관리하는 대기열과 DB_PC 간의 SSH 릴레이 통신을 거쳐 발송해야 합니다.