# -*- coding: utf-8 -*-
"""정적 검사 — 코드·자산을 읽어 **에러가 나지 않는 고장**을 찾는다.

`tests/test_no_silent_failures.py`(개발 중 즉시 피드백)와 자가 점검(서버에서 매일)이
**같은 함수를 부른다.** 같은 규칙을 두 번 구현하면 한쪽만 고쳐지는 것이 이 프로젝트에서
반복된 실패라, 판정은 여기 한 곳에만 둔다.

서버에는 pytest 도 tests/ 도 node 도 없다 — 그래서 검사 본체는 **표준 라이브러리만**
쓰고, node 가 필요한 검사(템플릿 스크립트 문법)는 있을 때만 돈다.

각 함수는 `(ok, detail)` 을 돌려주고 **부작용이 없다** (자가 점검 규칙).
"""
from __future__ import annotations

import glob
import io
import os
import re
from typing import List, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(rel: str) -> str:
    with io.open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _exists(rel: str) -> bool:
    return os.path.exists(os.path.join(ROOT, rel))


# ── 1) 정의되지 않은 CSS 변수 ────────────────────────────────────────────────

def undefined_css_vars() -> Tuple[bool, str]:
    """없는 변수는 **에러가 아니라 폴백**이라 오타를 아무도 못 잡는다.

    피드백 입력창이 `--panel`·`--input-bg`(존재한 적 없는 이름)를 써서 라이트 모드에서
    어두운 배경에 어두운 글자가 됐던 사고를 잡는다 (2026-08-13 사용자 제보).
    """
    if not _exists("app/static/style.css"):
        return True, "style.css 없음 — 건너뜀"
    defined = set(re.findall(r"(--[a-zA-Z0-9_-]+)\s*:", _read("app/static/style.css")))
    bad: List[str] = []
    for pat in ("app/static/*.css", "app/static/*.js",
                "app/frontend/*.html", "app/frontend/*.js"):
        for path in glob.glob(os.path.join(ROOT, pat)):
            with io.open(path, encoding="utf-8", errors="replace") as fh:
                txt = fh.read()
            local = set(re.findall(r"(--[a-zA-Z0-9_-]+)\s*:", txt))
            for i, line in enumerate(txt.splitlines(), 1):
                for m in re.finditer(r"var\(\s*(--[a-zA-Z0-9_-]+)\s*[,)]", line):
                    if m.group(1) not in defined and m.group(1) not in local:
                        bad.append(f"{os.path.basename(path)}:{i} {m.group(1)}")
    return (not bad), ("정의되지 않은 변수 참조 " + ", ".join(bad[:6])
                       if bad else "미정의 참조 없음")


# ── 2) `@@` 데이터소스 — 프론트와 서버가 같은 목록을 보는가 ──────────────────

def front_source_keys() -> set:
    """서버가 단일 소스다 — 프론트는 `/api/datasources` 로 목록을 받는다 (2026-08-13).

    ⛔ 예전엔 `chat.js` 가 같은 목록을 하드코딩해 갖고 있었다. 그래서 서버만 고치면
       조용히 어긋났고, `@@Google Workspace` 질문 오염과 `초상권` 라우트 누락이
       거기서 나왔다. 지금은 프론트 키 = 서버 키다.
    """
    from app.agents.orchestrator import OrchestratorAgent
    return {e["key"] for e in OrchestratorAgent._DB_REGISTRY}


def _front_group_names() -> set:
    """`chat.js` 의 GROUP_BY_NAME — 서버 그룹 이름을 화면 그룹에 잇는 표."""
    if not _exists("app/frontend/chat.js"):
        return set()
    m = re.search(r"var GROUP_BY_NAME = \{(.*?)\};", _read("app/frontend/chat.js"), re.S)
    return set(re.findall(r'"([^"]+)"\s*:', m.group(1))) if m else set()


