"""메일 발송 — 시트 행·탭 목록 파싱과 발송 전 점검. 순수 함수, 조회·LLM 없음."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.core.mail_template import MailTemplate, normalize_key, render, unreplaced, unreplaced_html

DOC_ID = "1Gofa6pusFe2t8o-ft1Xz5Be-sxmmUux5sQbu5vJA6S4"
TAB_DIR_SHEET = "_탭목록"
# gmail_v2 스프레드시트 — 자가 점검이 `_탭목록` 갱신 시각을 읽는 곳 (env CELLA_MAIL_TAB_DIR_SPREADSHEET_ID 로 덮어쓴다)
MAIL_TAB_DIR_SPREADSHEET_ID = "1n08f1VIna9hZH03Zdws-31-G7RFaYJ3T7Eg7vQk636c"
# 화면에서 고르는 스프레드시트 — 링크를 붙여넣지 않는다 (2026-10-01 요청). `_탭목록` 이 있는 파일만 쓸 수 있다.
MAIL_SPREADSHEETS = [{"name": "gmail_v2", "id": MAIL_TAB_DIR_SPREADSHEET_ID}]
# '셀라 메일 템플릿 캐시' Apps Script(재필 소유)가 쓰는 index.json 의 Drive 파일 ID
# (env CELLA_MAIL_TEMPLATE_CACHE_INDEX_ID 로 덮어쓴다). 탭 zip 은 서버가 직접 못 받는다 — 프록시.
MAIL_TEMPLATE_CACHE_INDEX_ID = "1O-v16uFccf4O3tj9hSIJSzH5axfSWer-"


def template_cache_index_id() -> str:
    import os
    return (os.getenv("CELLA_MAIL_TEMPLATE_CACHE_INDEX_ID") or "").strip() or MAIL_TEMPLATE_CACHE_INDEX_ID
DAILY_LIMIT = 2000
MAX_MESSAGE_BYTES = 24 * 1024 * 1024
TAB_DIR_STALE = timedelta(hours=1)
# 쉼표·세미콜론·공백이 있으면 To 에 여러 명이 들어간다 — 한 행 한 사람만
_EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")
_KST = timezone(timedelta(hours=9))


@dataclass
class Row:
    row: int
    manager: str
    email: str
    status: str
    values: dict[str, str] = field(default_factory=dict)
    attachments: list[str] = field(default_factory=list)  # `첨부` 열의 Drive 파일 ID
    attach_bad: list[str] = field(default_factory=list)   # 그 칸에서 알아볼 수 없는 조각 — 점검 오류


@dataclass
class Columns:
    email: int
    manager: int
    status: int
    note: int
    date: int | None
    variables: dict[str, int]
    attach: int | None = None


@dataclass
class Issue:
    row: int | None
    level: str
    message: str


# ⛔ 열은 **위치로** 읽는다 — 기존 Apps Script(gmailv2 CONFIG)와 똑같이.
#    담당자마다 헤더 이름이 다르다 (B열이 `담당자` 인 시트도, `문서명` 인 시트도,
#    G열이 `전송여부` 인 시트도 `전송 여부` 인 시트도 있다, 2026-10-01 실측).
#    이름으로 찾으면 기존 툴로 잘 쓰던 시트가 여기서만 안 열린다.
#    A 날짜 · B 탭 이름 · D 이메일 · G 전송여부 · H 비고, 1행 헤더, 2행부터 데이터.
COL_DATE, COL_MANAGER, COL_EMAIL, COL_STATUS, COL_NOTE = 0, 1, 3, 6, 7
_FIXED_COLS = {COL_DATE, COL_MANAGER, COL_EMAIL, COL_STATUS, COL_NOTE}
# 수신자별 첨부 열은 고정 위치가 없다 — 담당자가 I열 이후에 만들므로 1행 헤더 이름으로 찾는다
ATTACH_HEADERS = {"첨부", "첨부파일", "attachment", "attachments"}
_DRIVE_LINK = re.compile(r"/(?:file|document|spreadsheets|presentation)/d/([A-Za-z0-9_-]+)|[?&]id=([A-Za-z0-9_-]+)")
_DRIVE_BARE_ID = re.compile(r"[A-Za-z0-9_-]{20,}")


def drive_ids(cell: str) -> tuple[list[str], list[str]]:
    """첨부 칸 → (Drive 파일 ID, 알아볼 수 없는 조각). 줄바꿈·쉼표·공백으로 나눈다."""
    ids: list[str] = []
    bad: list[str] = []
    for part in re.split(r"[\s,]+", cell or ""):
        if not part:
            continue
        m = _DRIVE_LINK.search(part)
        fid = (m.group(1) or m.group(2)) if m else (part if _DRIVE_BARE_ID.fullmatch(part) else None)
        if fid is None:
            bad.append(part)
        elif fid not in ids:
            ids.append(fid)
    return ids, bad


def parse_rows(values: list[list[str]]) -> tuple[list[Row], Columns]:
    if not values or not any(str(c).strip() for c in values[0]):
        raise ValueError("시트 1행에 헤더가 없습니다 — 기존 메일 툴과 같은 양식(A 날짜 · B 탭 이름 · "
                         "D 이메일 · G 전송여부 · H 비고)이어야 합니다")
    headers = [str(h) for h in values[0]]
    variables = {normalize_key(h.strip()[1:-1]): i for i, h in enumerate(headers)
                 if h.strip().startswith("{") and h.strip().endswith("}")}
    attach = next((i for i, h in enumerate(headers)
                   if i not in _FIXED_COLS and normalize_key(h) in ATTACH_HEADERS), None)
    cols = Columns(email=COL_EMAIL, manager=COL_MANAGER, status=COL_STATUS,
                   note=COL_NOTE, date=COL_DATE, variables=variables, attach=attach)
    rows = []
    for ri, raw in enumerate(values[1:], start=2):
        cell = lambda i: (str(raw[i]).strip() if i is not None and i < len(raw) else "")
        if not any(str(c).strip() for c in raw):
            continue
        ids, bad = drive_ids(cell(attach))
        rows.append(Row(row=ri, manager=cell(COL_MANAGER), email=cell(COL_EMAIL),
                        status=cell(COL_STATUS),
                        values={k: cell(i) for k, i in variables.items()},
                        attachments=ids, attach_bad=bad))
    return rows, cols


def parse_tab_directory(values: list[list[str]]) -> tuple[dict[str, str], datetime | None]:
    tabs: dict[str, str] = {}
    updated = None
    for raw in values[1:]:
        if len(raw) >= 2 and str(raw[0]).strip() and str(raw[1]).strip():
            tabs[str(raw[0]).strip()] = str(raw[1]).strip()
            if len(raw) >= 4 and raw[3]:
                try:
                    ts_str = str(raw[3]).replace("Z", "+00:00")
                    dt = datetime.fromisoformat(ts_str)
                    # If naive (no timezone), assume UTC
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    # Use the minimum (oldest) timestamp
                    if updated is None or dt < updated:
                        updated = dt
                except (ValueError, TypeError):
                    # Unparseable timestamp, treat as unknown
                    pass
    return tabs, updated


def pending(rows: list[Row]) -> list[Row]:
    return [r for r in rows if r.email and r.status.strip().upper() != "O"]


def first_subjects(rows: list[Row], templates: dict[str, MailTemplate | Exception]) -> dict[str, str]:
    """담당자 탭별 첫 대상 행으로 치환한 제목 — 전체 발송 확인 창에 보인다. 템플릿 오류인 탭은 뺀다."""
    out: dict[str, str] = {}
    for r in rows:
        tpl = templates.get(r.manager)
        if r.manager not in out and isinstance(tpl, MailTemplate):
            out[r.manager] = render(tpl, r.values)[0]
    return out


def estimate_size(template: MailTemplate) -> int:
    # base64 는 4/3 배 + 헤더 여유
    return int((len(template.html.encode()) + sum(len(b) for b, _ in template.images.values())) * 1.37) + 4096


def ensure_message_size(raw: bytes) -> bytes:
    """조립한 메일이 Gmail 한도를 넘으면 보내지 않는다 (보내 봐야 구글이 거절한다)."""
    if len(raw) > MAX_MESSAGE_BYTES:
        raise RuntimeError(f"메일 용량 초과 ({len(raw) / 1024 / 1024:.1f}MB > "
                           f"{MAX_MESSAGE_BYTES // 1024 // 1024}MB) — 첨부를 줄여 주세요")
    return raw


def check(rows: list[Row], tab_dir: dict[str, str], dir_updated_at: datetime | None,
          templates: dict[str, MailTemplate | Exception], now: datetime, sent_today: int = 0) -> list[Issue]:
    issues: list[Issue] = []
    updated_txt = dir_updated_at.astimezone(_KST).strftime("%m-%d %H:%M") if dir_updated_at else "기록 없음"
    if dir_updated_at is None or now - dir_updated_at > TAB_DIR_STALE:
        issues.append(Issue(None, "warn", f"탭 목록이 오래됐습니다 (마지막 갱신 {updated_txt}). "
                                          "시트 메뉴 '템플릿 검증' 을 한 번 눌러 주세요"))
    if sent_today + len(rows) > DAILY_LIMIT:
        issues.append(Issue(None, "error", f"오늘 발송 한도 초과 예상: 오늘 {sent_today}통 + 대상 {len(rows)}통 > {DAILY_LIMIT}통"))
    seen: dict[str, int] = {}
    for r in rows:
        if not _EMAIL.match(r.email):
            issues.append(Issue(r.row, "error", f"이메일 형식 오류: {r.email}"))
        key = r.email.lower()
        if key in seen:
            issues.append(Issue(r.row, "warn", f"중복 이메일 ({seen[key]}행과 같음)"))
        else:
            seen[key] = r.row
        if r.manager not in tab_dir:
            near = difflib.get_close_matches(r.manager, list(tab_dir), n=1)
            hint = f" — 혹시 '{near[0]}'?" if near else ""
            issues.append(Issue(r.row, "error", f"탭 없음: '{r.manager}'{hint} (탭 목록 갱신 {updated_txt}, "
                                                "방금 만든 탭이면 잠시 뒤 다시)"))
            continue
        tpl = templates.get(r.manager)
        if isinstance(tpl, Exception) or tpl is None:
            issues.append(Issue(r.row, "error", f"템플릿 오류: {tpl}"))
            continue
        if estimate_size(tpl) > MAX_MESSAGE_BYTES:
            issues.append(Issue(r.row, "error", f"메일 용량 초과 예상 ({estimate_size(tpl) // 1024 // 1024}MB) — 이미지를 줄여 주세요"))
        subject, html = render(tpl, r.values)
        if "\r" in subject or "\n" in subject:  # 헤더가 끊긴다 (Bcc: 같은 줄을 끼워 넣을 수 있다)
            issues.append(Issue(r.row, "error", "제목에 줄바꿈이 들어갑니다 — 제목에 쓰는 시트 칸의 줄바꿈을 지우세요"))
        left = unreplaced(subject) + unreplaced_html(html)
        if left:
            issues.append(Issue(r.row, "error", f"치환 안 된 변수: {', '.join(left)} "
                                                "(시트 헤더에 없거나 Docs 에서 서식이 쪼개짐)"))
    return issues
