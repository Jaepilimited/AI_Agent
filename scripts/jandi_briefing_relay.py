"""출근 브리핑 → 잔디 릴레이. **DB_PC(172.16.1.250)에서만 돈다.**

왜 릴레이인가 (2026-08-25 실측):

    DB_PC → wh.jandi.com:443   OK (0.18s)
    DB_PC → 10.1.150.5:22      OK          ← SSH 만 열려 있다
    DB_PC → 10.1.100.5:80      timeout
    DB_PC → 10.1.150.5:3000    timeout
    WAS/APP → wh.jandi.com     403          ← 프록시 화이트리스트에 없다 (2026-08-18)

즉 **잔디에 붙는 쪽과 브리핑을 가진 쪽이 서로 다르다.** 서버는 09:00 에 만들어
대기열에 넣기만 하고, 이 스크립트가 SSH 터널로 꺼내 잔디로 보낸 뒤 결과를 되돌려준다.

실행:
    set CRAVER_SSH_PW=...              (노션 "AI Craver" 페이지)
    set BRIEFING_RELAY_TOKEN=...       (WAS .env 와 같은 값)
    ./sshenv/Scripts/python scripts/jandi_briefing_relay.py [--dry-run]

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

WAS_HOST = os.environ.get("CRAVER_WAS_HOST", "10.1.150.5")
WAS_USER = os.environ.get("CRAVER_SSH_USER", "jeffrey")
APP_PORT = int(os.environ.get("CRAVER_APP_PORT", "3000"))
SSH_TIMEOUT = 15
HTTP_TIMEOUT = 20
JANDI_TIMEOUT = 10


def _fail(message: str) -> int:
    print(f"[X] {message}", file=sys.stderr)
    return 1


def _tunnelled_request(client, method: str, path: str, token: str, payload=None):
    """WAS 의 127.0.0.1:3000 으로 직접 여는 SSH 채널 위에서 HTTP 를 말한다.

    포트 포워딩 대신 채널을 그대로 소켓으로 쓴다 — 로컬 포트를 점유하지 않고,
    한 번의 호출이 끝나면 채널도 같이 닫힌다.
    """
    channel = client.get_transport().open_channel(
        "direct-tcpip", ("127.0.0.1", APP_PORT), ("127.0.0.1", 0),
    )
    channel.settimeout(HTTP_TIMEOUT)
    conn = http.client.HTTPConnection("127.0.0.1", APP_PORT, timeout=HTTP_TIMEOUT)
    conn.sock = channel
    try:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"X-Relay-Token": token, "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        raw = response.read().decode("utf-8", "replace")
        return response.status, raw
    finally:
        try:
            conn.close()
        except OSError:
            pass


#: 종류별 표시. ⛔ 서버(`jandi_briefing.KIND_META`)와 **같은 목록이다** —
#: 릴레이는 DB_PC 에서 단독 실행돼야 해서 앱 모듈을 import 하지 않는다.
#: 한쪽만 고치면 색과 제목이 어긋난다 (에러는 안 난다). 테스트가 두 목록을 대조한다.
KIND_META = {
    "briefing": ("오늘의 출근 브리핑", "#f5a623"),
    "report_share": ("보고서가 공유되었습니다", "#4b8bf5"),
    "feedback": ("보내주신 의견이 처리되었습니다", "#46be8a"),
    "announcement": ("공지", "#9b6bde"),
}


def _post_jandi(url: str, body: str, kind: str = "briefing",
                title: str = "", link: str = "") -> tuple[bool, str]:
    label, color = KIND_META.get(kind, KIND_META["briefing"])
    info = [{"title": title or label}]
    if link:
        info.append({"title": "셀라에서 이어서 물어보기", "description": link})
    payload = json.dumps({
        "body": body[:9000], "connectColor": color, "connectInfo": info,
    }).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Accept": "application/vnd.tosslab.jandi-v2+json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=JANDI_TIMEOUT) as response:
            response.read()
        return True, ""
    except urllib.error.HTTPError as exc:
        return False, f"http {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, f"{type(exc).__name__}: {exc}"[:200]


def main() -> int:
    parser = argparse.ArgumentParser(description="출근 브리핑 잔디 릴레이 (DB_PC 전용)")
    parser.add_argument("--dry-run", action="store_true", help="꺼내 오기만 하고 보내지 않는다")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

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
        client.connect(
            WAS_HOST, username=WAS_USER, password=password,
            timeout=SSH_TIMEOUT, banner_timeout=SSH_TIMEOUT, auth_timeout=SSH_TIMEOUT,
        )
    except Exception as exc:
        return _fail(f"SSH 접속 실패 {WAS_USER}@{WAS_HOST}: {type(exc).__name__} {exc}")

    try:
        status, raw = _tunnelled_request(
            client, "GET", f"/api/internal/briefing-outbox?limit={args.limit}", token,
        )
        if status == 404:
            return _fail(
                "WAS 가 404 를 돌려줬습니다. BRIEFING_RELAY_TOKEN 이 서버와 다르거나 "
                "서버에 설정되지 않았습니다 (.env 는 배포에서 제외되므로 WAS 에서 직접 넣어야 합니다).",
            )
        if status != 200:
            return _fail(f"대기열 조회 실패: HTTP {status} {raw[:200]}")
        items = json.loads(raw).get("items", [])
        print(f"[i] 대기 {len(items)}건")
        if not items:
            return 0
        if args.dry_run:
            for item in items:
                first = item["body"].splitlines()[0] if item["body"] else ""
                print(f"    #{item['id']} {item['for_date']} {first[:60]}")
            return 0

        results = []
        for item in items:
            ok, error = _post_jandi(
                item["webhook_url"], item["body"],
                kind=item.get("kind", "briefing"),
                title=item.get("title", ""), link=item.get("link", ""))
            results.append({"id": item["id"], "ok": ok, "error": error})
            print(f"    #{item['id']} {'보냄' if ok else '실패 ' + error}")

        status, raw = _tunnelled_request(
            client, "POST", "/api/internal/briefing-outbox/ack", token, {"results": results},
        )
        if status != 200:
            # ⚠️ 여기서 실패하면 다음 실행에 같은 건을 다시 보낸다. 조용히 넘기지 않는다.
            return _fail(f"결과 회신 실패: HTTP {status} {raw[:200]} — 다음 실행에 중복 발송될 수 있습니다.")
        print(f"[o] {json.loads(raw)}")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
