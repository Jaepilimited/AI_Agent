"""사내 용어 사전 — 은어·축약어·오타를 LLM 호출 전에 정식 명칭으로 보정한다.

배경 (2026-08-06):
    사용자들이 "센앰"(센텔라 앰플), "포마"/"pm"(포어마이징), "프바시"(프로바이오시카)
    같은 축약어와 "힝라우선세럼"(히알루시카 선세럼) 같은 오타로 질문한다.
    사내 은어는 세상 어디에도 없는 지식이라 모델이 알아맞힐 수 없고,
    프롬프트에 쌓는 방식은 수십 개를 넘기면 관리가 무너진다.

2층 구조:
    1층 — **정확 일치 사전** (term_aliases 테이블): 은어 → 정식 명칭.
          결정적 치환. "센앰 매출" → "센텔라 앰플(센앰) 매출".
    2층 — **자모 유사도 오타 보정**: 사전에 없는 한글 덩어리를 자모로 분해해
          알려진 용어와 비교, 충분히 비슷하면 보정한다.
          "힝라우선세럼" → "히알루시카(힝라우)선세럼".

설계 원칙:
    - 원문을 괄호로 보존한다 — LLM 이 맥락을 잃지 않는다.
    - 한글 별칭은 앞뒤가 한글이 아닐 때만, 영문은 단어 경계(\\b)로 치환한다.
      ("3pm" 안의 pm, "포마드" 안의 포마를 건드리지 않게)
    - **오타 보정은 정확히 일치하면 건드리지 않는다.** "히알루"는 히알루시카와
      히알루 테카 양쪽에 걸리는 중의어라, 올바른 표기는 그대로 두고
      틀린 표기(힝라우)만 가장 가까운 쪽으로 보정한다.
    - 보정은 한글 덩어리당 1회, 최고 후보가 2위와 충분히 벌어질 때만.
      애매하면 안 고치는 게 맞다 — 잘못 고치면 조용히 오답이 된다.

관리: /api/admin/aliases (GET/POST/DELETE). note='추측' 항목은 검수 필요.
"""

from __future__ import annotations

import re
import time
from difflib import SequenceMatcher
from typing import Optional

import structlog

from app.db.mariadb import execute, fetch_all

