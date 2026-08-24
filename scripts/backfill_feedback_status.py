# -*- coding: utf-8 -*-
"""이미 고친 붐따(👎)에 뒤늦게 처리 표시를 단다 — 회신이 제보자에게 닿게 한다.

붐따 처리함(`feedback_inbox`)은 2026-08-14 에 생겼다. 그 전 붐따는 전부 `status='new'`
기본값으로 들어갔고, 고친 뒤 돌아와 표시한 사람이 없었다. 2026-08-24 전수 확인에서
**이미 고쳐졌는데 `new` 로 남아 있는 16건**을 찾았다. 같은 사고인 메가와리 Q2 오답조차
`#83`·`#117` 만 done 이고 `#96` 은 new 였다.

⛔ 근거 없이 done 을 달지 않는다. 아래 목록은 커밋·코드에서 **고친 자리를 확인한 것만**
   담는다. 재현이 필요한 추정 건(#100 상반기 매출 262억 차이 등)은 일부러 뺐다.

⚠️ 처리 표시는 곧 제보자에게 가는 알림이다 (`/api/notifications`). 메모가 회신 문구가
   되므로 "무엇이 어떻게 됐는지" 를 제보자의 말로 적는다. 메일은 꺼져 있어 앱 안에서만 뜬다.

사용:
    python scripts/backfill_feedback_status.py              # 미리보기 (기본)
    python scripts/backfill_feedback_status.py --apply      # 로컬 DB 에 적용
    python scripts/backfill_feedback_status.py --apply --remote   # 프로덕션(WAS 경유)

`--remote` 는 `CRAVER_SSH_PW` 가 필요하다 (노션 "AI Craver" 페이지).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ⚠️ Windows 콘솔은 cp949 라 한글 회신 문구가 인쇄에서 터진다 (리눅스 서버는 무관).
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = Path(__file__).resolve().parent.parent
WAS = "10.1.150.5"
REMOTE = "/home/jeffrey/AI_Agent"
WHO = "임재필"

# (붐따 id, 신고일(YYYY-MM-DD), 제보자에게 갈 회신)
# 날짜는 **행이 정말 그 신고인지 대조**하는 데 쓴다. 컷오버 때 mysqldump 로 통째로
# 옮겨 id 가 보존됐지만, 확인 없이 id 로만 UPDATE 하면 엉뚱한 사람에게 회신이 간다.
ENTRIES = [
    # ── 차트 (임재필, 사흘간 5건) ────────────────────────────────────────────
    (146, "2026-08-24", "연도별로 선을 나누는 차트를 고쳤습니다. 대륙을 둘 이상 물으면 "
                        "차트가 아예 안 나오던 것과, '2025 라벨/2026 라벨' 을 연도 비교로 "
                        "못 알아듣던 것 두 가지였습니다."),
    (145, "2026-08-24", "연도별로 선을 나누는 차트를 고쳤습니다. 대륙을 둘 이상 물으면 "
                        "차트가 아예 안 나오던 것과, '2025 라벨/2026 라벨' 을 연도 비교로 "
                        "못 알아듣던 것 두 가지였습니다."),
    (142, "2026-08-21", "'2025 라벨 / 2026 라벨' 로 연도를 나눠 그리도록 고쳤습니다. "
                        "이제 한 선 안에 두 해가 이어지지 않습니다."),
    (141, "2026-08-21", "'그래프로 그려줘' 같은 후속 요청이 조회를 다시 하지 않고 "
                        "대화 내용만으로 답하던 것을 고쳤습니다. 직전 조회 경로를 "
                        "그대로 물려받습니다."),
    (140, "2026-08-21", "'시계열 그래프로 그려줘' 같은 후속 요청이 조회를 다시 하지 않고 "
                        "대화 내용만으로 답하던 것을 고쳤습니다. 직전 조회 경로를 "
                        "그대로 물려받습니다."),
    (116, "2026-07-28", "같은 대화 안에서 매출이 달라지던 원인을 제거했습니다. "
                        "이전 조회의 조건을 그대로 이어받도록 고쳤습니다. (2026-08-10 반영)"),
    (114, "2026-07-27", "멀티턴 대화가 끊기던 문제를 고쳤습니다. 직전 조회와 맥락을 "
                        "구조로 이어받습니다. (2026-08-10·08-18 반영)"),
    # ⚠️ #111 은 **배포 뒤에** 표시해야 한다 — 코드는 고쳤지만 프로덕션에 올라가기 전에
    #    "해결됨" 이 뜨면 제보자가 다시 물었을 때 같은 오답을 본다.
    (111, "2026-07-23", "'히알루 테카' 를 물었는데 다른 라인인 '히알루시카' 정보로 답하던 "
                        "것을 고쳤습니다. 이제 지목한 라인 자료가 없으면 없다고 답합니다."),
    (105, "2026-07-08", "3년 된 노션 문서로 답하면서 그 사실을 밝히지 않던 것을 고쳤습니다. "
                        "이제 문서가 언제 것인지 함께 알립니다. (2026-08-18 반영)"),
    (104, "2026-07-08", "출처 링크가 항상 새 창에서 열리도록 바꿨습니다. 보던 대화가 "
                        "노션으로 바뀌지 않습니다."),
    (98,  "2026-07-01", "조회가 실패하던 SQL 오류(GROUP BY 누락)를 자동 재생성 대상에 "
                        "넣었습니다. (2026-08-06 반영)"),
    (96,  "2026-06-17", "메가와리 2026 Q2 기간이 등록돼 있지 않아 날짜가 지어졌습니다. "
                        "실제 62.2억으로 교정했습니다. (2026-08-18 반영)"),
    (95,  "2026-06-17", "구글 워크스페이스 조회가 프록시를 통과하지 못해 항상 실패하던 "
                        "원인을 제거했습니다. (2026-08-06 반영)"),
    # ⛔ #55 는 뺐다. 프로덕션에서 이미 `ack` 로 "[재현 필요] [기능 요청]" 이라고
    #    분류돼 있다 — 메가와리 **대시보드의 일정을 가져와 달라**는 기능 요청이고,
    #    Q2 기간을 프롬프트 표에 등록한 것으로는 그 요청이 끝나지 않는다.
    #    사람이 내린 판정을 스크립트가 덮어쓰면 처리함이 기록으로서 값을 잃는다.
    (53,  "2026-05-27", "차트가 조용히 사라지던 원인(JSON 응답 깨짐)을 고쳤습니다. "
                        "원형 차트도 정상 생성됩니다. (2026-08-11 반영)"),
    (91,  "2026-05-14", "CBT 는 팀 값이 브랜드 컬럼에 잘못 들어간 것이라, 이제 스킨천사 "
                        "매출에 합산합니다. (2026-08-05 반영)"),
    (80,  "2026-04-27", "제품명이 테이블 이름으로 잘못 읽혀 '허용되지 않은 테이블' 오류가 "
                        "나던 것을 고쳤습니다."),
    (79,  "2026-04-27", "제품명이 테이블 이름으로 잘못 읽혀 '허용되지 않은 테이블' 오류가 "
                        "나던 것을 고쳤습니다."),
    (78,  "2026-04-27", "제품명이 테이블 이름으로 잘못 읽혀 '허용되지 않은 테이블' 오류가 "
                        "나던 것을 고쳤습니다."),
    (73,  "2026-04-17", "조회하지 않은 외부 사실을 근거처럼 쓰던 것을 막았습니다. 이제 "
                        "데이터에 있는 것만 원인으로 말합니다. (2026-08-14·08-19 반영)"),
    (60,  "2026-04-13", "대시보드 카탈로그를 등록했습니다. '대시보드 종류' 질문에 목록으로 "
                        "답합니다. (2026-08-19 반영)"),
    (59,  "2026-04-13", "대시보드 카탈로그를 등록했습니다. '대시보드 종류' 질문에 목록으로 "
                        "답합니다. (2026-08-19 반영)"),
]


def run(apply: bool) -> int:
    sys.path.insert(0, str(PROJ))
    from app.core import feedback_inbox
    from app.db.mariadb import fetch_one

    done = skipped = mismatched = 0
    for fid, day, note in ENTRIES:
        row = fetch_one(
            "SELECT id, DATE(created_at) AS d, status, user_id FROM message_feedback "
            "WHERE id = %s AND rating = -1", (fid,))
        if not row:
            print(f"  [없음]   #{fid} — 이 DB 에 없습니다"); mismatched += 1; continue
        if str(row["d"]) != day:
            print(f"  [불일치] #{fid} — 신고일 {row['d']} ≠ {day}, 건너뜁니다")
            mismatched += 1; continue
        if row["status"] == feedback_inbox.STATUS_DONE:
            print(f"  [이미]   #{fid} — 이미 해결됨"); skipped += 1; continue
        if apply:
            feedback_inbox.set_status(fid, feedback_inbox.STATUS_DONE, WHO, note)
        print(f"  {'[처리]' if apply else '[예정]'}   #{fid} ({day}) — {note[:48]}…")
        done += 1

    print(f"\n{'적용' if apply else '미리보기'}: 처리 {done} · 이미 {skipped} · 대조실패 {mismatched}")
    if not apply:
        print("실제 적용하려면 --apply 를 붙이세요.")
    return 1 if mismatched else 0


def run_remote(apply: bool) -> int:
    pw = os.getenv("CRAVER_SSH_PW", "")
    if not pw:
        print("CRAVER_SSH_PW 환경변수가 필요합니다. (노션 'AI Craver' 페이지 참조)")
        return 1
    try:
        import paramiko
    except ImportError:
        print("paramiko 필요: ./sshenv/Scripts/python -m pip install paramiko")
        return 1
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(WAS, username="jeffrey", password=pw, timeout=30, banner_timeout=30)
    sftp = c.open_sftp()
    name = "_backfill_feedback_status.py"
    sftp.put(str(Path(__file__)), f"{REMOTE}/{name}")
    flag = " --apply" if apply else ""
    _, o, e = c.exec_command(
        f"cd {REMOTE} && ./venv/bin/python {name}{flag} 2>&1; "
        f"rc=$?; rm -f {name}; exit $rc", timeout=600)
    print(o.read().decode("utf-8", "replace").rstrip())
    rc = o.channel.recv_exit_status()
    err = e.read().decode("utf-8", "replace").strip()
    if err:
        print("[stderr]", err[:400])
    sftp.close(); c.close()
    return rc


def main() -> int:
    apply = "--apply" in sys.argv
    where = "프로덕션(WAS 경유)" if "--remote" in sys.argv else "이 서버의 DB"
    print(f"===== 붐따 처리 표시 백필 — {where} / {len(ENTRIES)}건 =====\n")
    return run_remote(apply) if "--remote" in sys.argv else run(apply)


if __name__ == "__main__":
    raise SystemExit(main())
