"""메일 발송 — 시트 행·탭 목록 파싱과 발송 전 점검. 순수 함수, 조회·LLM 없음."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.core.mail_template import MailTemplate, normalize_key, render, unreplaced, unreplaced_html

DOC_ID = "1Gofa6pusFe2t8o-ft1Xz5Be-sxmmUux5sQbu5vJA6S4"
TAB_DIR_SHEET = "_탭목록"
DAILY_LIMIT = 2000
MAX_MESSAGE_BYTES = 24 * 1024 * 1024
TAB_DIR_STALE = timedelta(hours=1)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_KST = timezone(timedelta(hours=9))


@dataclass
class Row:
    row: int
    manager: str
    email: str
    status: str
    values: dict[str, str] = field(default_factory=dict)


@dataclass
class Columns:
    email: int
    manager: int
    status: int
    note: int
    date: int | None
    variables: dict[str, int]


@dataclass
class Issue:
    row: int | None
    level: str
    message: str


def _find(headers: list[str], *words: str) -> int | None:
    for i, h in enumerate(headers):
        if normalize_key(h) in words:
            return i
    return None


def parse_rows(values: list[list[str]]) -> tuple[list[Row], Columns]:
    for hi, headers in enumerate(values):
        headers = [str(h) for h in headers]
        email, manager = _find(headers, "email", "이메일"), _find(headers, "담당자")
        status, note = _find(headers, "전송여부"), _find(headers, "비고")
        if email is not None and manager is not None and status is not None:
            break
    else:
        raise ValueError("헤더를 찾지 못했습니다: Email · 담당자 · 전송여부 열이 필요합니다")
    variables = {normalize_key(h.strip()[1:-1]): i for i, h in enumerate(headers)
                 if h.strip().startswith("{") and h.strip().endswith("}")}
    cols = Columns(email=email, manager=manager, status=status,
                   note=note if note is not None else status + 1,
                   date=_find(headers, "date", "전송날짜"), variables=variables)
    rows = []
    for ri, raw in enumerate(values[hi + 1:], start=hi + 2):
        cell = lambda i: (str(raw[i]).strip() if i is not None and i < len(raw) else "")
        if not any(str(c).strip() for c in raw):
            continue
        rows.append(Row(row=ri, manager=cell(manager), email=cell(email), status=cell(status),
                        values={k: cell(i) for k, i in variables.items()}))
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


def estimate_size(template: MailTemplate) -> int:
    # base64 는 4/3 배 + 헤더 여유
    return int((len(template.html.encode()) + sum(len(b) for b, _ in template.images.values())) * 1.37) + 4096


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
        left = unreplaced(subject) + unreplaced_html(html)
        if left:
            issues.append(Issue(r.row, "error", f"치환 안 된 변수: {', '.join(left)} "
                                                "(시트 헤더에 없거나 Docs 에서 서식이 쪼개짐)"))
    return issues
