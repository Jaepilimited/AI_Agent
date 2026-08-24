# -*- coding: utf-8 -*-
"""프론트/서버 짝이 어긋나면 탭이 **에러 없이** 빈 화면이 된다.

`@@` 목록이 두 벌이라 조용히 어긋났던 사고와 같은 부류라 같은 방식으로 막는다.
"""
import io
import os

from app.core import static_checks as SC


def _read(rel):
    with io.open(os.path.join(SC.ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def test_flow_tab_button_exists():
    assert 'data-tab="flow"' in _read("app/frontend/chat.html")


def test_flow_tab_content_container_exists():
    html = _read("app/frontend/chat.html")
    assert 'id="tab-flow"' in html
    assert 'id="flow-canvas"' in html


def test_flow_tab_is_wired_in_js():
    """버튼만 있고 로더가 없으면 눌러도 아무 일이 없다."""
    js = _read("app/frontend/chat.js")
    assert 'tab.dataset.tab === "flow"' in js
    assert "function loadFlowCanvas" in js


def test_js_uses_hierarchical_layout_not_physics():
    """⚠️ 흐름도는 좌→우 계층 배치다. 위키 그래프의 물리엔진을 그대로 쓰면
    노드가 뭉쳐서 흐름으로 읽히지 않는다."""
    js = _read("app/frontend/chat.js")
    assert "hierarchical" in js and "'LR'" in js
