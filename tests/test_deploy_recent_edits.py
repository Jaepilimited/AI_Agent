# -*- coding: utf-8 -*-
"""관문이 걸렸을 때 "누가 만지고 있는지" 를 함께 보여준다. 2026-09-09.

⛔ **왜 있나** (하루에 두 번):

        09:04  import 만 있고 모듈이 없는 상태가 전송됐다 → 프로덕션 80초 정지
        09:15  인증 미들웨어가 반쯤 배선된 상태 → 스위트 76건 실패

   두 번 다 원인은 **다른 세션이 그 순간 편집 중**이었다는 것이다. 그런데
   누르는 사람이 보는 것은 `76 failed` 뿐이라 "내가 뭘 깼나" 를 한참 뒤진다.
   실제로 그날 같은 스위트를 4분 간격으로 두 번 쟀더니 **76건 → 1건**이었다 —
   둘 다 정확히 잰 것이고 트리가 그 사이에 달라진 것이다.

   ⟹ 실패 옆에 "최근에 바뀐 파일" 을 찍으면 *"내 잘못이 아니라 기다릴 일"*
      이라는 판단을 즉시 할 수 있다.

⛔ **막지 않고 판단도 하지 않는다.** 이미 걸린 관문 옆에 붙는 보조 설명이다.
⛔ **"누가" 를 단정하지 않는다** — mtime 은 *언제* 만 말한다. 그날 우리는
   파일 이름만 보고 세션을 잘못 짚었다.
"""
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.deploy_preflight import (  # noqa: E402
    RECENT_EDIT_WINDOW_SECONDS,
    _ago,
    format_recent_edits_notice,
    recently_touched,
)

DEPLOY = ROOT / "scripts" / "deploy_new_server.py"


@pytest.fixture()
def tree(tmp_path):
    import subprocess
    for d in ("app/core", "app/agents", "scripts", "tests"):
        (tmp_path / d).mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, capture_output=True)
    return tmp_path


def touch(tree, rel, seconds_ago=0, body="x = 1\n"):
    p = tree / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    if seconds_ago:
        t = time.time() - seconds_ago
        import os
        os.utime(p, (t, t))
    return p


# ---------------------------------------------------------------------------
# 무엇을 보여주는가
# ---------------------------------------------------------------------------

def test_a_file_touched_moments_ago_is_reported(tree):
    """⛔ 2026-09-09 09:04 — import 를 넣고 30초 뒤에 전송이 떨어졌다."""
    touch(tree, "app/agents/sql_agent.py", seconds_ago=30)
    rows = recently_touched(tree)
    assert [r[0] for r in rows] == ["app/agents/sql_agent.py"]
    assert rows[0][1] == pytest.approx(30, abs=5)


def test_an_old_file_is_not_reported(tree):
    """⚠️ 창을 넓히면 조용한 날에도 목록이 길어져 읽히지 않는다."""
    touch(tree, "app/core/settled.py", seconds_ago=RECENT_EDIT_WINDOW_SECONDS + 120)
    assert recently_touched(tree) == []


def test_a_quiet_tree_says_nothing(tree):
    touch(tree, "app/core/old.py", seconds_ago=99999)
    assert recently_touched(tree) == []
    assert format_recent_edits_notice([]) == []


def test_newest_first(tree):
    touch(tree, "app/core/a.py", seconds_ago=300)
    touch(tree, "app/core/b.py", seconds_ago=10)
    touch(tree, "app/core/c.py", seconds_ago=120)
    assert [r[0] for r in recently_touched(tree)] == [
        "app/core/b.py", "app/core/c.py", "app/core/a.py"]


def test_an_untracked_new_module_is_marked(tree):
    """⚠️ 오늘 두 번 다 신호가 이것이었다 — `sales_outlook.py` 도
    `session_auth.py` 도 **방금 생긴 untracked 새 모듈**이었다.
    `M` 보다 훨씬 강한 신호다."""
    touch(tree, "app/core/session_auth.py", seconds_ago=20)
    rows = recently_touched(tree)
    assert rows[0][2] == "??"
    body = "\n".join(format_recent_edits_notice(rows))
    assert "git 에 없음" in body


