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


def test_flow_canvas_vis_options_no_css_var():
    """vis-network 는 <canvas> 에 그린다. canvas 2D 컨텍스트의 fillStyle 은 CSS
    커스텀 프로퍼티(`var(--...)`)를 해석하지 못해 조용히 무시되고 기본값(검정)으로
    남는다 — 실제로 한 번 이렇게 새서(엣지 라벨이 다크 모드에서 안 보임) 브라우저로
    확인 후 고쳤다. 이 검사는 **좁다**: vis 옵션(노드/엣지/Network 생성자) 구간만
    보고, `detail.innerHTML` 처럼 실제 DOM 에 꽂히는(정상 동작하는) `var(--...)` 는
    검사 대상에서 뺀다. 이 특정 실수(canvas 옵션에 CSS 변수 문자열)만 잡을 뿐,
    canvas/CSS 불일치 전체를 보증하지 않는다."""
    js = _read("app/frontend/chat.js")
    start = js.index("function loadFlowCanvas")
    end = js.index('.on("click"', start)
    vis_options_region = js[start:end]
    assert "var(--" not in vis_options_region
