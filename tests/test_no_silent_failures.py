# -*- coding: utf-8 -*-
"""조용한 실패 탐지 — **에러가 나지 않는 고장**을 찾는다.

이 시스템에서 발견이 늦는 결함은 예외를 던지지 않는다. 없는 CSS 변수는 폴백으로
넘어가고, 템플릿 스크립트가 깨지면 서버는 200 을 주며, 프론트와 서버의 `@@` 목록이
어긋나도 질문만 조용히 오염된다. **사람이 눈으로 볼 때까지 아무도 모른다.**

⛔ **판정 로직은 여기 없다** — `app/core/static_checks.py` 한 곳에 있고, 서버의
   자가 점검(매일 07:30)도 **같은 함수**를 부른다. 서버에는 pytest 도 tests/ 도 없어서
   테스트만으로는 매일 돌 수 없다. 같은 규칙을 두 번 구현하면 한쪽만 고쳐진다.
"""
import glob
import io
import os

import pytest

from app.core import static_checks as SC

ROOT = SC.ROOT


@pytest.mark.parametrize("check_id,fn,label",
                         SC.ALL, ids=[c[0] for c in SC.ALL])
def test_static_check(check_id, fn, label):
    ok, detail = fn()
    assert ok, f"{label}: {detail}"


def test_handwritten_value_list_detector_catches_the_real_incident():
    """⛔ 2026-08-24 실제 오답 — 자동 주입 목록과 손으로 적은 목록이 갈렸다.

    `{{VALUES:Continent1}}` 에는 실측(…중남미…)이 채워져 있었는데, 규칙 본문에
    옛 목록(남미·중미)이 ✅ 표시와 함께 남아 있었다. LLM 은 **손으로 적힌 쪽**을 믿고
    0건을 냈고, 이어서 그 목록을 근거로 인용하며 "남미·중미 값은 정상 존재하므로
    데이터가 없는 것" 이라고 단정했다 — 조회도 설명도 틀렸다.
    """
    bad = ("    - ✅ **Continent1 사용** (광역): `'유럽'`, `'아시아'`, "
           "`'북미'`, `'남미'`, `'중미'`, `'중동'`,")
    assert not SC._SQL_OPERATOR.search(bad), "이 줄은 쿼리 예시가 아니라 문서화다"
    assert not SC._NEGATIVE_CONTEXT.search(bad)
    assert SC._mentions_column(bad, "Continent1")
    assert len(SC._QUOTED.findall(bad)) >= SC._ENUMERATION_MIN


def test_value_list_detector_ignores_sql_examples():
    """⚠️ 오탐 방지 — 쿼리 예시는 값을 **문서화**한 게 아니다.

    이걸 잡으면 프롬프트의 CASE WHEN 예시 20여 줄이 전부 경보가 되고,
    경보가 소음이 되면 아무도 안 본다.
    """
    example = "  - ✅ `CASE WHEN Country IN ('중국', '대만', '홍콩') THEN '중화권'`"
    assert SC._SQL_OPERATOR.search(example)


def test_value_list_detector_uses_word_boundaries():
    """⚠️ `Category` 가 `SM_Main_Category` 안에서 잡히면 안 된다.

    이 프로젝트에서 `'라인'`⊂`'가이드라인'`, `'환율'`⊂`'전환율'` 로 이미 겪은 부류다.
    """
    assert SC._mentions_column("| Category | STRING |", "Category")
    assert not SC._mentions_column("| SM_Main_Category | STRING |", "Category")


def test_value_list_detector_targets_only_autofilled_columns():
    """대상 컬럼을 손으로 들고 있으면 검사 자체가 낡는다 — 프롬프트에서 읽어야 한다."""
    cols = SC._autofilled_columns("… {{VALUES:Continent1}} … {{VALUES:Team_NEW}} …")
    assert cols == ["Continent1", "Team_NEW"]
    assert SC._autofilled_columns("자리표시자 없음") == []


def test_report_templates_have_valid_script():
    """⛔ 문법이 깨지면 **에러 없이 백지**가 나간다 (서버 200, HTML 정상 저장).

    node 가 필요해 자가 점검에는 넣지 못했다 — 서버에 node 가 없다. 개발 중에만 돈다.
    """
    import shutil

    from app.reports import render

    if not shutil.which("node"):
        pytest.skip("node 없음 — 문법 검사 건너뜀")
    for path in glob.glob(os.path.join(ROOT, "app/reports/templates/*.html")):
        with io.open(path, encoding="utf-8") as fh:
            err = render.lint_script(fh.read())
        assert not err, f"{os.path.basename(path)} 스크립트 문법 오류: {err}"


