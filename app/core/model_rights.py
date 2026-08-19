"""모델 초상권 — 스프레드시트 적재 + 사용 가능 여부 조회.

배경 (2026-08-07):
    모델 사진의 사용 가능 매체·지역·기간이 스프레드시트에만 있어, 마케터가 기간이
    지난 이미지를 쓰면 에이전시 적발 시 모델당 수백만 원(시트 기재 기준 200~500만원)을
    물어야 한다. "이 모델 사진 지금 써도 되나?"를 챗봇이 답하게 만든 것.
    구조는 전성분 파이프라인(app/core/ingredients.py)과 동일: 시트 → MariaDB 매일 적재.

시트 구조 (읽는 법):
    탭마다 모델별 "블록"이 반복된다 — 헤더 행(이름/라인/온라인/오프라인/사용 가능
    매체/촬영날짜/연장/지역 4칸/금액) 다음에 모델 행이 오고, 그 아래 몇 행에 걸쳐
    매체 목록·연장 회차별 지역 기한·에이전시 연락처가 이어진다. 병합 셀은 values API
    에서 좌상단 셀에만 값이 온다. "사용 X" 마커가 블록에 있으면 현재 사용 불가.

핵심 원칙:
    - 판정은 데이터가 하고, LLM 은 설명만 한다 — 기간 파싱·만료 판정은 여기(코드)서
      끝내고, 핸들러에는 판정 결과를 넘긴다 (전성분의 '미상≠미포함' 원칙과 동일)
    - "업로드 만료" = 신규 업로드 금지. 기간이 남았어도 이 마커가 있으면 사용 불가로
      안내한다
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime
from typing import Optional

import structlog

from app.config import get_settings
from app.core.textmatch import strip_particle
from app.db.mariadb import execute, fetch_all

logger = structlog.get_logger(__name__)

SPREADSHEET_ID = "1iG7sH6dBAyw90O6qjMQQtEZm4LWjgw-FISXHwG23FIw"
# 원본 시트 — 판정이 안 되거나 불명일 때 사용자가 직접 볼 수 있게 답변·상태에 노출한다
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit"

# 라인별 초상권 담당자 (시트 상단 안내 기재)
LINE_MANAGERS = {
    "톤 브라이트닝": "소담이", "톤브라이트닝": "소담이", "티트리카": "소담이", "테카": "소담이",
    "포어마이징": "전선영", "프로바이오시카": "전선영", "랩인네이처": "전선영",
    "센텔라": "지유환", "히알루-시카": "지유환", "히알루시카": "지유환",
}
ESCALATION_CONTACT = "빅쩨이"  # 기간 외 사용/기타 문의

_DDL_MODELS = """
CREATE TABLE IF NOT EXISTS model_rights (
    id INT AUTO_INCREMENT PRIMARY KEY,
    model_name VARCHAR(100) NOT NULL,
    sheet_tab VARCHAR(100) NOT NULL DEFAULT '',
    product_line VARCHAR(100) NOT NULL DEFAULT '',
    online_ok TINYINT NOT NULL DEFAULT 0,
    offline_ok TINYINT NOT NULL DEFAULT 0,
    media VARCHAR(300) NOT NULL DEFAULT '',
    shoot_date VARCHAR(30) NOT NULL DEFAULT '',
    agency VARCHAR(300) NOT NULL DEFAULT '',
    marked_unusable TINYINT NOT NULL DEFAULT 0,
    synced_at DATETIME NOT NULL,
    UNIQUE KEY uq_model (model_name, sheet_tab),
    INDEX idx_line (product_line)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_DDL_PERIODS = """
CREATE TABLE IF NOT EXISTS model_right_periods (
    id INT AUTO_INCREMENT PRIMARY KEY,
    model_name VARCHAR(100) NOT NULL,
    sheet_tab VARCHAR(100) NOT NULL DEFAULT '',
    extension_no INT NOT NULL DEFAULT 1,
    region VARCHAR(40) NOT NULL,
    period_raw VARCHAR(120) NOT NULL DEFAULT '',
    start_date DATE NULL,
    end_date DATE NULL,
    upload_expired TINYINT NOT NULL DEFAULT 0,
    fee VARCHAR(60) NOT NULL DEFAULT '',
    synced_at DATETIME NOT NULL,
    INDEX idx_model (model_name),
    INDEX idx_end (end_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


_DDL_FACES = """
CREATE TABLE IF NOT EXISTS model_faces (
    id INT AUTO_INCREMENT PRIMARY KEY,
    model_name VARCHAR(100) NOT NULL,
    embedding BLOB NOT NULL,
    source VARCHAR(300) NOT NULL DEFAULT '',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_model (model_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def ensure_model_rights_tables() -> None:
    for ddl in (_DDL_MODELS, _DDL_PERIODS, _DDL_FACES):
        try:
            execute(ddl)
        except Exception as e:
            logger.debug("model_rights_ddl_skip", error=str(e)[:120])


# ── 파싱 ─────────────────────────────────────────────────────────────────────

_DATE_RE = re.compile(r"(\d{2})\.(\d{1,2})\.(\d{1,2})")
_REGIONS = ("한국", "동남아", "일본, 대만, 중국", "미주, 유럽")


def _parse_date(tok: str) -> Optional[date]:
    m = _DATE_RE.search(tok)
    if not m:
        return None
    yy, mm, dd = (int(x) for x in m.groups())
    try:
        return date(2000 + yy, mm, dd)
    except ValueError:
        return None


def _parse_period(raw: str) -> dict:
    """'25.05.01~25.11.01 (업로드 만료)' → 시작/끝/만료마커."""
    raw = (raw or "").strip()
    out = {"raw": raw, "start": None, "end": None, "upload_expired": 0, "unusable": False}
    if not raw:
        return out
    if raw == "X":
        out["unusable"] = True
        return out
    if "업로드 만료" in raw:
        out["upload_expired"] = 1
    dates = _DATE_RE.findall(raw)
    if dates:
        parsed = [_parse_date(".".join(d)) for d in dates]
        parsed = [p for p in parsed if p]
        if parsed:
            out["start"] = parsed[0]
            out["end"] = parsed[-1] if len(parsed) > 1 else None
    return out


def _header_map(row: list[str]) -> Optional[dict]:
    """헤더 행이면 컬럼명→인덱스 맵을 돌려준다 (레이아웃이 탭마다 달라 위치 기반 불가)."""
    cells = [c.strip() for c in row]
    if "이름" not in cells or "사용 가능 매체" not in cells:
        return None
    m = {}
    for i, c in enumerate(cells):
        if c:
            m[c] = i
    return m


def parse_tab(rows: list[list[str]], tab: str) -> list[dict]:
    """한 탭의 raw 행들에서 모델 블록들을 추출한다."""

    def cell(r: list[str], idx: Optional[int]) -> str:
        if idx is None or idx >= len(r):
            return ""
        return str(r[idx]).strip()

    models = []
    hdr: Optional[dict] = None
    cur: Optional[dict] = None

    def flush():
        nonlocal cur
        if cur and cur["name"]:
            cur["media"] = ", ".join(dict.fromkeys(cur["media"]))
            cur["agency"] = " / ".join(cur["agency"])[:300]
            models.append(cur)
        cur = None

    for row in rows:
        h = _header_map(row)
        if h:
            flush()
            hdr = h
            continue
        if hdr is None:
            continue
        name_c = cell(row, hdr.get("이름"))
        media_c = cell(row, hdr.get("사용 가능 매체"))
        ext_c = cell(row, hdr.get("연장"))

        if cur is None:
            if not name_c:
                continue
            cur = {
                "tab": tab, "name": name_c,
                "line": cell(row, hdr.get("라인")),
                "online": cell(row, hdr.get("온라인")).upper() == "O",
                "offline": cell(row, hdr.get("오프라인")).upper() == "O",
                "shoot_date": cell(row, hdr.get("촬영날짜")),
                "media": [media_c] if media_c else [],
                "agency": [], "marked_unusable": False, "periods": [],
            }
        else:
            # 블록 연속 행: 이름 칸에 값이 있으면 에이전시/비고
            if name_c:
                cur["agency"].append(name_c)
            if media_c:
                cur["media"].append(media_c)

        if cur is None:
            continue
        # 연장 회차 행 (연장 번호가 없어도 첫 행에 지역 기한이 올 수 있다)
        region_vals = {rg: cell(row, hdr.get(rg)) for rg in _REGIONS if hdr.get(rg) is not None}
        if any(v for v in region_vals.values()):
            if any(v == "사용 X" for v in region_vals.values()):
                cur["marked_unusable"] = True
            else:
                ext_no = int(ext_c) if ext_c.isdigit() else (cur["periods"][-1]["ext"] if cur["periods"] else 1)
                fee = cell(row, hdr.get("금액"))
                for rg, v in region_vals.items():
                    if not v:
                        continue
                    p = _parse_period(v)
                    cur["periods"].append({
                        "ext": ext_no, "region": rg, **p, "fee": fee,
                    })
        elif ext_c.isdigit():
            pass  # 연장 번호만 있고 기한이 빈 행 — 기록할 것 없음

    flush()
    return models


# ── 적재 ─────────────────────────────────────────────────────────────────────


def sync_model_rights() -> dict:
    """시트 전 탭 → model_rights / model_right_periods 재적재."""
    import os

    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    ensure_model_rights_tables()
    credential_path = (
        os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        or get_settings().google_application_credentials
    )
    creds = Credentials.from_service_account_file(
        credential_path,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    svc = build(
        "sheets", "v4", credentials=creds, cache_discovery=False, num_retries=2
    )
    meta = (
        svc.spreadsheets()
        .get(spreadsheetId=SPREADSHEET_ID)
        .execute(num_retries=2)
    )

    all_models = []
    for sh in meta["sheets"]:
        tab = sh["properties"]["title"]
        rows = (
            svc.spreadsheets().values()
            .get(spreadsheetId=SPREADSHEET_ID, range=f"'{tab}'!A1:R1000")
            .execute(num_retries=2).get("values", [])
        )
        all_models.extend(parse_tab(rows, tab))

    now = datetime.now()
    execute("DELETE FROM model_right_periods")
    execute("DELETE FROM model_rights")
    n_periods = 0
    for m in all_models:
        execute(
            "INSERT INTO model_rights (model_name, sheet_tab, product_line, online_ok, "
            "offline_ok, media, shoot_date, agency, marked_unusable, synced_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE product_line=VALUES(product_line), synced_at=VALUES(synced_at)",
            (m["name"][:100], m["tab"][:100], m["line"][:100], int(m["online"]),
             int(m["offline"]), m["media"][:300], m["shoot_date"][:30], m["agency"],
             int(m["marked_unusable"]), now),
        )
        for p in m["periods"]:
            execute(
                "INSERT INTO model_right_periods (model_name, sheet_tab, extension_no, "
                "region, period_raw, start_date, end_date, upload_expired, fee, synced_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (m["name"][:100], m["tab"][:100], p["ext"], p["region"][:40],
                 p["raw"][:120], p["start"], p["end"], p["upload_expired"],
                 p["fee"][:60], now),
            )
            n_periods += 1
    stats = {"models": len(all_models), "periods": n_periods,
             "tabs": len(meta["sheets"])}
    logger.info("model_rights_synced", **stats)
    return stats


# ── 조회 (핸들러용) ──────────────────────────────────────────────────────────


def get_rights_context(query: str, fallback_all: bool = True) -> str:
    """질문과 관련된 모델들의 판정 요약 텍스트 — LLM 은 이걸 설명만 한다.

    `fallback_all=False` 는 **사진을 붙였는데 인물을 특정하지 못한** 경우에 쓴다.
    그때까지 전체 목록을 내려주면 "누구야" 한마디에 모델 34명과 에이전시 연락처를
    쏟아붓는다 (2026-08-19 실측). 답이 아니라 소음이고, 연락처까지 함께 나간다 —
    모르면 모른다고 답하는 편이 낫다 (성분의 '미상≠미포함' 과 같은 원칙).
    """
    models = fetch_all("SELECT * FROM model_rights ORDER BY model_name")
    if not models:
        return ""
    q = query.replace(" ", "")
    named = [m for m in models if m["model_name"].replace(" ", "") in q]
    by_line = [m for m in models
               if m["product_line"] and any(tok and tok in q for tok in
                                            m["product_line"].replace("-", "").split())]
    if not named and not by_line and not fallback_all:
        return ""
    picked = named or by_line or models
    today = date.today()

    lines = [f"오늘 날짜: {today.isoformat()}", ""]
    for m in picked[:30]:
        periods = fetch_all(
            "SELECT * FROM model_right_periods WHERE model_name=%s AND sheet_tab=%s "
            "ORDER BY extension_no, region", (m["model_name"], m["sheet_tab"]))
        active = [p for p in periods
                  if p["end_date"] and p["start_date"]
                  and p["start_date"] <= today <= p["end_date"]
                  and not p["upload_expired"]]
        latest_end = max((p["end_date"] for p in periods if p["end_date"]), default=None)
        if m["marked_unusable"]:
            status = "사용 불가 (시트에 '사용 X' 표기)"
        elif active:
            regs = ", ".join(sorted({p["region"] for p in active}))
            ends = min(p["end_date"] for p in active)
            status = f"사용 가능 — 지역: {regs}, 만료일: {ends.isoformat()}"
        elif latest_end and latest_end < today:
            status = f"기간 만료 (마지막 만료일 {latest_end.isoformat()}) — 신규 업로드 금지"
        else:
            status = "기한 정보 불명 — 담당자 확인 필요"
        mgr = LINE_MANAGERS.get(m["product_line"], "")
        lines.append(
            f"- {m['model_name']} (라인: {m['product_line'] or '미기재'}, "
            f"온라인 {'O' if m['online_ok'] else 'X'}/오프라인 {'O' if m['offline_ok'] else 'X'}, "
            f"매체: {m['media'] or '미기재'}) → {status}"
            + (f" / 담당: {mgr}" if mgr else "")
            + (f" / 에이전시: {m['agency']}" if m["agency"] else "")
        )
    lines.append("")
    lines.append(f"규칙: 기재된 매체·기간 외 사용 시 모델당 수백만 원의 추가 초상권 비용이 발생할 수 있다. "
                 f"만료·불명 건이나 기간 외 사용 문의는 라인 담당자 또는 {ESCALATION_CONTACT}에게.")
    return "\n".join(lines)


# 사진이 붙었을 때 초상권으로 보내는 신호. 사람을 가리키거나(누구·인물),
# 써도 되는지를 묻는(써도·기한) 말이 하나라도 있으면 초상권 질문이다.
_PERSON_WORDS = ("누구", "누가", "인물", "사람", "모델")
_USAGE_WORDS = ("써도", "쓸 수", "쓸수", "사용 가능", "사용가능", "사용해도",
                "사용 기한", "기한", "만료", "언제까지")

_NAMES_TTL = 300.0
_NAMES_CACHE: dict = {"at": 0.0, "names": []}


def _model_names_cached() -> list[str]:
    """초상권 DB 에 실재하는 모델 이름 — 코드에 손으로 적지 않는다.

    프롬프트에 값 목록을 적으면 반드시 낡는다는 것을 이미 겪었다(값 목록 실측화).
    이름도 같다 — 시트에 모델이 추가되면 코드를 고치지 않아도 따라와야 한다.
    """
    now = time.monotonic()
    if _NAMES_CACHE["names"] and now - _NAMES_CACHE["at"] < _NAMES_TTL:
        return _NAMES_CACHE["names"]
    rows = fetch_all("SELECT model_name FROM model_rights")
    names = [str(r["model_name"]).strip() for r in rows if str(r.get("model_name") or "").strip()]
    _NAMES_CACHE.update({"at": now, "names": names})
    return names


def _name_variants(name: str) -> list[str]:
    """시트에 적힌 이름에서 사람들이 실제로 부르는 표기들을 뽑는다.

    시트 표기는 한 칸에 여러 이름이 들어 있다 — `김제인 (김정은)` · `YINGXIN (잉씬)` ·
    `Alexa & Wai` · `야오 조우`. 통짜로만 비교하면 **"김제인 관련 사진"이 안 걸린다**
    (2026-08-19 배포 직후 프로덕션에서 실제로 bigquery 로 샜다).
    """
    raw = (name or "").strip()
    if not raw:
        return []
    out = [raw]
    # 괄호 안팎을 각각 후보로 (김제인 (김정은) → 김제인 / 김정은)
    for part in re.split(r"[()]", raw):
        part = part.strip()
        if part and part != raw:
            out.append(part)
    # 'A & B' 는 통짜 이름이므로 쪼개지 않는다 — 한쪽만으로 특정되지 않는다
    return [v for v in dict.fromkeys(out) if v]


def _name_hit(query: str, name: str) -> bool:
    """이름이 낱말로 나오는가.

    ⛔ 부분 문자열로 보면 '조'가 '조회' 안에서, '안나'가 '안나푸르나' 안에서 걸린다
       (보고서 필터의 '요인도 → 인도' 와 같은 부류). 어절을 조사까지 떼어 비교한다.
    """
    q = query or ""
    for variant in _name_variants(name):
        if " " in variant or "&" in variant:
            # 여러 낱말로 된 이름은 공백만 지우고 통째로 본다
            if variant.lower().replace(" ", "") in q.lower().replace(" ", ""):
                return True
            continue
        n = variant.lower()
        for tok in re.split(r"[\s,./·|]+", q):
            tok = tok.strip("?!.\"'()[]{}<>~:;").lower()
            if not tok:
                continue
            if tok == n or strip_particle(tok) == n:
                return True
    return False


def model_rights_intent(query: str, has_image: bool = False) -> bool:
    """초상권 질문인가.

    낱말만 보던 게이트가 실제 오답을 냈다 (2026-08-19 프로덕션): 사진을 붙이고
    "누구야" 라고 물으면 초상권 경로로 가지 못하고, 이미지가 있으면 direct(vision)
    으로 **확신을 갖고** 강제되어 LLM 재판정도 못 탔다. 그래서 앱이 자기 기능을
    "제공하지 않습니다" 라고 답했다. 이름 질문("Alexa & Wai 정보")도 같은 이유로
    노션·웹으로 새서 지어냈다.

    신호는 셋이다 — 명시 낱말(초상권) · **첨부된 사진** · **DB 에 실재하는 모델 이름**.
    """
    q = query or ""
    if "초상권" in q:
        return True
    if "모델" in q and any(k in q for k in
                          ("사진", "이미지", "써도", "쓸 수", "사용 가능", "사용해도",
                           "기한", "만료", "언제까지")):
        return True

    _usage = any(k in q for k in _USAGE_WORDS)
    # 사진이 붙어 있다는 것 자체가 신호다. 단 제품컷·차트 분석은 그대로 비전에 남긴다
    # ("이 제품 성분", "이 차트 읽어줘"는 사람도 사용 가부도 묻지 않는다).
    if has_image and (any(w in q for w in _PERSON_WORDS) or _usage):
        return True

    try:
        names = _model_names_cached()
    except Exception as e:  # DB 가 없어도 위 낱말 판정은 살아 있어야 한다
        logger.warning("model_names_lookup_failed", error=str(e)[:200])
        names = []
    for name in names:
        # 짧은 이름(조·소리·안나…)은 단독으로 경로를 가로채면 안 된다 — 보조 신호가 있을 때만.
        _longest = max((len(v.replace(" ", "")) for v in _name_variants(name)), default=0)
        if _longest <= 2 and not (has_image or _usage or "사진" in q):
            continue
        if _name_hit(q, name):
            return True
    return False