def at_source_parity() -> Tuple[bool, str]:
    """`@@` 목록이 화면까지 온전히 도달하는가.

    두 가지를 본다:
      1. **프론트가 목록을 다시 하드코딩하지 않았는가** — 사본이 생기면 또 어긋난다
      2. 서버 그룹이 전부 `GROUP_BY_NAME` 에 있는가 — 없는 그룹의 소스는 **화면에서
         통째로 사라진다** (에러 없이)
    그리고 모든 키가 `@@키 질문` 에서 **깨끗이 걷히는지** 확인한다.
    """
    if not _exists("app/frontend/chat.js"):
        return True, "chat.js 없음 — 건너뜀"
    js = _read("app/frontend/chat.js")

    i = js.find("var SOURCE_GROUPS = [")
    j = js.find("];", i) if i >= 0 else -1
    literal = js[i:j] if j > i else ""
    if re.search(r"keys:\s*\[\s*[\"']", literal):
        return False, "프론트가 @@ 목록을 다시 하드코딩했다 — /api/datasources 로 받아야 한다"

    from app.agents.orchestrator import OrchestratorAgent

    srv_groups = {e.get("group", "") for e in OrchestratorAgent._DB_REGISTRY}
    missing_groups = sorted(g for g in srv_groups if g and g not in _front_group_names())
    if missing_groups:
        return False, f"GROUP_BY_NAME 에 없는 서버 그룹 {missing_groups} — 화면에서 사라진다"

    dirty = []
    for e in OrchestratorAgent._DB_REGISTRY:
        entry, clean = OrchestratorAgent.parse_db_prefix(f"@@{e['key']} 매출 알려줘")
        if not entry or clean.strip() != "매출 알려줘":
            dirty.append(f"{e['key']}→{clean.strip()!r}")
    if dirty:
        return False, f"파싱 후 질문 오염 {dirty[:4]}"
    return True, f"@@ 소스 {len(srv_groups)}그룹 · 키 {len(OrchestratorAgent._DB_REGISTRY)}개 정상"


# ── 3) direct 프롬프트 단일 소스 ────────────────────────────────────────────

def prompt_single_source() -> Tuple[bool, str]:
    """프롬프트 사본이 늘면 경로에 따라 답이 갈린다.

    두 벌이던 시절 한쪽만 고쳐 *"보고서라는 별도 메뉴는 없습니다"* 라고 답했다.
    """
    if not _exists("app/agents/orchestrator.py"):
        return True, "건너뜀"
    src = _read("app/agents/orchestrator.py")
    n = src.count("당신은 Craver의 AI 어시스턴트입니다")
    return (n == 1), f"direct 프롬프트 사본 {n}개 (1이어야 정상)"


# ── 4) 캐시 버전 문서 ↔ 실제 ─────────────────────────────────────────────────

def cache_version_doc() -> Tuple[bool, str]:
    """문서가 한 칸 뒤처지면 다음 사람이 잘못된 번호에서 올린다 (실제로 그랬다)."""
    if not (_exists("app/frontend/chat.html") and _exists("CLAUDE.md")):
        return True, "파일 없음 — 건너뜀 (배포본에는 CLAUDE.md 가 없다)"
    real = dict(re.findall(r"(style\.css|chat\.js)\?v=(\d+)", _read("app/frontend/chat.html")))
    doc = re.search(r"현재: style\.css\?v=(\d+), chat\.js\?v=(\d+)", _read("CLAUDE.md"))
    if not doc:
        return True, "CLAUDE.md 에 버전 줄 없음 — 건너뜀"
    ok = (real.get("style.css"), real.get("chat.js")) == (doc.group(1), doc.group(2))
    return ok, (f"실제 style={real.get('style.css')} chat={real.get('chat.js')} / "
                f"문서 style={doc.group(1)} chat={doc.group(2)}")


# ── 5) 라우팅 키워드 삼킴 충돌 (경로가 갈리는 것만) ──────────────────────────