@pytest.mark.parametrize("key", sorted(SC.front_source_keys()))
def test_every_front_source_parses_cleanly(key):
    """고른 소스가 질문에서 **완전히** 걷혀야 한다 — 부스러기가 남으면 질문이 바뀐다."""
    from app.agents.orchestrator import OrchestratorAgent

    entry, clean = OrchestratorAgent.parse_db_prefix(f"@@{key} 매출 알려줘")
    assert entry, f"@@{key} 를 서버가 인식하지 못한다"
    assert clean.strip() == "매출 알려줘", f"@@{key} 파싱 후 질문이 오염됐다: {clean!r}"


# ── 채팅 답변 수치 검증 (2026-08-13) ────────────────────────────────────────
# 보고서는 이 방어선을 갖고 있었지만 채팅에는 없었다. 지금은 계측만 한다.

_ROWS = [{"country": "미국", "rev": 115360000000.0},
         {"country": "일본", "rev": 25650000000.0},
         {"country": "인도네시아", "rev": 51830000000.0}]


@pytest.mark.parametrize("answer", [
    "미국 1,153.6억, 일본 256.5억입니다.",          # 행 값 (억 표기)
    "미국 115,360,000,000원입니다.",                # 행 값 (원 표기)
    "전체 합계는 1,928.4억입니다.",                  # 열 합계
    "미국이 전체의 59.8%를 차지합니다.",              # 비중
    "일본은 미국 대비 -77.8% 입니다.",               # 증감률
    "2026년 상위 3개 국가입니다.",                   # 연도·작은 정수
])
def test_answer_check_accepts_derivable_numbers(answer):
    """⚠️ 정상 답변을 미검증으로 잡으면 경보가 무의미해진다 — 단위 변환이 특히 중요하다."""
    from app.core.answer_check import verify
    res = verify(answer, _ROWS, "2026 상반기 국가별 매출")
    assert not res["unverified"], f"정상 수치를 미검증으로 잡았다: {res['unverified']}"


@pytest.mark.parametrize("answer,bad", [
    ("미국 1,153.6억이며 광고비는 412.7억입니다.", "412.7"),   # 조회에 없는 지표
    ("미국이 전체의 88.8%를 차지합니다.", "88.8"),             # 틀린 비중
    ("베트남은 88.3억입니다.", "88.3"),                       # 조회에 없는 행
])
def test_answer_check_flags_unexplainable_numbers(answer, bad):
    from app.core.answer_check import verify
    res = verify(answer, _ROWS, "2026 상반기 국가별 매출")
    assert bad in res["unverified"], f"{bad} 를 못 잡았다: {res}"


def test_llm_clients_share_generate_json_signature():
    """⛔ 클라이언트를 바꿔 끼우려면 **서명이 같아야** 한다.

    ClaudeClient.generate_json 에 `max_output_tokens` 가 없어서, 그걸 넘기는 호출부에서
    TypeError 가 나고 호출부의 except 가 삼켰다 — 보고서 해석 절이 Claude 에서만
    조용히 사라졌다 (2026-08-14 모델 비교에서 발견).
    """
    import inspect

    from app.core.llm import ClaudeClient, GeminiClient

    g = set(inspect.signature(GeminiClient.generate_json).parameters)
    c = set(inspect.signature(ClaudeClient.generate_json).parameters)
    assert g <= c, f"Claude 에 없는 인자: {sorted(g - c)} — 호출부가 조용히 실패한다"


# ── 질문 → 검색 키워드 (2026-08-14) ────────────────────────────────────────
# ⛔ 한국어는 교착어라 **조사를 떼고 나서** 불용어를 봐야 한다. 안 그러면 검색이
#    조용히 빗나간다 — 드라이브는 항상 0건이었고("구글드라이브에서 …" 통째로 검색),
#    위키는 `매출이` 로 LIKE 를 걸어 "매출" 문서를 못 찾았다.