def test_the_same_scope_as_the_untracked_notice(tree):
    """⛔ 감시 대상 판정은 `_load_bearing` 한 곳이다 — 두 번 구현하면 갈린다."""
    touch(tree, "scripts/_probe.py", seconds_ago=5)          # 일회성 관례
    touch(tree, "tests/test_x.py", seconds_ago=5)            # 전송 안 됨
    touch(tree, "app/frontend/chat.js", seconds_ago=5)       # 파이썬 아님
    touch(tree, "app/core/real.py", seconds_ago=5)
    assert [r[0] for r in recently_touched(tree)] == ["app/core/real.py"]


def test_the_window_is_ten_minutes():
    """실측(2026-09-09 09:30): 2분 0개 · 5분 2개 · 10분 2개 · 20분 11개 · 60분 16개.

    오늘 두 사고는 30초·2분 간격이라 어느 창으로도 잡히지만, 넓히면 상시 뜬다.
    """
    assert RECENT_EDIT_WINDOW_SECONDS == 600


def test_a_custom_window_is_honoured(tree):
    touch(tree, "app/core/x.py", seconds_ago=300)
    assert recently_touched(tree, within_seconds=120) == []
    assert recently_touched(tree, within_seconds=600)


# ---------------------------------------------------------------------------
# 사람이 읽는 문구
# ---------------------------------------------------------------------------

def test_the_notice_never_names_a_session():
    """⛔ mtime 은 *언제* 만 말한다. 그날 우리는 파일 이름만 보고
    세션을 잘못 짚었다 (`sales_outlook` → S&OP 세션인 줄 알았는데 아니었다)."""
    body = "\n".join(format_recent_edits_notice([("app/core/x.py", 30, "M")]))
    assert "수 있습니다" in body, "단정하면 틀린다"
    for word in ("ai-agent", "s-op", "세션이 편집 중입니다"):
        assert word not in body


def test_the_notice_tells_you_what_to_do():
    body = "\n".join(format_recent_edits_notice([("app/core/x.py", 30, "M")]))
    assert "끝난 뒤 다시 실행" in body


def test_the_notice_caps_the_list():
    rows = [(f"app/core/m{i}.py", i, "M") for i in range(20)]
    lines = format_recent_edits_notice(rows)
    listed = [l for l in lines if ".py" in l]
    assert len(listed) == 8
    assert any("외 12개" in l for l in lines)


@pytest.mark.parametrize("secs,want", [(5, "5초 전"), (89, "89초 전"), (90, "1분 전"), (610, "10분 전")])
def test_elapsed_reads_naturally(secs, want):
    assert _ago(secs) == want


# ---------------------------------------------------------------------------
# 배선 — 걸렸을 때만 나오는가
# ---------------------------------------------------------------------------

def test_it_is_printed_only_on_the_failure_path():
    """⛔ 통과할 때도 찍으면 매번 뜨는 소음이 된다."""
    src = DEPLOY.read_text(encoding="utf-8")
    i_fail = src.index('print(f"  [점검] !! 문제')
    i_ok = src.index('print("  [점검] 통과")')
    i_notice = src.index("for line in recent_edits_notice():")
    assert i_ok < i_fail < i_notice, "실패 분기 안에 있어야 한다"


def test_it_is_printed_before_the_skip_hatch():
    """⚠️ `--skip-preflight` 안내보다 **앞**에 와야 읽힌다 — 뒤에 두면
    사람이 우회 문구부터 보고 그대로 넘긴다."""
    src = DEPLOY.read_text(encoding="utf-8")
    i_notice = src.index("for line in recent_edits_notice():")
    i_skip = src.index('--skip-preflight 로 무시하고 보냅니다')
    assert i_notice < i_skip


def test_the_helper_swallows_its_own_failure():
    """⚠️ 보조 설명이 배포를 세우면 안 된다."""
    src = DEPLOY.read_text(encoding="utf-8")
    body = src[src.index("def recent_edits_notice"):src.index("def untracked_notice")]
    assert "except Exception" in body
    assert "raise" not in body


def test_console_encoding_is_handled():
    """⚠️ 콘솔이 cp949 다 — 진단이 죽으면 아무도 못 본다 (CLAUDE.md)."""
    src = DEPLOY.read_text(encoding="utf-8")
    start = src.index("for line in recent_edits_notice():")
    assert "cp949" in src[start:start + 300]


