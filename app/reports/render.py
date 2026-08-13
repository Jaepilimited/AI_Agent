# -*- coding: utf-8 -*-
"""payload + 템플릿 → HTML.

두 가지를 강제한다.

1. **서술 속 숫자는 슬롯으로만** 들어간다. `{{ derived.pnl.H1_26.sales | eok }}` 처럼.
   템플릿 본문에 숫자 리터럴이 남아 있으면 `lint_template()` 이 잡는다 —
   원본 파이프라인 README 의 "하드코딩된 값이 없어야 한다"를 사람 약속이 아니라 기계 규칙으로 만든 것.
2. **표·차트는 payload 에서 그린다.** 값을 옮겨 적는 경로 자체를 두지 않는다.

슬롯 문법:  {{ 경로 }} 또는 {{ 경로 | 필터 }}   (필터: eok, pct, num, pct0, raw)
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Tuple

import structlog

logger = structlog.get_logger(__name__)

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

_SLOT = re.compile(r"\{\{\s*([A-Za-z0-9_.\[\]]+)\s*(?:\|\s*([a-z0-9]+)\s*)?\}\}")


def _lookup(payload: Dict[str, Any], path: str) -> Any:
    cur: Any = payload
    for part in path.split("."):
        if part.endswith("]") and "[" in part:
            name, idx = part[:-1].split("[", 1)
            if name:
                cur = cur[name]
            cur = cur[int(idx)]
        else:
            cur = cur[part]
    return cur


def _fmt(val: Any, filt: str | None) -> str:
    if val is None:
        return "—"
    if filt == "eok":
        return f"{float(val):,.1f}억"
    if filt == "pct":
        return f"{float(val):.2f}%"
    if filt == "pct0":
        return f"{float(val):.0f}%"
    if filt == "num":
        return f"{float(val):,.0f}" if isinstance(val, (int, float)) else str(val)
    return str(val)


def lint_template(html: str, allow: List[str] | None = None) -> List[Tuple[int, str]]:
    """서술 텍스트에 남은 숫자 리터럴을 찾는다.

    반환: [(줄 번호, 문제가 된 줄)] — 비어 있어야 정상이다.
    """
    allow = allow or []
    # script/style 은 검사 대상이 아니다 (표·차트를 그리는 코드)
    stripped = re.sub(r"<(script|style)[^>]*>.*?</\1>", lambda m: "\n" * m.group(0).count("\n"),
                      html, flags=re.S)
    bad: List[Tuple[int, str]] = []
    for i, line in enumerate(stripped.splitlines(), 1):
        text = _SLOT.sub("", line)              # 슬롯은 제거
        text = re.sub(r"<[^>]*>", " ", text)    # 태그·속성 제거 (class 명의 숫자 등)
        text = re.sub(r"&[a-z]+;", " ", text)
        for tok in allow:
            text = text.replace(tok, " ")
        if re.search(r"\d", text):
            bad.append((i, line.strip()[:120]))
    return bad


def lint_script(html: str) -> str:
    """템플릿 안 자바스크립트의 **문법**을 본다. 문제 없으면 빈 문자열.

    ⛔ 이 검사가 없어서 오타 하나에 **보고서가 백지로 나갔다** (2026-08-13:
       `rest` 를 두 번 선언해 스크립트가 통째로 죽었다). 서버는 200 을 주고
       HTML 도 멀쩡히 저장되므로 **어디에서도 에러가 나지 않는다** — 사람이 열어
       보기 전까지 아무도 모른다. 표시가 코드에 있는 구조라 표시도 검사해야 한다.

    node 가 없으면 검사를 건너뛴다 (배포 서버에는 node 가 없을 수 있다).
    """
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if not node:
        return ""
    src = "\n".join(m.group(1) for m in
                    re.finditer(r"<script(?![^>]*type=)[^>]*>(.*?)</script>", html, re.S))
    if not src.strip():
        return ""
    fd, path = tempfile.mkstemp(suffix=".js")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(src)
        p = subprocess.run([node, "--check", path], capture_output=True, text=True, timeout=20)
        return "" if p.returncode == 0 else (p.stderr or p.stdout)[:400]
    except Exception:
        return ""
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def render(payload: Dict[str, Any], template_name: str, *,
           allow_literals: List[str] | None = None,
           strict: bool = True) -> str:
    path = os.path.join(TEMPLATE_DIR, template_name)
    with open(path, encoding="utf-8") as fh:
        html = fh.read()

    problems = lint_template(html, allow_literals)
    if problems and strict:
        lines = "\n".join(f"  {n}행: {t}" for n, t in problems[:15])
        raise ValueError(
            f"템플릿 서술에 하드코딩된 숫자가 {len(problems)}곳 있다. 슬롯으로 바꿔라:\n{lines}"
        )

    # 표시가 코드에 있는 구조라 코드도 검사한다. 문법이 깨지면 **에러 없이 백지**가 나간다
    js_err = lint_script(html)
    if js_err:
        logger.error("template_script_syntax_error", template=template_name, error=js_err)
        if strict:
            raise ValueError(f"템플릿 스크립트 문법 오류 — 보고서가 백지로 나간다:\n{js_err}")

    missing: List[str] = []

    def sub(m):
        try:
            return _fmt(_lookup(payload, m.group(1)), m.group(2))
        except (KeyError, IndexError, TypeError):
            missing.append(m.group(1))
            return f"<span class='missing'>[{m.group(1)}]</span>"

    out = _SLOT.sub(sub, html)
    if missing and strict:
        raise ValueError(f"payload 에 없는 슬롯: {sorted(set(missing))}")

    if "/*__PAYLOAD__*/" not in html:
        raise ValueError("템플릿에 /*__PAYLOAD__*/ 자리가 없다 — 표·차트를 그릴 수 없다")
    blob = json.dumps(payload, ensure_ascii=False, default=str)
    # 데이터 안의 </script> 가 스크립트 블록을 조기 종료시키는 것을 막는다
    blob = blob.replace("</", "<\\/")
    return out.replace("/*__PAYLOAD__*/", blob)


def write(payload: Dict[str, Any], template_name: str, out_path: str, **kw) -> str:
    html = render(payload, template_name, **kw)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(html)
    return out_path