@pytest.mark.parametrize("question,expected", [
    ("구글드라이브에서 내가 작성한 신규 입사자 교안 자료 찾아줘", ["신규", "입사자", "교안"]),
    ("일본에서 매출이 왜 늘었는지 알려줘", ["일본", "매출", "늘었는지"]),
    ("보고서 파이프라인의 판정 계층은 뭐야", ["보고서", "파이프라인", "판정", "계층"]),
    # ⚠️ 원문 표기를 지켜야 한다 — 대문자가 뜻인 경우가 있다
    ("B2B 거래처의 첫 거래일", ["B2B", "거래처", "거래일"]),
    # 유형어만 남으면 키워드는 비어야 한다 (mimeType 필터가 대신한다)
    ("내 드라이브에 있는 사진 보여줘", []),
])
def test_query_keywords(question, expected):
    from app.core.query_keywords import extract
    drive_stop = {"드라이브", "구글드라이브", "구글", "사진", "작성한", "시트"}
    assert extract(question, extra_stop=drive_stop) == expected


def test_search_paths_share_one_extractor():
    """검색 경로마다 불용어를 다시 만들면 한 곳만 고쳐진다 — 실제로 그래서 두 곳이 깨졌다."""
    import inspect

    from app.agents import gws_agent
    from app.knowledge import wiki_search

    for mod in (gws_agent, wiki_search):
        src = inspect.getsource(mod)
        assert "query_keywords" in src, f"{mod.__name__} 이 공용 추출기를 쓰지 않는다"


def test_strip_particle_keeps_short_nouns():
    """⚠️ 조사를 떼다 낱말을 깎으면 안 된다 — '교안'의 '안', '자료'의 '료'."""
    from app.core.textmatch import strip_particle
    assert strip_particle("구글드라이브에서") == "구글드라이브"
    assert strip_particle("매출이") == "매출"
    assert strip_particle("교안") == "교안"
    assert strip_particle("자료") == "자료"


# ── 검색어 동의어 확장 (2026-08-14) ─────────────────────────────────────────
# Drive API 는 **낱말이 실제로 들어 있어야** 찾는다 (제미나이는 의미로 찾아서 됐다).
# 색인을 만들지 않고 검색 시점에 넓힌다 — 권한·신선도 문제가 없다.

def test_expand_swaps_one_word_at_a_time():
    from app.core.query_keywords import expand
    alts = expand(["매뉴얼", "배송"])
    assert alts, "표기 변형 후보가 나와야 한다"
    for a in alts:
        assert len(a) == 2, "길이는 유지된다 (한 낱말만 바꾼다)"
        diff = sum(1 for x, y in zip(a, ["매뉴얼", "배송"]) if x != y)
        assert diff == 1, f"한 번에 하나만 바꿔야 한다: {a}"


def test_seeds_contain_only_safe_variants():
    """⛔ 씨앗에 **뜻이 다른 말**을 넣지 마라 (2026-08-14 사용자 판단).

    실제로 {실적·성과·매출·결산} 을 동의어로 뒀었다 — "실적 자료" 를 찾는데
    "매출 시트" 가 나오고 그게 정답처럼 보인다. 뜻이 비슷할 뿐인 말은 LLM 확장이
    맡고, 씨앗에는 **표기 변형**만 남긴다.
    """
    from app.core.query_keywords import _SYNONYMS
    flat = {w for g in _SYNONYMS for w in g}
    for risky in ("매출", "실적", "성과", "결산", "교안", "온보딩"):
        assert risky not in flat, f"뜻이 다를 수 있는 말이 씨앗에 있다: {risky}"


def test_llm_expansion_is_the_growth_path():
    """새 용어는 **사람이 등록하지 않아도** 대응돼야 한다 — LLM 확장이 그 자리다."""
    import inspect

    from app.agents import gws_agent
    assert "llm_variants" in inspect.getsource(gws_agent)


def test_widened_search_is_disclosed():
    """⛔ 넓혀서 찾았으면 밝혀야 한다 — 안 밝히면 근사치가 정답처럼 보인다."""
    import inspect

    from app.agents import gws_agent
    src = inspect.getsource(gws_agent)
    assert "넓혀 찾음" in src, "확장 사실을 답변에 밝히는 문구가 없다"


def test_synonyms_come_from_the_shared_dictionary():
    """⛔ 동의어 목록을 코드에 또 만들지 마라 — 이미 DB 사전이 있다 (2026-08-14 지적).

    `term_aliases` 는 별칭→정식명칭 한 방향이지만, **같은 정식명칭을 가진 별칭들은
    서로 동의어**다. 새 용어는 관리자 화면에서 넣으면 코드 수정 없이 검색에 반영된다.
    """
    import inspect

    from app.core import query_keywords as QK

    src = inspect.getsource(QK.expand)
    assert "_alias_groups()" in src, "확장이 DB 사전을 보지 않는다"
    # 사전이 비어 있어도(테스트 환경) 씨앗만으로 동작해야 한다
    assert QK.expand(["매뉴얼"]), "씨앗 표기 변형조차 동작하지 않는다"