_ROUTE_OF = {
    "_DATA_KEYWORDS": "bigquery", "_BIZ_CONTEXT": "bigquery", "_FULLDATA_KEYWORDS": "bigquery",
    "_NOTION_KEYWORDS": "notion", "_TEAM_KEYWORDS": "notion", "_HOWTO_KEYWORDS": "notion",
    "_CS_KEYWORDS": "cs", "_GWS_KEYWORDS": "gws",
    "_SEARCH_KEYWORDS": "direct", "_EXTERNAL_KEYWORDS": "multi",
}
# 기준선 — **실제 질문으로 하나씩 돌려 확인한** 충돌만 여기 둔다 (2026-08-13).
# 충돌이 있어도 검사 순서·가드 덕분에 옳게 라우팅되면 문제가 아니다.
#   확인한 질문 예: "네이버 검색광고 성과"→bigquery · "다우오피스 사용법"→notion ·
#   "배송 얼마나 걸려?"→cs · "회의실 예약 어떻게 해"→notion · "팀별 자료 어디있어"→notion ·
#   "할인행사 효과 분석해줘"→multi · "이번달 일정 보여줘"→gws
# ⚠️ **새로 뜨는 것은 반드시 질문으로 확인하고 넣어라.** 그냥 추가하면 이 검사는
#    아무것도 지키지 않는 목록이 된다.
KNOWN_COLLISIONS = {
    ("환율", "전환율"),          # `_GUARDED` 로 경계 확인 — 전환율은 bigquery
    ("경쟁", "경쟁사"), ("경쟁", "경쟁사 순위"),
    ("경쟁사", "경쟁사 순위"),   # "경쟁사 순위"→multi. 데이터+외부 맥락이라 허용
    ("검색", "파일 검색"), ("검색", "네이버 검색광고"),
    ("메타", "메타 광고"), ("메타", "메타광고"),
    ("반품", "반품 정책"), ("반품", "반품정책"),   # _COMPOUND_NOTION 이 의도적으로 notion
    ("일정", "출시 일정"), ("오늘", "오늘 일정"), ("이번달", "이번달 일정"),
    ("성과", "성과금"), ("성과", "성과급"),
    ("스킨", "스킨케어"), ("스킨", "스킨1004"),
    ("다우", "다우오피스"), ("데이터", "데이터 허브"),
    ("얼마", "배송 얼마나"), ("정책", "정책변화"), ("팀별", "팀별 자료"),
    ("할인", "할인행사"), ("행사", "할인행사"), ("회의", "회의실 예약"),
}


def keyword_collisions() -> Tuple[bool, str]:
    """짧은 낱말이 **경로가 다른** 긴 낱말에 삼켜지는가.

    `라인` ⊂ `가이드라인`, `환율` ⊂ `전환율` 이 이 방식으로 드러났다. 같은 경로끼리는
    충돌해도 결과가 같으므로 세지 않는다. 검토가 끝난 것은 KNOWN_COLLISIONS 로 뺀다.
    """
    from app.agents.orchestrator import OrchestratorAgent as O

    o = O.__new__(O)
    kw = {}
    for name, route in _ROUTE_OF.items():
        for w in getattr(o, name, []) or []:
            kw.setdefault(w.lower(), set()).add(route)
    han = re.compile(r"[가-힣]")
    new = []
    for a, ra in kw.items():
        if len(a) < 2 or not han.search(a):
            continue
        for b, rb in kw.items():
            if a == b or a not in b or len(b) <= len(a) or (ra & rb):
                continue
            if (a, b) in KNOWN_COLLISIONS:
                continue
            new.append(f"'{a}'⊂'{b}'")
    return (not new), ("새 충돌 " + ", ".join(sorted(set(new))[:6]) if new
                       else "경로가 갈리는 새 충돌 없음")


