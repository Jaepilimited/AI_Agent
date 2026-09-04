# -*- coding: utf-8 -*-
"""영상 같은 큰 정적 파일을 서버에 **한 번만** 올린다.

⛔ `deploy_new_server.py` 는 트리를 통째로 보낸다. 40MB 짜리 MV 가 거기 끼면
   **배포할 때마다** 함께 올라간다 (전체 전송량이 37.8MB 다 — 두 배가 넘는다).
   그래서 `EXCLUDE_EXT` 에서 영상 확장자를 빼고, 바뀔 때만 이걸로 올린다.

⚠️ 저장소에도 두지 않는다 (`.gitignore`). 40MB 바이너리는 한 번 들어가면
   영영 남는다. 원본에서 다시 만드는 방법은 `deploy_new_server.py` 머리말에 있다.

사용:
    set CRAVER_SSH_PW=...
    python scripts/upload_media.py            # app/static/media/* 전부
    python scripts/upload_media.py --dry      # 보낼 목록만
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
LOCAL_DIR = PROJ / "app" / "static" / "media"
REMOTE_DIR = "/home/jeffrey/AI_Agent/app/static/media"
HOST = "10.1.150.5"
USER = "jeffrey"


def main() -> int:
    if not LOCAL_DIR.is_dir():
        print(f"보낼 디렉토리가 없습니다: {LOCAL_DIR}")
        return 1
    files = sorted(p for p in LOCAL_DIR.iterdir() if p.is_file())
    if not files:
        print(f"보낼 파일이 없습니다: {LOCAL_DIR}")
        return 1

    total = sum(f.stat().st_size for f in files)
    print(f"대상: {HOST}:{REMOTE_DIR}")
    for f in files:
        print(f"    {f.name}  {f.stat().st_size / 1024 / 1024:.1f} MB")
    print(f"합계 {total / 1024 / 1024:.1f} MB")

    if "--dry" in sys.argv:
        print("  [--dry 이므로 전송 안 함]")
        return 0

    pw = os.getenv("CRAVER_SSH_PW", "")
    if not pw:
        print("CRAVER_SSH_PW 환경변수가 필요합니다. (노션 AI Craver 페이지 참조)")
        return 1

    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=pw, timeout=30)
    try:
        client.exec_command(f"mkdir -p {REMOTE_DIR}")[1].channel.recv_exit_status()
        sftp = client.open_sftp()
        try:
            for f in files:
                # ⛔ 임시 이름으로 올린 뒤 바꿔 단다 — 올리는 도중에 누가 열면
                #    반쪽 파일을 받는다 (CSV 보관본을 원자적으로 쓰는 것과 같은 이유)
                tmp = f"{REMOTE_DIR}/.{f.name}.part"
                sftp.put(str(f), tmp)
                sftp.posix_rename(tmp, f"{REMOTE_DIR}/{f.name}")
                print(f"  올림: {f.name}")
        finally:
            sftp.close()
        # 웹서버가 읽을 수 있어야 한다
        client.exec_command(f"chmod 644 {REMOTE_DIR}/*")[1].channel.recv_exit_status()
    finally:
        client.close()
    print("완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
