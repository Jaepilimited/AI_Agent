# -*- coding: utf-8 -*-
"""자산 캐시 지문 회귀 — 손으로 세던 `?v=` 를 대신한다 (2026-08-31).

**왜 바꿨나 (실측):** 정적 파일은 2026-03-17 부터 `no-store` 로 나가고 있었고
`?v=` 규칙은 그 **뒤인** 2026-04-08 에 문서에 들어왔다. 브라우저 실측에서
새로고침 한 번에 자기 자산 8개가 전부 네트워크로 다시 왔다(디스크 캐시 0건, 776KB).
번호를 올리든 말든 아무것도 달라지지 않았다.

⛔ **여기서 가장 나쁜 실패는 "영영 낡은 파일에 갇히는 것"** 이다. 캐시를 켜는 순간
   그 위험이 생기므로, 아래 두 방향을 **함께** 지킨다:
     · 지문이 맞을 때만 오래 캐시한다
     · 지문이 안 맞으면 절대 캐시하지 않는다 (낡은 URL 은 굳지 않는다)
"""
from __future__ import annotations

import io
import os

import pytest

from app.core import asset_version as av

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_stamp_follows_content_not_name(tmp_path, monkeypatch):
    """내용이 바뀌면 지문이 바뀌고, 그대로면 그대로다."""
    base = tmp_path / "frontend"
    base.mkdir()
    target = base / "x.js"
    target.write_text("one", encoding="utf-8")
    monkeypatch.setitem(av._MOUNTS, "/frontend/", base)
    av._CACHE.clear()

    first = av.stamp("/frontend/x.js")
    assert first != av.boot_token()
    assert av.stamp("/frontend/x.js") == first, "안 바뀐 파일의 지문이 흔들린다"

    target.write_text("two", encoding="utf-8")
    os.utime(target, (0, 0))  # mtime 도 함께 바꿔 캐시 무효화를 확인
    assert av.stamp("/frontend/x.js") != first, "내용이 바뀌었는데 지문이 그대로다"


def test_missing_or_escaping_paths_fall_back_to_boot_token(tmp_path, monkeypatch):
    """⚠️ 버전 **없이** 내보내는 경로는 두지 않는다 — 그때만 조용히 낡는다.

    기동 토큰은 배포(재기동)마다 바뀌므로 최악이라도 '한 번은 다시 받는다'.
    """
    base = tmp_path / "frontend"
    base.mkdir()
    monkeypatch.setitem(av._MOUNTS, "/frontend/", base)
    av._CACHE.clear()

    assert av.stamp("/frontend/nope.js") == av.boot_token()
    assert av.stamp("/frontend/../../.env") == av.boot_token(), "마운트 밖으로 새 나갔다"
    assert av.stamp("/somewhere/else.js") == av.boot_token()


def test_only_the_current_stamp_is_treated_as_fresh():
    """⛔ 이 판정이 안전장치다 — 참일 때만 1년 캐시를 준다."""
    current = av.stamp("/frontend/chat.js")
    assert av.matches("/frontend/chat.js", current)
    assert not av.matches("/frontend/chat.js", "271"), "옛 번호가 최신으로 통과했다"
    assert not av.matches("/frontend/chat.js", "")
    assert not av.matches("/frontend/chat.js", "deadbeef")


def test_rewrite_stamps_our_assets_and_leaves_others_alone():
    html = ('<link href="/static/style.css">'
            '<script src="/frontend/chat.js?v=271"></script>'
            '<script src="https://cdn.example.com/lib.js?v=1"></script>')
    out = av.rewrite_html(html)
    assert f'/static/style.css?v={av.stamp("/static/style.css")}' in out
    assert f'/frontend/chat.js?v={av.stamp("/frontend/chat.js")}' in out
    assert "?v=271" not in out, "손으로 적힌 옛 번호가 살아남았다"
    assert "https://cdn.example.com/lib.js?v=1" in out, "외부 주소를 건드렸다"


def test_rewrite_does_not_treat_json_suffix_as_javascript():
    """`.json` 안의 `.js`를 잡으면 대시보드 카탈로그 URL이 404로 훼손된다."""
    html = 'fetch("/static/dashboard-catalog.json?v=1")'

    assert av.rewrite_html(html) == html


@pytest.mark.parametrize("path,root,expected", [
    # Starlette 1.x — path 가 이미 전체 경로다
    ("/frontend/chat.js", "/frontend", "/frontend/chat.js"),
    # 예전 동작 — path 는 나머지, root_path 에 접두
    ("/chat.js", "/frontend", "/frontend/chat.js"),
    ("/static/style.css", "/static", "/static/style.css"),
])
def test_mount_path_is_not_doubled(path, root, expected):
    """⚠️ 이어 붙이면 `/frontend/frontend/chat.js` 가 되고, 그때 나는 것은 에러가
    아니라 **캐시가 조용히 안 켜지는 것**이다 (실측으로 잡았다)."""
    assert av.normalize_path(path, root) == expected


def test_parse_version_reads_only_v():
    assert av.parse_version("v=abc123") == "abc123"
    assert av.parse_version("x=1&v=abc&y=2") == "abc"
    assert av.parse_version("") == ""
    assert av.parse_version("version=abc") == ""


def test_served_html_has_no_handwritten_numbers_left():
    """소스에 숫자가 남아 있으면 다음 사람이 그걸 보고 또 올린다."""
    import re

    for rel in ("app/frontend/chat.html", "app/frontend/login.html",
                "app/frontend/eval_review.html", "app/static/coa_finder.html"):
        path = os.path.join(REPO, rel)
        if not os.path.exists(path):
            continue
        with io.open(path, encoding="utf-8") as fh:
            html = fh.read()
        hand = re.findall(r"/(?:frontend|static)/[\w./-]+\.(?:js|css)\?v=(\d+)", html)
        assert not hand, f"{rel} 에 손으로 적은 캐시 번호: {hand}"