def asset_sanity() -> Tuple[bool, str]:
    """프론트 자산이 비었거나 뭉텅 잘리지 않았는가.

    ⛔ 스크립트로 파일을 쓰다 실패하면 `open(w)` 가 **이미 자른 뒤**라 0바이트가 남는다.
       실제로 두 번 겪었다 (2026-07, 2026-08-13). 화면은 백지가 되고 서버는 200 을 준다.
    """
    MIN = {"app/frontend/chat.js": 150_000, "app/static/style.css": 50_000,
           "app/frontend/chat.html": 5_000}
    bad = []
    for rel, floor in MIN.items():
        if not _exists(rel):
            bad.append(f"{rel} 없음"); continue
        n = os.path.getsize(os.path.join(ROOT, rel))
        if n < floor:
            bad.append(f"{rel} {n}바이트 (최소 {floor})")
    return (not bad), ("; ".join(bad) if bad else "프론트 자산 크기 정상")


def fi_prompt_masking() -> Tuple[bool, str]:
    """권한 없는 사용자용 프롬프트에서 손익(FI) 섹션이 **실제로** 지워지는가.

    ⛔ 이 검사가 없으면 조용히 뚫린다. `_mask_fi_prompt()` 는 프롬프트의 제목
       (`## 테이블 14: FI_LLM_Flat …`)과 라우팅 표 행을 **정규식으로** 지운다.
       프롬프트에서 번호를 바꾸거나 제목을 손보면 정규식이 안 맞고, **에러 없이**
       FI 스키마가 권한 없는 사용자 프롬프트에 실린다.
       기존 테스트는 합성 픽스처만 봤다 — 실제 파일이 바뀌어도 통과한다.

    두 방향을 함께 본다:
      ① 원본에는 FI 가 **있어야** 한다 (없어졌으면 검사 자체가 무의미해진 것)
      ② 마스킹 후에는 FI 흔적이 **하나도 없어야** 한다
    """
    from app.agents.sql_agent import PROMPTS_DIR, _mask_fi_prompt

    path = PROMPTS_DIR / "sql_generator.txt"
    if not path.exists():
        return False, "sql_generator.txt 없음"
    raw = path.read_text(encoding="utf-8")

    if "FI_LLM_Flat" not in raw:
        return False, "원본 프롬프트에 FI_LLM_Flat 이 없다 — 마스킹 대상이 사라졌거나 이름이 바뀌었다"

    masked = _mask_fi_prompt(raw)
    # 표기 변형까지 본다 — 테이블명·데이터셋·필수 필터 컬럼
    # ⚠️ `Sales_Integration` 은 매출·제품 테이블의 데이터셋이기도 하다 — FI 표식이 아니다.
    #    (이 검사를 처음 켰을 때 그것 때문에 오탐이 났다)
    leaks = [tok for tok in ("FI_LLM_Flat", "Record_Type", "SGA_DETAIL")
             if tok in masked]
    if leaks:
        return False, f"마스킹 후에도 FI 흔적이 남았다: {', '.join(leaks)}"

    removed = len(raw) - len(masked)
    if removed < 500:
        return False, f"지워진 분량이 {removed}자뿐 — 섹션이 아니라 한 줄만 지워졌을 수 있다"
    return True, f"FI 섹션 {removed:,}자 제거 확인"


# ── 8) 자동 주입 컬럼의 값을 손으로 다시 나열했는가 ─────────────────────────

# 부정형 문장은 "이 값은 없다" 를 가르치는 정상 서술이라 열거로 세면 안 된다.
_NEGATIVE_CONTEXT = re.compile(r"없다|없음|아니다|금지|마라|오답|쓰지|틀린")

# SQL 예시(`WHERE Country IN ('중국','대만','홍콩')`)는 값을 **문서화**한 게 아니라
# 쿼리를 보여준 것이다. 낡아도 조용한 오답을 만들지 않으므로 열거로 세지 않는다.
# 위험한 것은 연산자 없이 "쓸 수 있는 값은 A, B, C" 라고 **적어 둔** 쪽이다.
_SQL_OPERATOR = re.compile(r"\b(IN|LIKE|WHEN|WHERE|AND|OR)\b|[!=<>]=?")

# 한 호흡에 인용부호 값이 이만큼 이상 나오면 '목록을 적은 것' 으로 본다.
_ENUMERATION_MIN = 3

_QUOTED = re.compile(r"[`'\"]([^`'\"\n]{1,20})[`'\"]")


