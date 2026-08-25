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
    canvas/CSS 불일치 전체를 보증하지 않는다.

    ⚠️ 앵커는 **vis 옵션을 만드는 함수**다. 2026-08-25 에 `loadFlowCanvas` 가
       조회만 하고 그리기는 `renderFlowCanvas` 로 갈라지면서 이 검사가
       `ValueError: substring not found` 로 죽었다 — 검사가 실패한 게 아니라
       **검사를 못 한 것**이라, 왜 죽었는지 보이게 앵커 부재를 따로 단언한다.
    """
    js = _read("app/frontend/chat.js")
    assert "function renderFlowCanvas" in js, (
        "vis 옵션을 만드는 함수를 못 찾았다 — 이름이 바뀌었으면 이 앵커도 함께 옮길 것")
    start = js.index("function renderFlowCanvas")
    assert '.on("click"' in js[start:], (
        "renderFlowCanvas 뒤에 클릭 핸들러가 없다 — 구간을 다시 잡을 것")
    end = js.index('.on("click"', start)
    vis_options_region = js[start:end]
    assert "var(--" not in vis_options_region
