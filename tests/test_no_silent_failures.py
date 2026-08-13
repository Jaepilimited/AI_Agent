# -*- coding: utf-8 -*-
"""조용한 실패 탐지 — **에러가 나지 않는 고장**을 찾는다.

이 시스템에서 발견이 늦는 결함은 예외를 던지지 않는다. 없는 CSS 변수는 폴백으로
넘어가고, 템플릿 스크립트가 깨지면 서버는 200 을 주며, 프론트와 서버의 `@@` 목록이
어긋나도 질문만 조용히 오염된다. **사람이 눈으로 볼 때까지 아무도 모른다.**

그래서 여기 있는 검사들은 전부 "정상 동작처럼 보이는 상태"를 겨냥한다.
조회도 LLM 호출도 하지 않는다.
"""
import glob
import io
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel: str) -> str:
    with io.open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as fh:
        return fh.read()


# ── 1) 정의되지 않은 CSS 변수 ────────────────────────────────────────────────

def test_no_undefined_css_variables():
    """⛔ 없는 변수는 **에러가 아니라 폴백**이라 오타를 아무도 못 잡는다.

    실제 사고: 피드백 입력창이 `var(--panel,#1e1e1e)`·`var(--input-bg,#111)` 을 썼는데
    두 변수가 존재한 적이 없어 폴백(어두운 색)이 테마와 무관하게 늘 먹었다. 글자색만
    `--text` 로 테마를 따라가 **라이트 모드에서 어두운 배경에 어두운 글자**가 됐고,
    사용자가 쓴 글이 보이지 않았다 (2026-08-13 사용자 제보).
    """
    css = _read("app/static/style.css")
    defined = set(re.findall(r"(--[a-zA-Z0-9_-]+)\s*:", css))

    targets = (glob.glob(os.path.join(ROOT, "app/static/*.css"))
               + glob.glob(os.path.join(ROOT, "app/static/*.js"))
               + glob.glob(os.path.join(ROOT, "app/frontend/*.html"))
               + glob.glob(os.path.join(ROOT, "app/frontend/*.js")))
    bad = []
    for path in targets:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            txt = fh.read()
        local = set(re.findall(r"(--[a-zA-Z0-9_-]+)\s*:", txt))
        for i, line in enumerate(txt.splitlines(), 1):
            for m in re.finditer(r"var\(\s*(--[a-zA-Z0-9_-]+)\s*[,)]", line):
                if m.group(1) not in defined and m.group(1) not in local:
                    bad.append(f"{os.path.basename(path)}:{i} {m.group(1)}")
    assert not bad, (
        "정의되지 않은 CSS 변수 참조 — 폴백이 조용히 먹는다. "
        "실제 토큰은 style.css 의 html.dark/html.light 블록에 있다:\n  "
        + "\n  ".join(bad[:20]))


# ── 2) 템플릿 스크립트 문법 ─────────────────────────────────────────────────

def test_report_templates_have_valid_script():
    """⛔ 문법이 깨지면 **에러 없이 백지**가 나간다 (서버 200, HTML 정상 저장).

    실제 사고: `rest` 를 두 번 선언해 스크립트가 통째로 죽고 보고서가 백지로 저장됐다.
    node 가 없는 환경에서는 검사를 건너뛴다.
    """
    import shutil

    from app.reports import render

    if not shutil.which("node"):
        pytest.skip("node 없음 — 문법 검사 건너뜀")
    for path in glob.glob(os.path.join(ROOT, "app/reports/templates/*.html")):
        with io.open(path, encoding="utf-8") as fh:
            err = render.lint_script(fh.read())
        assert not err, f"{os.path.basename(path)} 스크립트 문법 오류:\n{err}"


# ── 3) `@@` 데이터소스 — 프론트와 서버가 같은 목록을 보는가 ──────────────────

def _front_source_keys() -> set:
    """`chat.js` 의 SOURCE_GROUPS[*].keys — 사용자가 실제로 고를 수 있는 @@ 목록."""
    js = _read("app/frontend/chat.js")
    m = re.search(r"var SOURCE_GROUPS = \[(.*?)\n  \];", js, re.S)
    assert m, "SOURCE_GROUPS 구조가 바뀌었다 — 이 검사를 다시 맞춰라"
    keys = set()
    for block in re.finditer(r"keys:\s*\[(.*?)\]", m.group(1), re.S):
        keys |= set(re.findall(r'"([^"]+)"', block.group(1)))
    return keys


def test_at_sources_front_and_server_agree():
    """⛔ `@@` 는 프론트와 서버가 **각자** 파싱한다 — 목록이 어긋나면 조용히 샌다.

    실제 사고: 프론트 칩은 `Google Workspace` 인데 서버 별칭에는 그 문자열이 없어
    `@@Google Workspace 오늘 일정` 이 소스는 맞게 잡히면서도 질문이
    **"Workspace 오늘 일정"** 으로 오염됐다 (2026-08-13).
    """
    from app.agents.orchestrator import OrchestratorAgent

    known = set()
    for e in OrchestratorAgent._DB_REGISTRY:
        known.add(e["key"].lower())
        known |= {a.lower() for a in (e.get("aliases") or [])}

    missing = sorted(k for k in _front_source_keys() if k.lower() not in known)
    assert not missing, (
        f"프론트 @@ 목록에 있는데 서버가 모르는 키: {missing} — "
        "orchestrator._DB_REGISTRY 의 key 나 aliases 에 추가하라")


@pytest.mark.parametrize("key", sorted(_front_source_keys()))
def test_every_front_source_parses_cleanly(key):
    """고른 소스가 질문에서 **완전히** 걷혀야 한다 — 부스러기가 남으면 질문이 바뀐다."""
    from app.agents.orchestrator import OrchestratorAgent

    entry, clean = OrchestratorAgent.parse_db_prefix(f"@@{key} 매출 알려줘")
    assert entry, f"@@{key} 를 서버가 인식하지 못한다"
    assert clean.strip() == "매출 알려줘", f"@@{key} 파싱 후 질문이 오염됐다: {clean!r}"


# ── 4) 캐시 버전 — 문서와 실제가 어긋나는가 ─────────────────────────────────

def test_cache_version_doc_matches_reality():
    """CSS/JS 를 고치고 `?v=` 를 안 올리면 사용자는 **옛 파일을 계속 본다.**

    올렸는지 정적으로는 알 수 없지만, CLAUDE.md 에 적어 둔 현재 버전과 실제가
    어긋나는 것은 잡을 수 있다 — 실제로 한 번 어긋나 있었다 (2026-08-13).
    """
    html = _read("app/frontend/chat.html")
    real = dict(re.findall(r"(style\.css|chat\.js)\?v=(\d+)", html))
    doc = re.search(r"현재: style\.css\?v=(\d+), chat\.js\?v=(\d+)", _read("CLAUDE.md"))
    assert doc, "CLAUDE.md 의 캐시 버전 줄을 못 찾았다"
    assert (real.get("style.css"), real.get("chat.js")) == (doc.group(1), doc.group(2)), (
        f"chat.html 은 style={real.get('style.css')} chat={real.get('chat.js')} 인데 "
        f"CLAUDE.md 는 style={doc.group(1)} chat={doc.group(2)} 로 적혀 있다")
