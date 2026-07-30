"""신규 서버 통합 기능 테스트 — 서버 내부에서 실제 라우트를 호출해 검증한다.

사용:
    set CRAVER_SSH_PW=...
    python scripts/e2e_test_new_server.py

인증은 서버의 JWT 시크릿으로 검증용 토큰(20분)을 직접 발급해 처리한다.
실사용자 비밀번호는 해시로만 저장돼 있어 로그인 경로를 쓸 수 없기 때문이다.
"""
from __future__ import annotations

import os
import sys

REMOTE = "/home/jeffrey/AI_Agent"
WAS = "10.1.150.5"

TEST_SCRIPT = r'''
import json, os, re, sys, time
sys.path.insert(0, "/home/jeffrey/AI_Agent")
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
load_dotenv("/home/jeffrey/AI_Agent/.env")
import jwt, httpx, pymysql
from app.config import get_settings

BASE = "http://127.0.0.1:3000"
S = get_settings()

cn = pymysql.connect(host=os.getenv("MARIADB_HOST"), port=int(os.getenv("MARIADB_PORT","3306")),
                     user=os.getenv("MARIADB_USER"), password=os.getenv("MARIADB_PASSWORD"),
                     database=os.getenv("MARIADB_DATABASE"), cursorclass=pymysql.cursors.DictCursor)
cur = cn.cursor()
cur.execute("SELECT id, display_name, role FROM users ORDER BY (role='admin') DESC, id LIMIT 1")
u = cur.fetchone(); cn.close()
tok = jwt.encode({"user_id": u["id"], "email": "", "brand_filter": "", "role": u["role"],
                  "exp": datetime.now(timezone.utc) + timedelta(minutes=25)},
                 S.jwt_secret_key, algorithm="HS256")
CK = {"token": tok}
print(f"  검증 계정: {u['display_name']} ({u['role']})\n")

results = []
def rec(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'O' if ok else 'X'}] {name:<28} {detail}")

# ── 1. 기본 엔드포인트 ──
print("── 1. 기본 엔드포인트")
for name, ep in [("health", "/health"), ("safety/status", "/safety/status"),
                 ("announcement", "/api/announcement"), ("login 페이지", "/login"),
                 ("chat 화면", "/frontend/chat.html")]:
    try:
        r = httpx.get(f"{BASE}{ep}", cookies=CK, timeout=20)
        rec(name, r.status_code == 200, f"HTTP {r.status_code}")
    except Exception as e:
        rec(name, False, type(e).__name__)

# ── 2. 인증·대화 ──
print("\n── 2. 인증 / 대화 저장소")
try:
    r = httpx.get(f"{BASE}/api/auth/me", cookies=CK, timeout=20)
    rec("내 정보 조회", r.status_code == 200, f"HTTP {r.status_code}")
except Exception as e:
    rec("내 정보 조회", False, type(e).__name__)
try:
    r = httpx.get(f"{BASE}/api/conversations", cookies=CK, timeout=30)
    n = len(r.json()) if r.status_code == 200 else -1
    rec("대화 목록", r.status_code == 200 and n >= 0, f"{n}건")
except Exception as e:
    rec("대화 목록", False, type(e).__name__)

# ── 3. 라우트별 실제 질의 ──
print("\n── 3. 라우트별 질의 (LLM 실호출)")
def ask(q, timeout=200):
    t0 = time.time()
    r = httpx.post(f"{BASE}/v1/chat/completions", cookies=CK, timeout=timeout,
                   json={"model": "claude", "stream": False,
                         "messages": [{"role": "user", "content": q}]})
    el = time.time() - t0
    if r.status_code != 200:
        return None, el, r.text[:120]
    d = r.json()
    c = ((d.get("choices") or [{}])[0].get("message") or {}).get("content", "")
    return c or str(d), el, ""

CASES = [
    ("BigQuery 매출", "올해 전체 채널 매출 알려줘",
     lambda c: "sales1_r" in c.lower() or "억" in c or "원" in c),
    ("BigQuery 수량(Product)", "올해 전체 채널 판매수량 알려줘",
     lambda c: "sales_integration.product" in c.lower() or "total_qty" in c.lower()),
    ("BigQuery 차트", "올해 국가별 매출 상위 5개 차트로 보여줘",
     lambda c: "chart-config" in c or '"type"' in c),
    ("신제품 정의", "올해 전체 신제품 매출 상위 3개",
     lambda c: "interval 6 month" in c.lower()),
    ("메가와리(Q10)", "2025년 4분기 메가와리 매출 알려줘",
     lambda c: "q10" in c.lower() or "qoo10" in c.lower()),
    ("노션 문서검색", "노션에서 연차 규정 알려줘",
     lambda c: len(c) > 200),
    ("일반 대화(direct)", "안녕하세요, 무엇을 도와줄 수 있나요?",
     lambda c: len(c) > 30),
]
for name, q, check in CASES:
    try:
        c, el, err = ask(q)
        if c is None:
            rec(name, False, f"{el:.1f}초 / {err}")
        else:
            rec(name, check(c), f"{el:.1f}초 / {len(c)}자")
    except Exception as e:
        rec(name, False, f"{type(e).__name__}: {str(e)[:60]}")

# ── 4. 스트리밍 ──
print("\n── 4. SSE 스트리밍")
try:
    t0 = time.time(); first = None; n = 0
    with httpx.stream("POST", f"{BASE}/v1/chat/completions", cookies=CK, timeout=200,
                      json={"model": "claude", "stream": True,
                            "messages": [{"role": "user", "content": "한 문장으로 인사해줘"}]}) as r:
        for line in r.iter_lines():
            if not line: continue
            n += 1
            if first is None: first = time.time() - t0
            if n > 500: break
    rec("스트리밍", n > 0 and first is not None, f"첫 청크 {first:.2f}초 / {n}청크")
except Exception as e:
    rec("스트리밍", False, type(e).__name__)

# ── 5. 배치 스케줄러 ──
print("\n── 5. 배치 스케줄러 (DB 기록 기준)")
try:
    cn = pymysql.connect(host=os.getenv("MARIADB_HOST"), port=int(os.getenv("MARIADB_PORT","3306")),
                         user=os.getenv("MARIADB_USER"), password=os.getenv("MARIADB_PASSWORD"),
                         database=os.getenv("MARIADB_DATABASE"))
    cu = cn.cursor()
    cu.execute("SELECT MAX(processed_at) FROM wiki_extraction_log")
    last = cu.fetchone()[0]
    fresh = last and (datetime.now() - last).total_seconds() < 7200
    rec("위키추출(매시 :15)", bool(fresh), f"최신 {last}")
    cu.execute("SELECT COUNT(*) FROM ad_users")
    n_ad = cu.fetchone()[0]
    rec("AD 사용자 데이터", n_ad > 400, f"{n_ad}명")
    cu.execute("SELECT COUNT(*) FROM conversations")
    rec("대화 데이터 이관", cu.fetchone()[0] > 800, "")
    cn.close()
except Exception as e:
    rec("배치 확인", False, f"{type(e).__name__}: {str(e)[:60]}")

ok = sum(1 for _, o, _ in results if o)
print(f"\n{'='*54}")
print(f"  통과 {ok} / 전체 {len(results)}")
fails = [n for n, o, _ in results if not o]
if fails:
    print("  실패: " + ", ".join(fails))
'''


def main() -> int:
    pw = os.getenv("CRAVER_SSH_PW", "")
    if not pw:
        print("CRAVER_SSH_PW 환경변수가 필요합니다.")
        return 1
    import paramiko
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(WAS, username="jeffrey", password=pw, timeout=30, banner_timeout=30)
    sftp = c.open_sftp()
    with sftp.open(f"{REMOTE}/_e2e.py", "w") as f:
        f.write(TEST_SCRIPT)
    print("===== 신규 서버 통합 기능 테스트 =====\n")
    _, o, e = c.exec_command(f"cd {REMOTE} && ./venv/bin/python _e2e.py 2>&1; rm -f _e2e.py", timeout=2400)
    print(o.read().decode("utf-8", "replace").rstrip())
    err = e.read().decode("utf-8", "replace").strip()
    if err:
        print("[stderr]", err[:400])
    sftp.close()
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