logger = structlog.get_logger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS term_aliases (
    id INT AUTO_INCREMENT PRIMARY KEY,
    alias VARCHAR(100) NOT NULL UNIQUE,
    canonical VARCHAR(200) NOT NULL,
    category VARCHAR(30) NOT NULL DEFAULT 'product',
    note VARCHAR(200) NOT NULL DEFAULT '',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

# 시드. note='추측 — 검수 필요' 는 AI 유추이므로 틀리면 admin API 로 삭제할 것.
_SEED = [
    # ── 사용자 확정 (2026-08-06 임재필) ──
    ("센앰", "센텔라 앰플", "product", "확정"),
    ("포마", "포어마이징", "line", "확정"),
    ("pm", "포어마이징", "line", "확정"),
    ("프바시", "프로바이오시카", "line", "확정"),
    # ── AI 추측 — 같은 축약 패턴에서 유추 ──
    ("포어마", "포어마이징", "line", "추측 — 검수 필요"),
    ("센테카", "센텔라 테카", "line", "추측 — 검수 필요"),
    ("랩인", "랩인네이처", "line", "추측 — 검수 필요"),
    ("lin", "랩인네이처", "line", "추측 — 기존 프롬프트에 있던 약어"),
    ("톤브", "톤 브라이트닝", "line", "추측 — 검수 필요"),
    ("커랩", "커먼랩스", "brand", "추측 — 검수 필요"),
    ("좀뷰", "좀비뷰티", "brand", "추측 — 검수 필요"),
    # 노션 문서 제목에 실사용 근거 ("KBT 스스 운영방법", "네이버 스스 업무 공유")
    ("스스", "스마트스토어", "channel", "추측 — 노션 문서에 실사용 근거"),
    # ── 국가 통칭 (업계 관용) ──
    ("인니", "인도네시아", "country", "관용 표현"),
    ("말레", "말레이시아", "country", "관용 표현"),
    ("싱가폴", "싱가포르", "country", "표기 변형"),
]

# 오타 보정 대상 어휘 (표기 → 정식 명칭).
# "히알루"는 정확 별칭으로 넣으면 "히알루 테카"까지 히알루시카로 바꿔버리므로
# **오타 보정 전용**이다 — 정확히 "히알루"라고 쓰면 건드리지 않고,
# "힝라우" 같은 오타만 주력 라인(히알루시카) 쪽으로 보정한다.
_FUZZY_VOCAB = [
    ("센텔라", "센텔라"),
    ("히알루시카", "히알루시카"),
    ("히알루", "히알루시카"),
    ("포어마이징", "포어마이징"),
    ("프로바이오시카", "프로바이오시카"),
    ("티트리카", "티트리카"),
    ("랩인네이처", "랩인네이처"),
    ("커먼랩스", "커먼랩스"),
    ("좀비뷰티", "좀비뷰티"),
    ("스마트스토어", "스마트스토어"),
    ("인도네시아", "인도네시아"),
    ("말레이시아", "말레이시아"),
    ("메가와리", "메가와리"),
]

_FUZZY_THRESHOLD = 0.82   # 오탐 우선 방지: 얼마인지(0.76)·커머스(0.75)는 보정 금지
_FUZZY_GAP = 0.05         # 1위-2위 최소 격차 — 애매하면 안 고친다

# ── 자모 분해 ────────────────────────────────────────────────────────────────

_CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_JONG = " ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"


def _jamo(s: str) -> str:
    out = []
    for ch in s:
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3:
            code -= 0xAC00
            out.append(_CHO[code // 588])
            out.append(_JUNG[(code % 588) // 28])
            jong = code % 28
            if jong:
                out.append(_JONG[jong])
        else:
            out.append(ch)
    return "".join(out)


# ── 로드 캐시 (5분) ───────────────────────────────────────────────────────────

_cache: list[tuple[re.Pattern, str, str]] = []
_cache_at: float = 0.0
_CACHE_TTL = 300.0


def ensure_term_aliases_table() -> None:
    """테이블 생성 + 비어 있으면 시드 (idempotent)."""
    try:
        execute(_DDL)
        execute(_DDL_CANDIDATES)
        rows = fetch_all("SELECT COUNT(*) c FROM term_aliases")
        if rows and rows[0]["c"] == 0:
            for alias, canonical, cat, note in _SEED:
                execute(
                    "INSERT IGNORE INTO term_aliases (alias, canonical, category, note) "
                    "VALUES (%s, %s, %s, %s)",
                    (alias, canonical, cat, note),
                )
            logger.info("term_aliases_seeded", count=len(_SEED))
    except Exception as e:
        logger.warning("term_aliases_ensure_failed", error=str(e)[:150])


# 별칭 뒤에 붙을 수 있는 조사 — 붙어도 별칭으로 인정한다 ("델메랑", "센앰은").
# ⚠️ '로/으로' 는 일부러 뺐다: "스스" + "로" = "스스로" 라는 일반 단어가 되어
# "스스로 해결" 이 스마트스토어로 오염된다. 조사가 아니라 단어의 일부일 수 있는
# 것은 허용하지 않는다.
_PARTICLES_AFTER = "(?:이랑|이라|한테|에서|부터|까지|보다|하고|랑|은|는|이|가|을|를|의|도|만|과|와)?"


def _compile(alias: str) -> re.Pattern:
    if re.fullmatch(r"[A-Za-z0-9]+", alias):
        return re.compile(rf"(?i)\b{re.escape(alias)}\b")
    # 별칭 + (조사 0~1개) 뒤가 한글이 아니어야 매칭 — 조사는 소비하지 않고 남긴다
    return re.compile(rf"(?<![가-힣]){re.escape(alias)}(?={_PARTICLES_AFTER}(?![가-힣]))")


def _load() -> list[tuple[re.Pattern, str, str]]:
    global _cache, _cache_at
    now = time.time()
    if _cache and now - _cache_at < _CACHE_TTL:
        return _cache
    try:
        rows = fetch_all("SELECT alias, canonical FROM term_aliases")
    except Exception as e:
        logger.warning("term_aliases_load_failed", error=str(e)[:120])
        return _cache
    rows.sort(key=lambda r: len(r["alias"]), reverse=True)
    _cache = [(_compile(r["alias"]), r["canonical"], r["alias"]) for r in rows]
    _cache_at = now
    return _cache


def invalidate_cache() -> None:
    global _cache_at
    _cache_at = 0.0


# ── 2층: 오타 보정 ────────────────────────────────────────────────────────────


def _fuzzy_correct(query: str) -> tuple[str, list[str]]:
    """한글 덩어리를 자모 유사도로 알려진 용어에 보정한다. 덩어리당 최대 1회."""
    hits: list[str] = []

    def fix_run(m: re.Match) -> str:
        run = m.group(0)
        # 이미 알려진 표기가 들어 있으면 손대지 않는다
        for term, _ in _FUZZY_VOCAB:
            if term in run:
                return run
        # 격차 판정은 **서로 다른 정식 명칭 간**에만 한다. 같은 용어의 이웃한
        # 창끼리 2위 경쟁을 시키면 높은 유사도의 실제 오타도 자기 자신에게 밀려
        # 기각되므로, 정식 명칭별 최고 점수만 비교한다.
        per_canon: dict[str, tuple[float, int, int, str]] = {}
        for term, canonical in _FUZZY_VOCAB:
            tl = len(term)
            tj = _jamo(term)
            for w in {tl - 1, tl, tl + 1}:
                if w < 2 or w > len(run):
                    continue
                for i in range(len(run) - w + 1):
                    window = run[i:i + w]
                    if window == term:
                        continue  # 정확 일치는 보정 대상이 아니다
                    window_jamo = _jamo(window)
                    score = SequenceMatcher(None, window_jamo, tj).ratio()
                    cur = per_canon.get(canonical)
                    if cur is None or score > cur[0]:
                        per_canon[canonical] = (score, i, i + w, tj[0] if tj else "")
        ranked = sorted(per_canon.items(), key=lambda kv: kv[1][0], reverse=True)
        if not ranked:
            return run
        canonical, (score, i, j, target_initial) = ranked[0]
        second = ranked[1][1][0] if len(ranked) > 1 else 0.0
        winning_jamo = _jamo(run[i:j])
        # 후보 간 격차 계산은 기존 전체 후보를 그대로 둔다. 초성이 다른 후보를
        # 미리 빼면 2위가 사라져 "커머스" 같은 애매한 일반어가 새로 통과한다.
        # 최종 승자만 초성을 확인해 "얼마인지"(ㅇ)→"포어마이징"(ㅍ) 오염을 막는다.
        same_initial = bool(winning_jamo and target_initial
                            and winning_jamo[0] == target_initial)
        if (same_initial and score >= _FUZZY_THRESHOLD
                and (score - second) >= _FUZZY_GAP):
            hits.append(f"{run[i:j]}≈{canonical}({score:.2f})")
            return run[:i] + f"{canonical}({run[i:j]})" + run[j:]
        return run

    out = re.sub(r"[가-힣]{3,}", fix_run, query)
    return out, hits


# ── 공개 API ─────────────────────────────────────────────────────────────────


def expand_aliases(query: str) -> tuple[str, list[str]]:
    """1층(정확 사전) → 2층(오타 보정) 순으로 질문을 보정한다.

    Returns:
        (보정된 질문, 적중 목록). 적중이 없으면 원문 그대로.
    """
    if not query:
        return query, []
    hits: list[str] = []
    out = query
    # 1층: 정확 별칭
    for pattern, canonical, alias in _load():
        if canonical in out:
            continue  # 이미 정식 명칭으로 물었으면 손대지 않는다
        new = pattern.sub(lambda m: f"{canonical}({m.group(0)})", out)
        if new != out:
            hits.append(f"{alias}→{canonical}")
            out = new
    # 2층: 오타 보정 (1층에서 치환된 괄호 원문은 한글 run 이 짧아져 재보정 위험 낮음)
    out, fuzzy_hits = _fuzzy_correct(out)
    hits.extend(fuzzy_hits)
    return out, hits


# ── 3층: 미인식 용어 자동 수집 (사전이 스스로 자라는 경로) ─────────────────────
#
# 0건 답변이 나온 질문에서 시스템이 모르는 한글 용어를 후보로 적재한다.
# 후보는 자동으로 사전에 들어가지 않는다 — admin 이 승인해야 한다.
# (오타 보정과 같은 원칙: 확신 없는 치환은 조용한 오답을 만든다.)

_DDL_CANDIDATES = """
CREATE TABLE IF NOT EXISTS term_alias_candidates (
    id INT AUTO_INCREMENT PRIMARY KEY,
    term VARCHAR(100) NOT NULL UNIQUE,
    first_query VARCHAR(500) NOT NULL DEFAULT '',
    occurrences INT NOT NULL DEFAULT 1,
    suggested_canonical VARCHAR(200) DEFAULT NULL,
    suggested_score FLOAT DEFAULT NULL,
    status ENUM('pending','approved','rejected') NOT NULL DEFAULT 'pending',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

# 질문에 흔히 나오는 일반어 — 후보에서 걸러낸다. 여기 없는 도메인 명사만 남는다.
_STOPWORDS = frozenset("""
매출 매출액 판매량 판매수량 판매 수량 순위 실적 비중 총합 합계 총액 금액 비용 광고비
알려줘 알려주세요 보여줘 보여주세요 뽑아줘 정리해줘 비교해줘 분석해줘 확인해줘 해줘 해주세요 알려 주세요
비교 분석 확인 기준 기간 조회 요약 상세 현황 추이 통계 정보 데이터 결과 목록 종류
상반기 하반기 분기 월별 연도별 연별 주별 일별 국가별 제품별 팀별 채널별 브랜드별 라인별 권역별
올해 작년 재작년 내년 지난달 이번달 지난주 이번주 오늘 어제 최근 지난 이번 향후
전체 전부 각각 모두 기존 신규 이후 이전 대비 대상 관련 부분 해당
얼마 얼마야 얼마임 무엇 뭐야 뭔지 어디 어떻게 왜 언제 누구
차트 그래프 도식화 시각화 테이블 리포트
제품 라인 브랜드 국가 채널 팀 플랫폼 카테고리 고객 거래처 업체
년 월 일 주 개 건 명 원 억원 만원 개월
아마존 쇼피 큐텐 틱톡 틱톡샵 라자다 올리브영 이커머스 자사몰
미국 일본 중국 한국 태국 베트남 필리핀 대만 홍콩 독일 영국 프랑스 폴란드 러시아 인도 호주
유럽 아시아 북미 남미 중미 중동 동남아 동남아시아
""".split())

_PARTICLES = "은는이가을를의도만과와랑에서로부터"


def _known_terms() -> set:
    """사전·어휘에 이미 있는 표기 — 후보에서 제외."""
    known = set(_STOPWORDS)
    for term, canonical in _FUZZY_VOCAB:
        known.add(term)
        known.update(canonical.split())
    try:
        for r in fetch_all("SELECT alias, canonical FROM term_aliases"):
            known.add(r["alias"].lower())
            known.update(r["canonical"].split())
    except Exception:
        pass
    return known


def _strip_particle(tok: str) -> str:
    """조사 제거는 `app/core/textmatch.py` 한 곳이다 (검색어 추출도 같은 것을 쓴다).

    ⛔ 예전엔 여기서 **한 글자만** 뗐다 — `에서`·`으로` 같은 두 글자 조사는 못 뗐다.
       검색 경로마다 조사 처리가 달라 조용히 어긋난 것이 2026-08-14 드라이브 사고다.
    """
    from app.core.textmatch import strip_particle
    return strip_particle(tok)


def _suggest(term: str) -> tuple[Optional[str], Optional[float]]:
    """후보 용어에 가장 가까운 정식 명칭을 제안한다 (표시용 — 확정은 admin)."""
    tj = _jamo(term)
    best, best_score = None, 0.0
    for vocab_term, canonical in _FUZZY_VOCAB:
        score = SequenceMatcher(None, tj, _jamo(vocab_term)).ratio()
        if score > best_score:
            best, best_score = canonical, score
    if best_score >= 0.5:
        return best, round(best_score, 2)
    return None, None


def collect_candidates(query: str) -> int:
    """0건 답변이 나온 질문에서 미인식 한글 용어를 후보로 적재한다.

    format_answer 의 빈 결과 분기에서 백그라운드 스레드로 호출된다 —
    사용자 응답 경로를 절대 늦추지 않는다. 반환: 적재/갱신한 후보 수.
    """
    if not query or len(query) > 500:
        return 0
    try:
        known = _known_terms()
        saved = 0
        for run in re.findall(r"[가-힣]{2,15}", query):
            tok = _strip_particle(run)
            if len(tok) < 2 or tok in known or tok.lower() in known:
                continue
            # 이미 정식명칭(원문) 형태로 치환된 조각은 원문 쪽이 사전에 있으므로 위에서 걸러진다
            sug, score = _suggest(tok)
            execute(
                "INSERT INTO term_alias_candidates "
                "(term, first_query, suggested_canonical, suggested_score) "
                "VALUES (%s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE occurrences = occurrences + 1, last_seen_at = NOW()",
                (tok[:100], query[:500], sug, score),
            )
            saved += 1
        if saved:
            logger.info("alias_candidates_collected", count=saved, query=query[:80])
        return saved
    except Exception as e:
        logger.warning("alias_candidates_failed", error=str(e)[:120])
        return 0