def test_particle_stripping_has_one_implementation():
    """조사 제거가 여러 벌이면 경로마다 다르게 잘린다 — 실제로 그래서 어긋났다."""
    import inspect

    from app.core import term_aliases

    src = inspect.getsource(term_aliases._strip_particle)
    assert "textmatch" in src, "term_aliases 가 자체 조사 제거를 다시 갖고 있다"


def test_empty_search_feeds_the_candidate_dictionary():
    """0건 질문의 미등록 용어는 **자동으로** 후보에 쌓여야 한다 (수동 등록 금지)."""
    import inspect

    from app.core import query_keywords as QK

    assert "collect_candidates" in inspect.getsource(QK.log_empty)


def test_flow_spec_check_catches_dangling_function_reference():
    """선언이 없는 함수를 가리키면 그림이 거짓말을 한다 — 반드시 잡혀야 한다."""
    from app.flow import spec
    from app.core import static_checks as SC

    bad = spec.Node("ghost", "유령", fn="app.agents.orchestrator._nope_not_here")
    original = spec.NODES
    spec.NODES = original + (bad,)
    try:
        ok, detail = SC.flow_spec_matches_code()
        assert ok is False
        assert "ghost" in detail
    finally:
        spec.NODES = original


def test_flow_spec_check_catches_missing_at_source_route():
    """`@@` 소스를 새로 붙였는데 캔버스에 노드가 없으면 잡아야 한다."""
    from app.agents.orchestrator import OrchestratorAgent
    from app.core import static_checks as SC

    original = OrchestratorAgent._DB_REGISTRY
    OrchestratorAgent._DB_REGISTRY = original + [
        {"key": "테스트소스", "aliases": [], "route": "nowhere",
         "group": "x", "icon": "chart", "label": "x", "desc": "x"}
    ]
    try:
        ok, detail = SC.flow_spec_matches_code()
        assert ok is False
        assert "nowhere" in detail
    finally:
        OrchestratorAgent._DB_REGISTRY = original


def test_every_static_check_is_registered_in_self_check():
    """`SC.ALL` 에 검사를 추가해도 `self_check.CHECKS` 는 자동으로 따라오지 않는다.

    ⛔ 2026-08-24 실제 발생 — `static_value_list_dupes` 가 `SC.ALL` 에 추가됐지만
       (같은 날 커밋 9f39cbf, "손으로 적은 낡은 값 목록을 LLM 이 믿고 0건을 '데이터
       없음'으로 오답한 사고" 방어) `self_check.CHECKS` 에는 등록되지 않았다. 그 결과
       이 방어선은 **pytest 에서만 돌고 서버 자가 점검(매일 07:30)에서는 죽어 있었다**
       — 그 상태로 이미 배포까지 됐다. `CHECKS` 가 `SC.ALL` 을 순회해 자동 생성되는
       줄 알았던 것 자체가 착각이었다(`_static(id)` 로 하나씩 손으로 매핑하는 구조).

    이 프로젝트가 반복해서 걸리는 "사본이 갈린다" 사고와 같은 종류다
    (direct 프롬프트 두 벌 / @@ 목록 두 벌 / Continent1 값 두 벌) — 손으로 유지하는
    거울 목록에 "둘이 일치하는가"를 보는 사람이 아무도 없었다. 이 테스트가 그 사람이다.
    """
    from app.core import self_check as SELF

    registered_ids = {c.id for c in SELF.CHECKS}
    missing = [check_id for check_id, _fn, _label in SC.ALL
               if check_id not in registered_ids]
    assert not missing, (
        f"self_check.CHECKS 에 등록되지 않은 정적 검사: {missing} — "
        "서버 자가 점검(매일 07:30)에서 돌지 않는다. self_check.py 의 static 카테고리에 "
        "Check(id, 'static', 심각도, 설명, _static(id)) 를 추가할 것"
    )

    # ⛔ **반대 방향도 봐야 한다** (2026-08-24 리뷰). `_static(name)` 은 `SC.ALL` 에
    #    없는 id 를 받으면 예전엔 `CheckResult(True, "검사 정의 없음 — 건너뜀")` 을
    #    돌려줬다 — id 에 오타가 나거나 `SC.ALL` 에서 함수가 빠지면 **그 검사는
    #    영원히 초록으로 통과한다.** 방어선이 사라진 것과 없는 것이 화면에서
    #    똑같이 보인다. 위 사고(dca36c3)의 정확히 반대이고, 이 브랜치가 양쪽
    #    거울에 항목을 더하면서 그 면적이 늘었다. 거울 목록은 양방향으로 대조한다.
    known_ids = {check_id for check_id, _fn, _label in SC.ALL}
    dangling = sorted(c.id for c in SELF.CHECKS
                      if c.category == "static" and c.id not in known_ids)
    assert not dangling, (
        f"static_checks.ALL 에 없는 id 를 self_check 가 등록했다: {dangling} — "
        "id 오타이거나 검사가 삭제된 것이다. 매일 07:30 에 '통과' 로 세어지고 있었다"
    )