# 확인을 마친 예외. ⚠️ **뜰 때마다 실제 줄을 읽고 넣어라** — 그냥 쌓으면
# `KNOWN_COLLISIONS` 와 같은 이유로 아무것도 안 지키는 목록이 된다.
# 줄 번호가 아니라 내용으로 건다 (줄 번호는 편집마다 어긋난다).
_KNOWN_HANDWRITTEN_OK = (
    # `Integrated_marketing_cost.Team` 값 목록이다. `Team_NEW` 는 "같은 코드 체계" 라고
    # **비교 언급**될 뿐, 이 줄이 문서화하는 컬럼이 아니다. 그 테이블 컬럼은 자동 주입
    # 대상이 아니라 대조할 실측 목록 자체가 없다 (2026-08-24 확인).
    "integrated_ad·Team_NEW 와",
)


def _autofilled_columns(prompt: str) -> List[str]:
    """`{{VALUES:X}}` 가 실제로 박혀 있는 컬럼만 대상으로 삼는다.

    ⚠️ 목록을 손으로 들고 있으면 이 검사 자체가 낡는다 — 검사가 막으려는 실패를
       검사가 저지르는 꼴이다. 프롬프트에서 직접 읽는다.
    """
    return sorted(set(re.findall(r"\{\{VALUES:([A-Za-z_0-9]+)\}\}", prompt)))


def _mentions_column(line: str, column: str) -> bool:
    """⚠️ 부분 문자열 매치 금지 — `Category` 가 `SM_Main_Category` 안에서 잡힌다.

    이 프로젝트에서 `'라인'`⊂`'가이드라인'`, `'환율'`⊂`'전환율'` 로 이미 겪은 부류다.
    """
    return re.search(rf"(?<![A-Za-z_0-9]){re.escape(column)}(?![A-Za-z_0-9])", line) is not None


def prompt_no_handwritten_value_lists() -> Tuple[bool, str]:
    """자동 주입되는 컬럼의 값 목록을 프롬프트 본문에 손으로 나열하지 않았는가.

    ⛔ 2026-08-24 실제 오답: `{{VALUES:Continent1}}` 로 실측 목록(…중남미…)을 넣어두고도
       규칙 본문에 옛 목록(남미·중미)이 ✅ 표시와 함께 남아 있었다. LLM 은 **손으로 적힌
       쪽**을 믿고 0건을 냈고, 이어서 그 목록을 근거로 인용하며 "남미·중미 값은 정상
       존재하므로 데이터가 없는 것" 이라고 단정했다 — 조회도 설명도 틀렸다.

    `value_lists.py` 가 없애려던 실패인데 **사본을 하나만 지워서** 살아남았다.
    이 검사는 "값 목록은 한 곳(자동 주입)에서만 온다" 를 강제한다.

    ⚠️ 부정형("Continent2 에는 '유럽'·'아시아' 가 없다")은 가르치는 문장이라 통과시킨다.
    """
    rel = "prompts/sql_generator.txt"
    if not _exists(rel):
        return True, "건너뜀 (프롬프트 없음)"
    prompt = _read(rel)
    columns = _autofilled_columns(prompt)
    if not columns:
        return True, "건너뜀 (자동 주입 컬럼 없음)"
    offenders: List[str] = []
    for lineno, line in enumerate(prompt.split("\n"), 1):
        if "{{VALUES:" in line or _NEGATIVE_CONTEXT.search(line):
            continue
        if _SQL_OPERATOR.search(line):      # 쿼리 예시는 문서화가 아니다
            continue
        if any(marker in line for marker in _KNOWN_HANDWRITTEN_OK):
            continue
        hit = next((c for c in columns if _mentions_column(line, c)), None)
        if not hit:
            continue
        if len(_QUOTED.findall(line)) >= _ENUMERATION_MIN:
            offenders.append(f"{rel}:{lineno} ({hit})")
    if offenders:
        return False, (
            f"자동 주입 컬럼의 값을 손으로 나열한 곳 {len(offenders)}건: "
            + ", ".join(offenders[:4])
            + " — 목록은 {{VALUES:컬럼}} 한 곳에서만 와야 한다"
        )
    return True, f"손으로 적은 값 목록 없음 ({len(columns)}개 컬럼: {', '.join(columns)})"