def test_it_does_not_block():
    src = DEPLOY.read_text(encoding="utf-8")
    start = src.index("for line in recent_edits_notice():")
    block = src[start:start + 300]
    assert "sys.exit" not in block


def test_git_absence_does_not_break_it(tmp_path):
    """⚠️ git 이 없어도 mtime 은 말할 수 있다 — 상태 표시만 빈다."""
    (tmp_path / "app" / "core").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    touch(tmp_path, "app/core/x.py", seconds_ago=10)
    rows = recently_touched(tmp_path)
    assert [r[0] for r in rows] == ["app/core/x.py"]


# ---------------------------------------------------------------------------
# 관문이 **통과**할 때 — 가장 위험한 자리
# ---------------------------------------------------------------------------
#
# ⛔ `[편집중]` 은 실패 분기에만 있다. 그런데 2026-09-09 09:15 의 상태는
#    **구문 통과 · import 통과 · /health 200 인데 스위트 76건 실패** 였고,
#    원인은 20초 전에 생긴 `session_auth.py` 였다. 사람이 배포를 누르는 순간은
#    대개 관문이 통과할 때라, 그 자리에서 침묵하면 안 된다.
#
# ⚠️ **블록을 따로 만들지 않았다.** 미추적 경고가 이미 그 파일들을 보여주고
#    있고 빠진 것은 *언제* 뿐이다 — 블록을 더하면 같은 파일이 두 번 적히고,
#    두 번 적힌 것은 곧 한 번도 안 읽힌다.
#
# ⚠️ `M` 은 여기 넣지 않는다. 그날 상시 114~117개였고 사고를 낸 것은 없다.
#    `??` 는 두 번 뜨고 **두 번 다 사고**였다.

from app.core.deploy_preflight import format_untracked_notice  # noqa: E402


def test_a_freshly_created_untracked_source_says_when():
    """⛔ 09:15 에 보였어야 했던 그 한 줄이다."""
    body = "\n".join(format_untracked_notice(
        [("app/core/session_auth.py", 78)], ages={"app/core/session_auth.py": 20}))
    assert "20초 전에 바뀜" in body
    assert "다른 세션이 편집 중일 수 있습니다" in body


def test_an_old_untracked_source_does_not():
    """⚠️ 며칠 된 미커밋 파일은 '편집 중' 이 아니다 — 매번 뜨면 죽는다."""
    body = "\n".join(format_untracked_notice(
        [("app/core/settled.py", 300)], ages={"app/core/settled.py": 99999}))
    assert "바뀜" not in body
    assert "편집 중일 수 있습니다" not in body


def test_the_warning_line_appears_only_when_something_is_fresh():
    rows = [("app/core/a.py", 10), ("app/core/b.py", 20)]
    stale = "\n".join(format_untracked_notice(rows, ages={"app/core/a.py": 99999}))
    fresh = "\n".join(format_untracked_notice(rows, ages={"app/core/a.py": 30}))
    assert "편집 중일 수 있습니다" not in stale
    assert "편집 중일 수 있습니다" in fresh


def test_without_ages_it_behaves_exactly_as_before():
    """⚠️ 호출부가 나이를 안 넘겨도 기존 문구 그대로여야 한다."""
    body = "\n".join(format_untracked_notice([("app/core/x.py", 5)]))
    assert "app/core/x.py  5줄" in body
    assert "바뀜" not in body


def test_the_deploy_script_passes_the_ages():
    """⛔ 만들어 놓고 배선하지 않으면 아무것도 안 지킨다 (이 저장소의 전례)."""
    src = DEPLOY.read_text(encoding="utf-8")
    assert "recently_touched(PROJ)" in src
    assert "format_untracked_notice(rows, ages)" in src


def test_there_is_no_second_block_listing_the_same_files():
    """⚠️ 성공 분기에 `[편집중]` 블록을 또 만들면 같은 파일이 두 번 적힌다."""
    src = DEPLOY.read_text(encoding="utf-8")
    assert src.count("recent_edits_notice()") == 2, "정의 1 + 실패분기 호출 1 뿐이어야 한다"
