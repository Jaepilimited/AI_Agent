# -*- coding: utf-8 -*-
"""GCP 이관이 복제한 붐따 행을 지운다 — 처리함에서 '내용 없는 붐따'로 보이던 것.

`scripts/_import_gcp_feedback.py` 가 넣은 행이 기존 행과 **완전히 겹친다**: 같은 사용자·
같은 초·같은 대화. 다만 `messages` 는 함께 옮겨지지 않아 복제본은 질문·답변이 비어 있다.
처리함에서 "무엇에 대한 신고인지 알 수 없는 붐따" 로 21건이 계속 떠 있었다 (2026-08-24 확인).

⛔ `wontfix` 로 덮지 않는다. 그러면 제보자에게 **"고치지 않음" 알림 21개**가 간다 —
   붙어 있는 것보다 나쁘다. 복제본은 사용자가 남긴 신고가 아니라 이관 부산물이므로 지운다.

⛔ 지우기 전에 네 가지를 **행마다** 확인한다. 하나라도 어긋나면 그 행은 건드리지 않는다:
     1. 같은 (사용자, 생성시각, 대화) 에 짝이 정확히 2개
     2. 지울 쪽은 `messages` 에 원본 메시지가 **없고**, 남길 쪽은 **있다**
     3. 지울 쪽에 코멘트가 없다        ← 사람이 쓴 글은 절대 지우지 않는다
     4. 지울 쪽 상태가 `new` 다        ← 누가 처리한 흔적이 있으면 남긴다

⚠️ 삭제는 되돌릴 수 없다. 실행 전에 지울 행 전체를 JSON 으로 남긴다.

사용:
    python scripts/dedupe_feedback_rows.py                  # 미리보기 (기본)
    python scripts/dedupe_feedback_rows.py --apply          # 로컬 DB 에 적용
    python scripts/dedupe_feedback_rows.py --apply --remote # 프로덕션(WAS 경유)
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
WAS = "10.1.150.5"
REMOTE = "/home/jeffrey/AI_Agent"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _candidates(fetch_all):
    """지울 행 목록. 위 네 조건을 모두 만족하는 것만 돌려준다."""
    rows = fetch_all(
        "SELECT f.id, f.user_id, f.created_at, f.conversation_id, f.message_id, "
        "       f.rating, f.comment, f.status, f.anon_id, "
        "       (m.id IS NOT NULL) AS has_msg "
        "FROM message_feedback f LEFT JOIN messages m ON m.id = f.message_id "
        "WHERE f.rating < 0 ORDER BY f.created_at, f.id") or []

    groups = defaultdict(list)
    for r in rows:
        groups[(r["user_id"], str(r["created_at"]), r["conversation_id"])].append(r)

    drop, refused = [], []
    for key, group in groups.items():
        if len(group) != 2:                                   # 조건 1
            continue
        keep = [r for r in group if r["has_msg"]]
        gone = [r for r in group if not r["has_msg"]]
        if len(keep) != 1 or len(gone) != 1:                  # 조건 2
            refused.append((key, "짝의 모양이 다르다")); continue
        g = gone[0]
        if (g["comment"] or "").strip():                      # 조건 3
            refused.append((key, f"#{g['id']} 에 코멘트가 있다")); continue
        if g["status"] != "new":                              # 조건 4
            refused.append((key, f"#{g['id']} 상태가 {g['status']}")); continue
        g["_keep_id"] = keep[0]["id"]
        drop.append(g)
    return drop, refused


def run(apply: bool) -> int:
    sys.path.insert(0, str(PROJ))
    from app.db.mariadb import execute, fetch_all

    drop, refused = _candidates(fetch_all)
    for key, why in refused:
        print(f"  [보류] {key[1]} — {why}")
    for r in drop:
        print(f"  {'[삭제]' if apply else '[예정]'} #{r['id']} (남길 것 #{r['_keep_id']}) "
              f"{r['created_at']} user={r['user_id']}")
    if not drop:
        print("  지울 복제본이 없습니다.")
        return 0

    # ⚠️ 파일 이름에 DB 호스트를 넣는다. `data/` 는 **배포 대상**이라, 이름이 같으면
    #    다음 배포가 로컬 백업으로 **서버의 백업을 덮어쓴다** — 삭제의 유일한 되돌림
    #    기록이 그렇게 사라진다 (`EXCLUDE_PATHS` 에 `data/reports` 만 있다).
    from app.config import get_settings
    host = re.sub(r"[^A-Za-z0-9_.-]", "_", get_settings().mariadb_host or "unknown")
    backup = PROJ / "data" / f"feedback_dedupe_backup_{host}.json"
    if apply:
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text(json.dumps(drop, ensure_ascii=False, indent=1, default=str),
                          encoding="utf-8")
        print(f"\n  백업: {backup}")
        ids = [r["id"] for r in drop]
        n = execute("DELETE FROM message_feedback WHERE id IN ({}) AND rating < 0".format(
            ",".join(["%s"] * len(ids))), tuple(ids))
        print(f"  삭제 {n} 행")
    print(f"\n{'적용' if apply else '미리보기'}: 삭제 대상 {len(drop)} · 보류 {len(refused)}")
    if not apply:
        print("실제 삭제하려면 --apply 를 붙이세요. (되돌릴 수 없습니다)")
    return 0


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
    name = "_dedupe_feedback_rows.py"
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
    print(f"===== 붐따 복제본 정리 — {where} =====\n")
    return run_remote(apply) if "--remote" in sys.argv else run(apply)


if __name__ == "__main__":
    raise SystemExit(main())
