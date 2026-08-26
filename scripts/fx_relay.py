"""오늘의 환율 → 서버. **DB_PC(172.16.1.250)에서만 돈다.**

왜 릴레이인가 (2026-08-26 실측):

    WAS → open.er-api.com / frankfurter / koreaexim / naver   전부 000 (2ms 즉시 실패)
    WAS → generativelanguage.googleapis.com                   404  ← 프록시는 살아 있다
    DB_PC → open.er-api.com / api.frankfurter.app             OK (0.17s)

프록시 화이트리스트에 환율 API 가 없어서 막힌 것이지 네트워크가 죽은 게 아니다.
잔디 브리핑 릴레이와 **같은 구조·같은 토큰**을 쓴다 — 다만 방향이 반대다 (밀어 넣는다).

실행:
    set CRAVER_SSH_PW=...
    set BRIEFING_RELAY_TOKEN=...
    ./sshenv/Scripts/python scripts/fx_relay.py [--dry-run]

⚠️ paramiko 는 `sshenv/` 격리 venv 에 있다. 전역 파이썬으로 돌리지 마라.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timezone

WAS_HOST = os.environ.get("CRAVER_WAS_HOST", "10.1.150.5")
WAS_USER = os.environ.get("CRAVER_SSH_USER", "jeffrey")
APP_PORT = int(os.environ.get("CRAVER_APP_PORT", "3000"))
HTTP_TIMEOUT = 20
FETCH_TIMEOUT = 12

#: 브리핑에 싣는 통화. 회사 주력 시장 기준 + 동남아·오세아니아.
#: ⛔ **`app/core/fx_rates.py` 와 같은 목록이어야 한다.** 여기는 DB_PC 에서 단독으로
#:    돌아야 해서 앱 모듈을 import 하지 않는다 — 사본이 불가피하다. 한쪽만 늘리면
#:    수집은 되는데 화면에 안 뜨거나(반대면 화면이 빈 칸) **에러 없이** 어긋난다.
CURRENCIES = ("USD", "JPY", "CNY", "EUR", "SGD", "PHP", "MYR", "IDR", "AUD")
#: JPY 는 100엔 고시가 한국 관행이다. IDR 은 1루피아가 0.078원이라 100단위로 적는다.
UNITS = {"JPY": 100, "IDR": 100}


def _quote_date(stamp: str) -> str:
    """`Wed, 26 Aug 2026 00:02:31 +0000` → `2026-08-26`. 못 읽으면 오늘로 둔다."""

    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S GMT"):
        try:
            parsed = datetime.strptime(stamp, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return str(parsed.astimezone(timezone.utc).date())
        except ValueError:
            continue
    return str(date.today())


def _fail(message: str) -> int:
    print(f"[X] {message}", file=sys.stderr)
    return 1


def _get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "cella-fx/1.0"})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_rates() -> tuple[list[dict], str, str] | None:
    """(행, 출처, 기준일). 한 곳이 죽어도 다른 곳으로 간다 — 환율은 매일 있어야 한다."""

    try:
        data = _get_json("https://open.er-api.com/v6/latest/USD")
        if data.get("result") == "success":
            rates = data["rates"]
            krw = float(rates["KRW"])
            rows = []
            for currency in CURRENCIES:
                if currency == "USD":
                    value = krw
                else:
                    per_usd = float(rates[currency])
                    value = krw / per_usd * UNITS.get(currency, 1)
                rows.append({"currency": currency, "krw": round(value, 4),
                             "unit": UNITS.get(currency, 1)})
            # ⛔ `date.today()` 로 찍지 마라 — 이 소스는 **09:02 KST 에 갱신**된다.
            #    09:00 브리핑 시점의 최신값은 전일 고시다. API 가 준 날짜를 그대로 쓴다.
            stamp = str(data.get("time_last_update_utc", ""))
            quoted = _quote_date(stamp)
            return rows, "open.er-api.com", quoted
    except Exception as exc:
        print(f"[!] open.er-api 실패: {type(exc).__name__} {exc}", file=sys.stderr)

    try:
        others = ",".join(c for c in CURRENCIES if c != "USD")
        data = _get_json(f"https://api.frankfurter.app/latest?from=USD&to=KRW,{others}")
        rates = data["rates"]
        krw = float(rates["KRW"])
        rows = []
        for currency in CURRENCIES:
            if currency == "USD":
                value = krw
            else:
                value = krw / float(rates[currency]) * UNITS.get(currency, 1)
            rows.append({"currency": currency, "krw": round(value, 4),
                         "unit": UNITS.get(currency, 1)})
        # frankfurter 는 ECB 고시라 KST 아침엔 늘 전일자다 — 그 날짜를 그대로 쓴다.
        return rows, "frankfurter.app", str(data.get("date", "")) or str(date.today())
    except Exception as exc:
        print(f"[!] frankfurter 실패: {type(exc).__name__} {exc}", file=sys.stderr)

    return None


def fetch_history(day: str) -> tuple[list[dict], str, str] | None:
    """지정한 날짜의 환율 (ECB). 전월대비 기준값을 채우려고 만들었다.

    ⛔ open.er-api 무료 요금제에는 **과거 조회가 없다.** 그래서 여기만 ECB 를 쓴다.
    ⚠️ 출처가 다르면 그 차이가 변동률에 섞인다 — 실측(2026-08-26)으로 두 출처는
       대부분 0.08% 이내지만 **CNY 0.25% · IDR 0.17%** 벌어진다. 한 달치가 쌓이면
       같은 출처끼리 비교하게 되어 저절로 사라진다.
    ⚠️ ECB 는 주말·공휴일에 고시가 없다 — 그때는 **직전 영업일 값**을 그 날짜로
       돌려준다. 응답의 `date` 를 그대로 쓰는 이유다 (요청한 날짜를 쓰면 거짓이 된다).
    """
    others = ",".join(c for c in CURRENCIES if c != "USD")
    try:
        data = _get_json(f"https://api.frankfurter.app/{day}?from=USD&to=KRW,{others}")
        rates = data["rates"]
        krw = float(rates["KRW"])
        rows = []
        for currency in CURRENCIES:
            if currency == "USD":
                value = krw
            else:
                value = krw / float(rates[currency]) * UNITS.get(currency, 1)
            rows.append({"currency": currency, "krw": round(value, 4),
                         "unit": UNITS.get(currency, 1)})
        return rows, "frankfurter.app", str(data.get("date") or day)
    except Exception as exc:
        print(f"[!] {day} 과거 조회 실패: {type(exc).__name__} {exc}", file=sys.stderr)
        return None

def push(client, token: str, payload: dict) -> tuple[int, str]:
    """WAS 의 127.0.0.1:3000 으로 여는 SSH 채널 위에서 HTTP 를 말한다."""

    channel = client.get_transport().open_channel(
        "direct-tcpip", ("127.0.0.1", APP_PORT), ("127.0.0.1", 0),
    )
    channel.settimeout(HTTP_TIMEOUT)
    conn = http.client.HTTPConnection("127.0.0.1", APP_PORT, timeout=HTTP_TIMEOUT)
    conn.sock = channel
    try:
        conn.request(
            "POST", "/api/internal/fx",
            body=json.dumps(payload).encode("utf-8"),
            headers={"X-Relay-Token": token, "Content-Type": "application/json"},
        )
        response = conn.getresponse()
        return response.status, response.read().decode("utf-8", "replace")
    finally:
        try:
            conn.close()
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="오늘의 환율 릴레이 (DB_PC 전용)")
    parser.add_argument("--dry-run", action="store_true", help="가져오기만 하고 보내지 않는다")
    parser.add_argument("--date", metavar="YYYY-MM-DD",
                        help="그 날짜의 과거 환율을 채운다 (전월대비 기준값 백필용, ECB)")
    args = parser.parse_args()

    fetched = fetch_history(args.date) if args.date else fetch_rates()
    if not fetched:
        return _fail("환율을 가져오지 못했습니다 (두 곳 모두 실패).")
    rows, source, quoted = fetched
    print(f"[i] {source} · 고시일 {quoted}")
    for row in rows:
        unit = f"({row['unit']})" if row["unit"] != 1 else ""
        print(f"    {row['currency']}{unit} {row['krw']:,.2f}원")
    if args.dry_run:
        return 0

    password = os.environ.get("CRAVER_SSH_PW", "")
    token = os.environ.get("BRIEFING_RELAY_TOKEN", "")
    if not password:
        return _fail("CRAVER_SSH_PW 가 없습니다 (노션 'AI Craver' 페이지).")
    if not token:
        return _fail("BRIEFING_RELAY_TOKEN 이 없습니다 (WAS .env 와 같은 값이어야 합니다).")

    try:
        import paramiko
    except ImportError:
        return _fail("paramiko 가 없습니다. ./sshenv/Scripts/python 으로 실행하세요.")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(WAS_HOST, username=WAS_USER, password=password,
                       timeout=15, banner_timeout=15, auth_timeout=15)
    except Exception as exc:
        return _fail(f"SSH 접속 실패 {WAS_USER}@{WAS_HOST}: {type(exc).__name__} {exc}")

    try:
        status, body = push(client, token, {
            "for_date": quoted, "rates": rows, "source": source,
        })
        if status == 404:
            return _fail(
                "WAS 가 404 를 돌려줬습니다. BRIEFING_RELAY_TOKEN 이 서버와 다르거나 "
                "서버에 설정되지 않았습니다 (.env 는 배포에서 제외된다).")
        if status != 200:
            return _fail(f"전송 실패: HTTP {status} {body[:200]}")
        print(f"[o] {body}")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
