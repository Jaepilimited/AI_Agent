"""CLAUDE.md → AGENTS.md 동기화.

두 파일은 같은 규칙을 담지만 읽는 주체가 다르다 (Claude Code / Codex·기타 에이전트).
사본을 손으로 관리했더니 두 달치가 밀려, **지금은 금지된 규칙**이 AGENTS.md 에만
살아남아 있었다 (2026-08-12 발견):

  - 프로덕션 주소가 172.16.1.250 (이관 전 주소 — 여기 배포하면 아무것도 반영 안 됨)
  - "판매수량 → SALES_ALL_Backup.Total_Qty" (세트를 1개로 세어 오답, 현재 사용 금지)

규칙을 고칠 때는 **CLAUDE.md 만** 고치고 이 스크립트를 돌린다.

    python scripts/sync_agents_md.py            # 동기화
    python scripts/sync_agents_md.py --check    # 어긋나 있으면 exit 1 (CI·훅용)
"""
import os
import sys

# Windows 콘솔 기본 코드페이지(cp949)에서 —·✅ 같은 문자가 UnicodeEncodeError 를 낸다
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE_DIR, "CLAUDE.md")
DST = os.path.join(BASE_DIR, "AGENTS.md")

HEADER = """<!-- 자동 생성 파일 — 직접 고치지 마라.
     규칙의 단일 소스는 CLAUDE.md 다. 고친 뒤 `python scripts/sync_agents_md.py` 로 갱신할 것.
     (손으로 관리하던 시절 두 달치가 밀려, 이관 전 프로덕션 주소와 폐기된 수량 컬럼 규칙이
      이 파일에만 남아 있었다 — 2026-08-12) -->

"""


def build() -> str:
    with open(SRC, encoding="utf-8") as f:
        return HEADER + f.read()


def main() -> int:
    want = build()
    current = ""
    if os.path.exists(DST):
        with open(DST, encoding="utf-8") as f:
            current = f.read()

    if "--check" in sys.argv:
        if current == want:
            print("OK — AGENTS.md 가 CLAUDE.md 와 일치한다")
            return 0
        print("MISMATCH — `python scripts/sync_agents_md.py` 를 실행하라", file=sys.stderr)
        return 1

    if current == want:
        print("변경 없음")
        return 0
    with open(DST, "w", encoding="utf-8", newline="\n") as f:
        f.write(want)
    print(f"AGENTS.md 갱신 완료 ({len(want.splitlines())}줄)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