# ── 9) 흐름 선언 ↔ 코드 일치 ────────────────────────────────────────────────

def classifier_return_routes() -> set:
    """`_keyword_classify_ex` 본문에서 **실제로 반환하는** 라우트 리터럴을 읽는다.

    ⛔ 이 파싱이 pytest 안에만 있으면 **서버에서는 영원히 안 돈다.** 바로 오늘
       (2026-08-24) 같은 모양의 사고를 겪었다 — `static_value_list_dupes` 가
       `SC.ALL` 에는 있는데 `self_check.CHECKS` 에 등록되지 않아, 그날 아침 만든
       방어가 pytest 에서는 초록인 채 **서버에서는 죽어 있었다**(`dca36c3`).
       그래서 판정은 여기 한 곳에 두고, 이미 등록된 `flow_spec_matches_code` 가
       부른다 — 새 검사 id 를 만들지 않으므로 등록을 빠뜨릴 자리 자체가 없다.

    ⚠️ 빈 집합은 "반환이 없다" 가 아니라 **정규식이 낡았다**는 뜻이다. 호출부가
       빈 집합을 통과시키면 `==` 비교가 공허하게 성립할 수 있으니 반드시 실패로
       다뤄야 한다.
    """
    import inspect

    from app.agents.orchestrator import OrchestratorAgent

    src = inspect.getsource(OrchestratorAgent._keyword_classify_ex)
    return set(re.findall(r'return\s*\(\s*"([a-z_]+)"\s*,', src))