# ── 관문이 `@@`/슬래시 선택을 둘 다 보는가 (2026-08-24) ────────────────────
# ⛔ `/보고서` 를 누르고 본문에 "보고서" 라고 안 적으면 **버튼이 무시됐다.**
#    관문은 `db_entry`(=`@@보고서` 타이핑)만 봤고, 슬래시·칩 선택은 `enabled_sources`
#    로만 온다. 관문이 놓치면 `@@` 단일소스 경로가 route="report" 를 확정하는데
#    `HANDLER_ROUTES` 에 report 가 없어 조용히 `_handle_direct` 로 강등된다 —
#    에러도, 안내도 없고, CLAUDE.md 가 약속한 "지정하면 문구를 보지 않는다"가 거짓이 된다.
#    아키텍처 캔버스를 만들다 흐름을 코드로 다시 읽으면서 드러났다.

@pytest.mark.parametrize("method", ["route_and_stream", "route_and_execute"])
def test_report_intercept_checks_both_selection_channels(method):
    """보고서 관문이 `db_entry` 와 `enabled_sources` 를 **둘 다** 보는가."""
    import inspect

    from app.agents.orchestrator import OrchestratorAgent

    src = inspect.getsource(getattr(OrchestratorAgent, method))
    head = src[:src.find("_rep_query") if "_rep_query" in src else len(src)]
    assert "_rep_selected" in src, f"{method} 에 보고서 관문이 없다"
    # 선택 경로 두 가지가 같은 판정에 들어와야 한다
    idx = src.find("_rep_selected")
    block = src[idx:idx + 400]
    assert 'route") == "report"' in block, "db_entry 경로를 안 본다"
    assert "enabled_sources" in block, (
        "enabled_sources 경로를 안 본다 — /보고서·칩 선택이 조용히 무시된다")


def test_both_intercepts_are_symmetric_about_enabled_sources():
    """⚠️ 초상권 관문은 처음부터 둘 다 봤다. 보고서만 한쪽이었다.

    관문이 늘어날 때 같은 비대칭이 생기지 않도록 **둘을 나란히** 검사한다.
    """
    import inspect

    from app.agents.orchestrator import OrchestratorAgent

    src = inspect.getsource(OrchestratorAgent.route_and_stream)
    for flag in ("_mr_selected", "_rep_selected"):
        i = src.find(flag)
        assert i > 0, f"{flag} 가 없다"
        assert "enabled_sources" in src[i:i + 400], (
            f"{flag} 가 enabled_sources 를 보지 않는다 — 선택 경로 하나가 새 나간다")


def test_feedback_inbox_defaults_to_all_not_unread_only():
    """⛔ 처리함 기본 필터가 '미확인' 이면 처리한 항목이 목록에서 사라진다.

    완료로 바꾸면 `loadFeedbackInbox()` 가 `status=new` 로 다시 불러오므로 방금 처리한
    행이 화면에서 지워진다. 에러도 안 나고 성공 표시도 없어서 **처리가 안 된 것처럼
    보인다** (2026-08-25 사용자 제보: "처리해도 처리됐다고 안나옴").
    이미 처리해 둔 39건도 기본 화면에서는 통째로 안 보였다.
    """
    from pathlib import Path

    html = (Path(__file__).resolve().parent.parent
            / "app" / "frontend" / "chat.html").read_text(encoding="utf-8")
    block = html[html.index('id="feedback-filter"'):]
    block = block[:block.index("</select>")]
    selected = [line for line in block.splitlines() if "selected" in line]
    assert len(selected) == 1, selected
    assert 'value=""' in selected[0], f"기본 필터가 전체가 아니다: {selected[0]}"
