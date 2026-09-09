# -*- coding: utf-8 -*-
"""Admin 아키텍처 캔버스가 그리는 **관문 순서**가 코드와 같은가 — 2026-09-09.

⛔ **왜 생겼나.** `static_flow_spec` 은 라우트만 봤다 — 노드가 가리키는 함수가
   있는가, 코드가 낼 수 있는 라우트가 캔버스에 있는가. **관문은 안 봤다.**
   실측: 분류기 앞에서 도는 관문 12개 중 **6개가 캔버스에 아예 없었고**
   (노션 저장·대시보드 링크·팀 담당국가·되묻기·회사사실·성분·유통기한),
   남은 것들은 **코드와 다른 순서**로 그려지고 있었다. 그런데 검사는 매일
   "일치" 라고 답했다 — 그림이 낡은 줄 아무도 몰랐다.

⛔ **순서가 뜻을 갖는다.** 위에 있는 관문이 먼저 잡으면 아래는 못 본다:
     · 유통기한이 재고보다 위여야 한다 (둘 다 '재고' 를 신호로 쓴다)
     · 노션 저장이 `@@` 파싱보다 위여야 한다 (`@@물류` 를 켠 채로도 저장돼야 한다)
   그림의 순서가 코드와 다르면 캔버스가 **조용히 거짓말을 한다.**
"""
import pytest

from app.core.static_checks import flow_gates_match_code
from app.flow import spec


def test_the_canvas_matches_the_code_today():
    ok, message = flow_gates_match_code()
    assert ok, message


def test_every_gate_declares_where_it_is_called():
    """⛔ `fn`(결정 함수)과 호출부는 다를 수 있다 — 보고서는 `wants_report` 로
    판정하지만 orchestrator 는 `_handle_report` 를 부른다. 둘을 따로 둔다."""
    gates = [n for n in spec.NODES if n.id.startswith("intercept.") or n.id == "alias_expand"]
    assert gates, "관문 노드가 하나도 없다"
    for node in gates:
        assert node.gate, f"{node.id} 에 호출부 표식이 없다 — 검사가 못 찾는다"


@pytest.mark.parametrize("earlier,later", [
    ("intercept.expiry", "intercept.inventory"),      # 유통기한이 재고보다 먼저
    ("intercept.notion_save", "at_parse"),            # 저장이 @@ 파싱보다 먼저
    ("alias_expand", "at_parse"),                     # 은어 보정이 파싱보다 먼저
])
def test_orders_that_carry_meaning(earlier, later):
    """⚠️ 이 셋은 뒤집히면 **에러가 아니라 오답**이 난다."""
    from app.core.static_checks import _declared_gate_chain

    ids = [n.id for n in spec.NODES]
    chain = _declared_gate_chain(spec)
    seq = chain if (earlier in chain and later in chain) else ids
    assert seq.index(earlier) < seq.index(later), f"{earlier} 가 {later} 보다 뒤에 있다"


def test_a_reordered_canvas_is_caught(monkeypatch):
    """⛔ 검사가 실제로 **잡는지** 본다. 통과만 확인하면 죽은 검사와 구분이 안 된다."""
    swapped = list(spec.EDGES)
    for i, e in enumerate(swapped):
        if e.src == "intercept.expiry" and e.label == "통과":
            swapped[i] = spec.Edge(e.src, "intercept.awards", label=e.label,
                                   conditional=e.conditional)
    monkeypatch.setattr(spec, "EDGES", tuple(swapped))
    ok, message = flow_gates_match_code()
    assert not ok, "캔버스 순서를 바꿨는데 통과했다 — 검사가 죽어 있다"


def test_a_new_undeclared_gate_is_caught():
    """⛔ **이게 2026-09-09 에 실제로 있던 상태다** — 관문이 코드에 생겼는데
    캔버스에 선언되지 않았고, 검사는 매일 "일치" 라고 답했다.

    합성 소스로 판정 기계를 직접 돌린다 — orchestrator 를 건드리지 않고도
    "새 관문이 생기면 잡히는가" 를 증명할 수 있다.
    """
    from app.core.static_checks import _early_return_gates

    src = "\n".join([
        "class A:",
        "    async def route_and_execute(self):",
        "        _new = brand_new_gate(query)",
        "        if _new:",
        "            return _new",
        "        return self._keyword_classify_ex(query)",
    ])
    blocks = _early_return_gates(src, "route_and_execute", "_keyword_classify_ex")
    assert blocks, "새 관문을 못 찾았다"
    assert any("brand_new_gate" in chunk for _s, _e, chunk in blocks), \
        "조건 변수를 만든 줄을 함께 싣지 않는다 — 무엇이 관문인지 알 수 없다"
    declared = {n.gate for n in spec.NODES if n.gate}
    assert not any(tok in blocks[0][2] for g in declared for tok in g.split("|")), \
        "합성 관문이 우연히 기존 선언에 걸렸다"


@pytest.mark.parametrize("gate_id", ["intercept.expiry", "intercept.inventory",
                                     "intercept.report", "intercept.images"])
def test_deleting_a_declared_gate_is_caught(monkeypatch, gate_id):
    """⚠️ **완전하지 않다.** 실측 15개 중 12개를 잡는다 — 못 잡는 셋은 그 블록이
    다른 관문이 덮은 바깥 `if` 안에 있어서다. 여기서는 잡히는 것만 고정한다."""
    kept = tuple(n for n in spec.NODES if n.id != gate_id)
    monkeypatch.setattr(spec, "NODES", kept)
    ok, _message = flow_gates_match_code()
    assert not ok, f"{gate_id} 를 뺐는데 통과했다"


def test_it_is_registered_in_both_mirror_lists():
    """⚠️ 거울 목록 두 벌은 양쪽 다 대조해야 한다 — 한쪽만 있으면 서버에서 안 돈다."""
    from app.core import static_checks as SC
    from app.core.self_check import CHECKS

    assert any(c[0] == "static_flow_gates" for c in SC.ALL)
    assert any(c.id == "static_flow_gates" for c in CHECKS)