def flow_spec_matches_code() -> Tuple[bool, str]:
    """캔버스가 그리는 흐름이 실제 코드와 같은가.

    ⛔ 이 검사가 죽으면 **기능 전체가 무의미하다.** 그림이 코드와 갈리는 순간
       캔버스는 이 프로젝트의 네 번째 "사본이 갈린 사고"가 된다
       (direct 프롬프트 두 벌 / @@ 목록 두 벌 / Continent1 값 두 벌).

    두 방향을 본다:
      · 선언 → 코드 : 노드가 가리키는 함수가 실제로 있는가
      · 코드 → 선언 : 코드가 만들 수 있는 **모든 경로**가 캔버스에 노드로 있는가

    ⛔ 예전엔 역방향이 `_DB_REGISTRY` 의 라우트 6종만 봤다 (bigquery·cs·gws·
       model_rights·notion·report). `direct`·`team`·`multi` 는 `@@` 로 고를 수
       없는 라우터 전용이라 **이 검사에 아예 안 보였다** — 열 번째 non-`@@` 경로를
       추가하면 캔버스 어디에도 안 나오는데 검사는 계속 "일치" 라고 답했을 것이다
       (2026-08-24 리뷰 지적). 지금은 세 출처의 합집합을 본다:
         · `ROUTER_ROUTES`  — 분류기가 낼 수 있는 값
         · `HANDLER_ROUTES` — `_handle_*` 실행 핸들러가 있는 값
         · `_DB_REGISTRY`   — `@@` 로 고를 수 있는 값
       오늘 이 합집합은 정확히 캔버스의 라우트 노드 집합이라 곧바로 통과하고,
       **다음에 추가될 경로부터** 잡는다.
    """
    try:
        from app.agents.orchestrator import (HANDLER_ROUTES, ROUTER_ROUTES,
                                             OrchestratorAgent)
        from app.flow import graph, spec
    except Exception as e:
        return False, f"흐름 모듈 로드 실패: {str(e)[:120]}"

    problems: List[str] = []
    for node in spec.NODES:
        for dotted in (node.fn, node.subgraph):
            if not dotted:
                continue
            try:
                graph.resolve(dotted)
            except Exception as e:
                problems.append(f"{node.id}→{dotted} ({type(e).__name__})")

    try:
        built = graph.build()
        node_ids = {n["id"] for n in built["nodes"]}
    except Exception as e:
        return False, f"그래프 조립 실패: {str(e)[:120]}"

    registry_routes = {e["route"] for e in OrchestratorAgent._DB_REGISTRY}
    every_route = set(ROUTER_ROUTES) | set(HANDLER_ROUTES) | registry_routes
    for route in sorted(every_route):
        if f"route.{route}" not in node_ids:
            problems.append(f"라우트 '{route}' 노드 없음")

    # 라우터 노드의 나가는 엣지 = 분류기가 낼 수 있는 값. 하나라도 어긋나면
    # 화면이 "이 질문은 저기로 갈 수 있다"고 없는 길을 알려준다 (13개 거짓 엣지 사고).
    for router in ("router.keyword", "router.llm"):
        drawn = {e["dst"][len("route."):] for e in built["edges"]
                 if e["src"] == router and e["dst"].startswith("route.")}
        if drawn != set(ROUTER_ROUTES):
            problems.append(
                f"{router} 엣지≠분류기 (+{sorted(drawn - set(ROUTER_ROUTES))} "
                f"−{sorted(set(ROUTER_ROUTES) - drawn)})")

    # 위 검사는 `ROUTER_ROUTES` 상수가 맞다는 전제 위에 있다 — 상수가 코드와 갈리면
    # 둘이 사이좋게 틀린다. 그래서 분류기 본문에서 직접 읽어 **양방향으로** 대조한다.
    # ⛔ 부분집합(⊆)으로는 부족하다: 상수 쪽이 더 넓은 방향이 정확히 **거짓 화살표가
    #    생기는 방향**이다 (라우터의 나가는 엣지를 이 상수에서 부챗살로 뽑기 때문).
    #    실제로 상수에 없는 라우트를 하나 끼워 넣어도 ⊆ 는 그대로 참이었다.
    try:
        returned = classifier_return_routes()
    except Exception as e:
        problems.append(f"분류기 반환 리터럴 파싱 실패 ({type(e).__name__})")
    else:
        if not returned:
            problems.append("분류기 반환 리터럴을 하나도 못 읽었다 — 정규식이 낡았다")
        elif returned != set(ROUTER_ROUTES):
            problems.append(
                f"분류기≠ROUTER_ROUTES (상수에만 {sorted(set(ROUTER_ROUTES) - returned)} "
                f"· 코드에만 {sorted(returned - set(ROUTER_ROUTES))})")

    # 도달 불가로 표시한 노드에 화살표가 붙으면 표시와 그림이 서로 반대말을 한다
    for node in spec.NODES:
        if node.unreachable and any(
                e["src"] == node.id or e["dst"] == node.id for e in built["edges"]):
            problems.append(f"'{node.id}' 는 도달 불가로 적혔는데 엣지가 있다")

    if problems:
        return False, ("흐름 선언이 코드와 어긋난다 "
                       f"{len(problems)}건: " + ", ".join(problems[:4]))
    return True, f"노드 {len(node_ids)}개 · 선언과 코드 일치"


ALL = [
    ("static_assets", asset_sanity, "프론트 자산 온전성"),
    ("static_value_list_dupes", prompt_no_handwritten_value_lists,
     "자동 주입 컬럼 값을 손으로 다시 나열했는가"),
    ("static_css_vars", undefined_css_vars, "정의되지 않은 CSS 변수"),
    ("static_at_sources", at_source_parity, "@@ 데이터소스 프론트/서버 일치"),
    ("static_prompt_copies", prompt_single_source, "direct 프롬프트 단일 소스"),
    ("static_cache_version", cache_version_doc, "캐시 버전 문서 일치"),
    ("static_kw_collision", keyword_collisions, "라우팅 키워드 삼킴 충돌"),
    ("static_fi_mask", fi_prompt_masking, "손익 프롬프트 마스킹 실동작"),
    ("static_flow_spec", flow_spec_matches_code, "흐름 선언 ↔ 코드 일치"),
]
