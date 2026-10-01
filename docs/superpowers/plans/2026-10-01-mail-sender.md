# 메일 발송 전용 페이지 (`/mail-sender`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 마케팅팀이 Apps Script(gmailv2)로 하던 "시트 수신자 × Docs 담당자 탭" 대량 발송을 셀라의 전용 페이지에서 점검·미리보기·백그라운드 발송까지 하게 만든다.

**Architecture:** 탭 목록은 gmailv2 Apps Script 가 시트 숨김 탭 `_탭목록` 에 적고, 서버는 그 목록으로 탭 ID 를 찾아 `docs.google.com` 탭별 zip 내보내기(사용자 `drive.readonly` 토큰)로 HTML+이미지를 받는다. 순수 함수(`mail_template`·`mail_sender`·`mail_mime`)가 변환·점검·MIME 을 맡고, `mail_google` 이 구글 호출, `mail_sender_jobs` 가 MariaDB 기록 + 데몬 스레드 발송, `mail_sender_api` 가 페이지·API 를 맡는다. LLM 은 쓰지 않는다.

**Tech Stack:** Python 3.11 · FastAPI · google-auth / google-api-python-client · beautifulsoup4 4.12.3 + lxml 6.0.2 · PyMySQL(PooledDB) · 바닐라 JS · pytest

**Spec:** `docs/superpowers/specs/2026-09-30-mail-sender-design.md`

## Global Constraints

- ⛔ LLM 을 부르지 않는다 — 치환·점검·발송은 규칙이다
- ⛔ 확인 없이 보내지 않는다 — 전체 발송은 확인 창을 거친 POST 로만 시작. 채팅 경로로 열지 않는다
- ⛔ 서버 재시작 시 작업을 자동 재개하지 않는다 — `running` 은 기동 시 `interrupted` 로 바꾸고 마지막 미확정 행은 `unknown`
- ⛔ 이미지는 링크로 보내지 않는다 — zip 안의 파일을 `cid:` 인라인 첨부. 결과 HTML 에 `http(s)://` 이미지 src 0개
- ⛔ `From:` 은 `sendAs.list` 에서 `verificationStatus == "accepted"` 인 주소(또는 본인 주소)만 — 발송 직전 재대조
- ⛔ 권한·토큰 문제를 발송 결과(X)로 만들지 않는다 — 발송 전 409/403 으로 막는다. 같은 원인 연속 3회면 작업 중지
- ⛔ 행마다 발송 직전 G열을 다시 읽어 `O` 면 건너뛴다 (Apps Script 툴과 병행)
- ⛔ `ThreadPoolExecutor` 를 `with` 로 감싸지 않는다
- 새 페이지는 `app/api/middleware.py:61` `_page_request` 집합에 넣는다
- 캐시 번호는 손대지 않는다 (`asset_version.rewrite_html` 자동)
- 스타일은 `style.css` 기존 토큰만 (`--bg-surface`·`--border`·`--text-muted` …). JS 인라인 스타일 금지
- 구글 문서 ID: `1Gofa6pusFe2t8o-ft1Xz5Be-sxmmUux5sQbu5vJA6S4` · 탭 목록 시트 이름: `_탭목록`
- 새 스코프: `https://www.googleapis.com/auth/gmail.send`, `https://www.googleapis.com/auth/spreadsheets` — 메일 페이지에서만 증분 동의
- 일일 한도 기준 2,000통/계정 · 발송 간격 1초

## Review Focus

- **변수 `{KOL ID}` 가 내보내기 HTML 에서 여러 `<span>` 으로 쪼개진 경우** — 치환이 안 된 `{…}` 가 그대로 나가면 안 된다. 점검에서 ❌ (Task 2 `test_split_variable_is_reported`, Task 4 `test_check_reports_unreplaced`)
- **시트 B열 담당자 이름 뒤 공백 / 탭 제목 공백 차이** — 양쪽 strip 후 매칭, 그래도 없으면 비슷한 이름 제안 (Task 4 `test_manager_trailing_space_matches`)
- **토큰을 옛 `SCOPES` 로 불러 갱신하면 새로 받은 `gmail.send` 가 떨어지는 것** — 저장된 스코프 그대로 불러야 한다 (Task 5 `test_credentials_keep_stored_scopes`)
- **작업 도중 다른 툴(Apps Script)이 같은 행을 `O` 로 바꾼 경우** — 그 행은 건너뛴다 (Task 7 `test_row_marked_O_midway_is_skipped`)
- **이미지가 많아 메일이 Gmail 한도(25MB)를 넘는 템플릿** — 발송 전 ❌ (Task 4 `test_check_rejects_oversized_template`)

---

## File Structure

| 파일 | 책임 |
|---|---|
| (Apps Script) gmailv2 `Code.gs` | `writeTabDirectory()` · 10분 트리거 설치 · `validateTemplate` 끝에서 호출 |
| `app/core/mail_template.py` | 내보내기 zip → `MailTemplate`(제목·인라인 스타일 HTML·cid 이미지), 변수 찾기·치환 |
| `app/core/mail_mime.py` | `build_mime()` — multipart/related, UTF-8 헤더, cid 첨부 |
| `app/core/mail_sender.py` | 시트 행 파싱 · `_탭목록` 파싱 · 점검(`check`) · 발송 대상 |
| `app/core/mail_google.py` | 사용자 토큰으로 구글 호출: 탭 zip 내보내기, 시트 읽기/쓰기, sendAs, 발송, 부여 스코프 조회 |
| `app/core/google_auth.py` (수정) | `MAIL_SCOPES`, 증분 동의(`extra_scopes`, `include_granted_scopes`), 저장 스코프 그대로 로드 |
| `app/api/auth_routes.py` (수정) | `/auth/google/login?feature=mail` |
| `app/core/mail_sender_jobs.py` | 테이블 DDL · 기동 시 중단 처리 · 작업 실행 스레드 · 중지 |
| `app/api/mail_sender_api.py` | `/mail-sender` 페이지 + `/api/mail-sender/*` |
| `app/static/mail_sender.html` · `mail_sender.js` | 전용 페이지 |
| `app/frontend/chat.html` · `chat.js` (수정) | 사이드바 버튼 |
| `app/api/middleware.py` (수정) · `app/main.py` (수정) | 페이지 등록 · 라우터 · 기동 DDL |
| `tests/test_mail_template.py` · `test_mail_mime.py` · `test_mail_sender.py` · `test_mail_google_auth.py` · `test_mail_sender_jobs.py` · `test_mail_sender_api.py` | 회귀 |

---

### Task 1: Apps Script — 탭 목록을 `_탭목록` 시트에 기록

공유 스크립트라 저장 즉시 전원에게 적용된다. 브라우저 편집 절차: monaco `pushEditOperations` → "저장되지 않은 변경사항" 표시 확인 → 저장 버튼 → 새로고침으로 반영 확인.

**Files:**
- Modify: gmailv2 Apps Script `Code.gs` (https://script.google.com/u/0/home/projects/1wwePYlGEflEQeHh-Pi5exumTUL5x07g6oukmo2QMpinwZm8Q7YBQytvZ/edit)

**Interfaces:**
- Produces: 시트 `_탭목록` — 1행 헤더 `탭 제목 | 탭 ID | 상위 탭 | 갱신 시각`, 2행부터 탭 하나당 한 행. `갱신 시각` 은 ISO 문자열(모든 행 동일)

- [ ] **Step 1: 함수 추가** — `Code.gs` 끝에 붙인다

```javascript
// 셀라 /mail-sender 가 탭 이름 → 탭 ID 를 찾는 목록. Docs API 가 막혀 있어 여기서 적어 준다.
function writeTabDirectory() {
  var doc = DocumentApp.openById(CONFIG.DOC_ID);
  var rows = [];
  (function walk(tabs, parent) {
    tabs.forEach(function (t) {
      rows.push([t.getTitle().trim(), t.getId(), parent]);
      walk(t.getChildTabs(), t.getTitle().trim());
    });
  })(doc.getTabs(), '');
  var now = new Date().toISOString();
  rows = rows.map(function (r) { return r.concat([now]); });
  var ss = SpreadsheetApp.getActiveSpreadsheet() || SpreadsheetApp.openById(TAB_DIR_SPREADSHEET_ID_);
  var sh = ss.getSheetByName('_탭목록') || ss.insertSheet('_탭목록');
  sh.clearContents();
  sh.getRange(1, 1, 1, 4).setValues([['탭 제목', '탭 ID', '상위 탭', '갱신 시각']]);
  if (rows.length) sh.getRange(2, 1, rows.length, 4).setValues(rows);
  if (!sh.isSheetHidden()) sh.hideSheet();
  return rows.length;
}

var TAB_DIR_SPREADSHEET_ID_ = '1n08f1VIna9hZH03Zdws-31-G7RFaYJ3T7Eg7vQk636c';

// 한 번만 실행: 10분마다 탭 목록 갱신 트리거
function installTabDirectoryTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'writeTabDirectory') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('writeTabDirectory').timeBased().everyMinutes(10).create();
}
```

- [ ] **Step 2: `validateTemplate` 끝에서도 갱신** — `validateTemplate()` 의 `try {` 블록 첫 줄에 추가

```javascript
    try { writeTabDirectory(); } catch (e) { Logger.log('탭 목록 갱신 실패: ' + e.message); }
```

- [ ] **Step 3: 문법 확인 후 저장** — 편집기 JS 콘솔에서 `new Function(monaco.editor.getModels()[0].getValue())` 가 예외 없이 끝나야 한다. 저장 → 새로고침 → `writeTabDirectory` 존재 확인

- [ ] **Step 4: 실행 확인** — 편집기에서 `writeTabDirectory` 실행 → 로그/반환값 99 근처. 시트에 숨김 탭 `_탭목록` 이 생기고 2행부터 `강하니 | t.xxxx | 자동화 컨택 Template | 2026-…` 형태

- [ ] **Step 5: 트리거 설치** — `installTabDirectoryTrigger` 1회 실행 → 편집기 왼쪽 "트리거" 화면에 `writeTabDirectory · 시간 기반 · 10분` 1개

- [ ] **Step 6: 기록** — `docs/superpowers/specs/2026-09-30-mail-sender-design.md` 의 "템플릿 읽기" 절에 "Task 1 완료(날짜)·트리거 소유자 계정" 한 줄 추가 후 커밋

```bash
git add docs/superpowers/specs/2026-09-30-mail-sender-design.md
git commit -m "docs(mail-sender): Apps Script 탭 목록 기록 적용"
```

---

### Task 2: `mail_template` — 내보내기 zip → 메일 HTML

**Files:**
- Create: `app/core/mail_template.py`
- Test: `tests/test_mail_template.py`

**Interfaces:**
- Produces:
  - `class TemplateError(ValueError)`
  - `@dataclass(frozen=True) class MailTemplate: subject: str; html: str; images: dict[str, tuple[bytes, str]]` — 키 = cid(꺾쇠 없음), 값 = (바이트, MIME)
  - `template_from_export_zip(zip_bytes: bytes) -> MailTemplate`
  - `find_variables(template: MailTemplate) -> set[str]` — 소문자·앞뒤 공백 제거한 변수명
  - `render(template: MailTemplate, values: dict[str, str]) -> tuple[str, str]` — (제목, HTML). `values` 키는 소문자·strip
  - `unreplaced(text: str) -> list[str]` — 남아 있는 `{…}` 원문 목록
  - `normalize_key(s: str) -> str`

- [ ] **Step 1: 실패하는 테스트**

```python
# tests/test_mail_template.py
import io
import zipfile

import pytest

from app.core.mail_template import (
    TemplateError, find_variables, render, template_from_export_zip, unreplaced,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20


def _zip(html: str, images: dict | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Template.html", html)
        for name, data in (images or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


EXPORT = (
    '<html><head><meta content="text/html; charset=UTF-8" http-equiv="content-type">'
    '<style type="text/css">.c1{font-weight:700}.c2{color:#1155cc;text-decoration:underline}'
    '.c9{background-color:#ffffff;max-width:451.3pt;padding:72pt 72pt 72pt 72pt}'
    '.lst-kix_a-0>li:before{content:"\\0025cf  "}</style></head>'
    '<body class="c9 doc-content">'
    '<p><span>&#51228;&#47785;: [SKIN1004 X {name}] Offer</span></p>'
    '<p><span>Hi </span><span class="c1">{ name }</span><span>!</span></p>'
    '<p><a class="c2" href="https://form.example">FORM</a></p>'
    '<p><span><img src="images/image1.png" style="width: 200px; height: 100px;"></span></p>'
    '<p><span><img src="images/image1.png"></span></p>'
    "</body></html>"
)


def test_subject_extracted_and_removed():
    t = template_from_export_zip(_zip(EXPORT, {"images/image1.png": PNG}))
    assert t.subject == "[SKIN1004 X {name}] Offer"
    assert "제목" not in t.html


def test_classes_inlined_and_style_block_dropped():
    t = template_from_export_zip(_zip(EXPORT, {"images/image1.png": PNG}))
    assert "<style" not in t.html and 'class="' not in t.html
    assert "font-weight:700" in t.html
    assert "max-width" not in t.html and "padding:72pt" not in t.html


def test_images_become_cid_and_dedupe():
    t = template_from_export_zip(_zip(EXPORT, {"images/image1.png": PNG}))
    assert len(t.images) == 1
    cid = next(iter(t.images))
    assert t.html.count(f'src="cid:{cid}"') == 2
    assert t.images[cid] == (PNG, "image/png")
    assert "images/image1.png" not in t.html


def test_missing_subject_line_raises():
    html = EXPORT.replace("&#51228;&#47785;:", "Title:")
    with pytest.raises(TemplateError, match="제목:"):
        template_from_export_zip(_zip(html, {"images/image1.png": PNG}))


def test_missing_image_file_raises():
    with pytest.raises(TemplateError, match="image1.png"):
        template_from_export_zip(_zip(EXPORT))


def test_variables_and_render_escape_html():
    t = template_from_export_zip(_zip(EXPORT, {"images/image1.png": PNG}))
    assert find_variables(t) == {"name"}
    subject, html = render(t, {"name": "a<b"})
    assert subject == "[SKIN1004 X a<b] Offer"
    assert "a&lt;b" in html and "{" not in html


def test_split_variable_is_reported():
    html = EXPORT.replace('<span class="c1">{ name }</span>', '<span>{KOL </span><span class="c1">ID}</span>')
    t = template_from_export_zip(_zip(html, {"images/image1.png": PNG}))
    _, out = render(t, {"name": "x", "kol id": "y"})
    assert unreplaced(out) == ["{KOL ID}"]
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_mail_template.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.core.mail_template'`

- [ ] **Step 3: 구현**

```python
# app/core/mail_template.py
"""Docs 탭 내보내기(zip) → 메일 HTML. LLM 없음 — 규칙만.

내보내기 HTML 은 구글이 만든 것이다: 한글은 숫자 엔티티, 서식은 <style> 의 클래스.
Gmail 은 <style> 을 부분적으로만 받으므로 클래스를 인라인 style 로 펼친다.
이미지는 zip 안의 파일을 cid 로 붙인다 — 링크(contentUri)는 만료된다.
"""
from __future__ import annotations

import html as _html
import io
import mimetypes
import re
import zipfile
from dataclasses import dataclass

from bs4 import BeautifulSoup, NavigableString

_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_VAR = re.compile(r"\{\s*([^{}<>]+?)\s*\}")
_DROP_BODY_PROPS = {"max-width", "padding", "padding-top", "padding-bottom", "padding-left", "padding-right"}


class TemplateError(ValueError):
    pass


@dataclass(frozen=True)
class MailTemplate:
    subject: str
    html: str
    images: dict[str, tuple[bytes, str]]


def normalize_key(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _class_styles(css: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for selectors, body in _RULE.findall(css):
        for sel in selectors.split(","):
            m = re.fullmatch(r"\s*\.([A-Za-z0-9_-]+)\s*", sel)
            if m:
                decl = body.strip().rstrip(";")
                if decl:
                    out[m.group(1)] = out.get(m.group(1), "") + decl + ";"
    return out


def _drop_props(style: str, drop: set[str]) -> str:
    kept = []
    for part in style.split(";"):
        if ":" in part and part.split(":", 1)[0].strip().lower() not in drop:
            kept.append(part.strip())
    return ";".join(kept) + (";" if kept else "")


def template_from_export_zip(zip_bytes: bytes) -> MailTemplate:
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    names = z.namelist()
    html_names = [n for n in names if n.lower().endswith(".html")]
    if not html_names:
        raise TemplateError("내보내기 zip 에 HTML 이 없습니다")
    soup = BeautifulSoup(z.read(html_names[0]).decode("utf-8", "replace"), "lxml")

    styles = _class_styles("".join(s.get_text() for s in soup.find_all("style")))
    for s in soup.find_all("style"):
        s.decompose()
    body = soup.body
    if body is None:
        raise TemplateError("내보내기 HTML 에 본문이 없습니다")
    body_style = _drop_props("".join(styles.get(c, "") for c in body.get("class", [])), _DROP_BODY_PROPS)
    for el in body.find_all(class_=True):
        inline = "".join(styles.get(c, "") for c in el.get("class", []))
        if inline or el.get("style"):
            el["style"] = inline + (el.get("style") or "")
        del el["class"]

    subject = None
    for p in body.find_all("p"):
        text = p.get_text().strip()
        if not text:
            continue
        if text.startswith("제목:"):
            subject = text[len("제목:"):].strip()
            p.decompose()
        break
    if subject is None:
        raise TemplateError("탭 첫 줄이 '제목:' 으로 시작하지 않습니다")

    images: dict[str, tuple[bytes, str]] = {}
    cid_by_src: dict[str, str] = {}
    for img in body.find_all("img"):
        src = img.get("src", "")
        if src.startswith(("http://", "https://")):
            raise TemplateError(f"외부 이미지 링크는 보낼 수 없습니다: {src[:60]}")
        if src not in cid_by_src:
            if src not in names:
                raise TemplateError(f"zip 에 이미지 파일이 없습니다: {src}")
            cid = f"img{len(cid_by_src) + 1}@mail-sender"
            mime = mimetypes.guess_type(src)[0] or "application/octet-stream"
            images[cid] = (z.read(src), mime)
            cid_by_src[src] = cid
        img["src"] = f"cid:{cid_by_src[src]}"

    inner = "".join(str(c) for c in body.contents)
    html = f'<div style="{body_style}">{inner}</div>' if body_style else f"<div>{inner}</div>"
    return MailTemplate(subject=subject, html=html, images=images)


def _text_of(template: MailTemplate) -> str:
    return template.subject + "\n" + BeautifulSoup(template.html, "lxml").get_text()


def find_variables(template: MailTemplate) -> set[str]:
    return {normalize_key(m) for m in _VAR.findall(_text_of(template))}


def _sub(text: str, values: dict[str, str], escape: bool) -> str:
    def repl(m: re.Match) -> str:
        key = normalize_key(m.group(1))
        if key not in values:
            return m.group(0)
        v = values[key] or ""
        return _html.escape(v) if escape else v
    return _VAR.sub(repl, text)


def render(template: MailTemplate, values: dict[str, str]) -> tuple[str, str]:
    vals = {normalize_key(k): v for k, v in values.items()}
    soup = BeautifulSoup(template.html, "lxml")
    for node in list(soup.find_all(string=True)):
        if isinstance(node, NavigableString) and "{" in node:
            node.replace_with(BeautifulSoup(_sub(_html.escape(str(node)), vals, escape=True), "html.parser"))
    body = soup.body or soup
    return _sub(template.subject, vals, escape=False), "".join(str(c) for c in body.contents)


def unreplaced(text: str) -> list[str]:
    plain = BeautifulSoup(text, "lxml").get_text() if "<" in text else text
    return [m.group(0) for m in re.finditer(r"\{[^{}]*\}", plain)]
```

⚠️ `render` 는 텍스트 노드 단위로 치환한다 — `_html.escape` 후 `_sub(escape=True)` 로 값까지 이스케이프하므로 값의 `<` 는 `&lt;` 로 남는다.

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_mail_template.py -q`
Expected: 7 passed

- [ ] **Step 5: 실물 확인** — 실제 탭 하나를 내보내 변환해 본다 (스크래치 스크립트, 커밋하지 않음): `_탭목록` 에서 담당자 탭 ID 하나를 골라 `drive.readonly` 토큰으로 `https://docs.google.com/document/d/<DOC_ID>/export?format=zip&tab=<ID>` 를 받아 `template_from_export_zip` → `subject` 출력, `len(images)`, `"http" not in html` 확인

- [ ] **Step 6: 커밋**

```bash
git add app/core/mail_template.py tests/test_mail_template.py
git commit -m "feat(mail-sender): Docs 탭 내보내기 → 메일 HTML 변환 (인라인 스타일·cid 이미지)"
```

---

### Task 3: `mail_mime` — MIME 조립

**Files:**
- Create: `app/core/mail_mime.py`
- Test: `tests/test_mail_mime.py`

**Interfaces:**
- Consumes: `MailTemplate.images` 형식 `dict[str, tuple[bytes, str]]`
- Produces: `build_mime(*, sender: str, to: str, subject: str, html: str, images: dict[str, tuple[bytes, str]]) -> bytes`

- [ ] **Step 1: 실패하는 테스트**

```python
# tests/test_mail_mime.py
from email import message_from_bytes, policy

from app.core.mail_mime import build_mime

PNG = b"\x89PNG\r\n\x1a\n" + b"1" * 10


def _parse(raw: bytes):
    return message_from_bytes(raw, policy=policy.default)


def test_related_with_inline_images():
    raw = build_mime(sender="Marketing <mk@x.com>", to="a@b.com", subject="[SKIN1004] 안녕",
                     html='<p>hi<img src="cid:img1@mail-sender"></p>',
                     images={"img1@mail-sender": (PNG, "image/png")})
    m = _parse(raw)
    assert m["Subject"] == "[SKIN1004] 안녕"
    assert m["To"] == "a@b.com" and "mk@x.com" in m["From"]
    assert m.get_content_type() == "multipart/related"
    parts = list(m.iter_parts())
    assert parts[0].get_content_type() == "text/html"
    assert parts[1]["Content-ID"] == "<img1@mail-sender>"
    assert parts[1].get_content_disposition() == "inline"
    assert parts[1].get_content() == PNG


def test_no_images_is_plain_html():
    m = _parse(build_mime(sender="a@x.com", to="b@y.com", subject="s", html="<p>x</p>", images={}))
    assert m.get_content_type() == "text/html"
    assert "<p>x</p>" in m.get_content()
```

- [ ] **Step 2: 실패 확인** — Run: `python -m pytest tests/test_mail_mime.py -q` → FAIL (모듈 없음)

- [ ] **Step 3: 구현**

```python
# app/core/mail_mime.py
"""메일 MIME 조립 — 이미지는 cid 인라인 첨부 (multipart/related)."""
from __future__ import annotations

from email.message import EmailMessage
from email.policy import SMTP


def build_mime(*, sender: str, to: str, subject: str, html: str,
               images: dict[str, tuple[bytes, str]]) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    full = ('<html><head><meta charset="UTF-8"></head>'
            '<body style="font-family:Arial,sans-serif;font-size:11pt;">' + html + "</body></html>")
    msg.set_content(full, subtype="html", charset="utf-8")
    if images:
        msg.make_related()
        for cid, (data, mime) in images.items():
            maintype, subtype = mime.split("/", 1)
            msg.add_related(data, maintype=maintype, subtype=subtype, cid=f"<{cid}>",
                            disposition="inline", filename=cid.split("@")[0] + "." + subtype.replace("jpeg", "jpg"))
    return msg.as_bytes(policy=SMTP)
```

- [ ] **Step 4: 통과 확인** — Run: `python -m pytest tests/test_mail_mime.py -q` → 2 passed

- [ ] **Step 5: 커밋**

```bash
git add app/core/mail_mime.py tests/test_mail_mime.py
git commit -m "feat(mail-sender): MIME 조립 (cid 인라인 이미지)"
```

---

### Task 4: `mail_sender` — 시트 행·탭 목록 파싱과 점검

**Files:**
- Create: `app/core/mail_sender.py`
- Test: `tests/test_mail_sender.py`

**Interfaces:**
- Consumes: `MailTemplate`, `find_variables`, `render`, `unreplaced`, `normalize_key` (Task 2)
- Produces:
  - 상수 `DOC_ID = "1Gofa6pusFe2t8o-ft1Xz5Be-sxmmUux5sQbu5vJA6S4"`, `TAB_DIR_SHEET = "_탭목록"`, `DAILY_LIMIT = 2000`, `MAX_MESSAGE_BYTES = 24 * 1024 * 1024`, `TAB_DIR_STALE = timedelta(hours=1)`
  - `@dataclass class Row: row: int; manager: str; email: str; status: str; values: dict[str, str]` (row = 시트 1-based 행 번호)
  - `@dataclass class Columns: email: int; manager: int; status: int; note: int; date: int | None; variables: dict[str, int]` (0-based 열 인덱스, variables 키=normalize_key(괄호 안))
  - `@dataclass class Issue: row: int | None; level: str; message: str` (level = `"error"|"warn"`)
  - `parse_rows(values: list[list[str]]) -> tuple[list[Row], Columns]` — 헤더 못 찾으면 `ValueError`
  - `parse_tab_directory(values: list[list[str]]) -> tuple[dict[str, str], datetime | None]` — 제목(strip) → 탭 ID, 갱신 시각
  - `pending(rows: list[Row]) -> list[Row]` — 이메일 있고 status != "O"
  - `check(rows, tab_dir, dir_updated_at, templates, now, sent_today=0) -> list[Issue]` — `templates: dict[str, MailTemplate | Exception]` (키 = 담당자 이름)
  - `estimate_size(template: MailTemplate) -> int`

- [ ] **Step 1: 실패하는 테스트**

```python
# tests/test_mail_sender.py
from datetime import datetime, timedelta, timezone

import pytest

from app.core.mail_sender import (
    MAX_MESSAGE_BYTES, check, parse_rows, parse_tab_directory, pending,
)
from app.core.mail_template import MailTemplate

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
SHEET = [
    ["Date", "담당자", "{KOL ID}", "Email", "{value1}", "{value2}", "전송여부", "비고"],
    ["", "강하니 ", "maria_jflores", "maria@gmail.com", "skin1004", "", "", ""],
    ["2026-09-30", "강하니", "done", "done@gmail.com", "", "", "O", ""],
    ["", "고도연", "bob", "bob@x", "", "", "", ""],
    ["", "없는사람", "c", "c@x.com", "", "", "", ""],
    ["", "강하니", "dup", "maria@gmail.com", "", "", "", ""],
]
TAB_DIR = [["탭 제목", "탭 ID", "상위 탭", "갱신 시각"],
           ["강하니", "t.a", "자동화 컨택 Template", "2026-10-01T08:55:00Z"],
           ["고도연", "t.b", "자동화 컨택 Template", "2026-10-01T08:55:00Z"],
           ["강하늬", "t.c", "", "2026-10-01T08:55:00Z"]]
TPL = MailTemplate(subject="[SKIN1004 X {KOL ID}] Offer", html="<p>Hi {kol id} {value1}</p>", images={})


def test_parse_rows_by_header_and_strip():
    rows, cols = parse_rows(SHEET)
    assert (cols.manager, cols.email, cols.status, cols.note, cols.date) == (1, 3, 6, 7, 0)
    assert set(cols.variables) == {"kol id", "value1", "value2"}
    assert rows[0].row == 2 and rows[0].manager == "강하니" and rows[0].values["kol id"] == "maria_jflores"


def test_parse_rows_header_not_on_first_line():
    rows, _ = parse_rows([["메모"], *SHEET])
    assert rows[0].row == 3


def test_parse_rows_missing_header_raises():
    with pytest.raises(ValueError, match="Email"):
        parse_rows([["담당자", "전송여부"]])


def test_pending_skips_O_and_blank_email():
    rows, _ = parse_rows(SHEET)
    assert [r.row for r in pending(rows)] == [2, 4, 5, 6]


def test_tab_directory():
    tabs, updated = parse_tab_directory(TAB_DIR)
    assert tabs["강하니"] == "t.a" and updated == datetime(2026, 10, 1, 8, 55, tzinfo=timezone.utc)


def _issues(**kw):
    rows, _ = parse_rows(SHEET)
    tabs, updated = parse_tab_directory(TAB_DIR)
    args = dict(rows=pending(rows), tab_dir=tabs, dir_updated_at=updated,
                templates={"강하니": TPL, "고도연": TPL}, now=NOW)
    args.update(kw)
    return check(**args)


def test_manager_trailing_space_matches():
    assert not [i for i in _issues() if i.row == 2 and i.level == "error"]


def test_check_reports_missing_tab_with_suggestion_and_refresh_time():
    msg = [i.message for i in _issues() if i.row == 5][0]
    assert "탭 없음" in msg and "08:55" in msg


def test_check_bad_email_and_duplicate():
    issues = _issues()
    assert any(i.row == 4 and "이메일" in i.message and i.level == "error" for i in issues)
    assert any(i.row == 6 and "중복" in i.message and i.level == "warn" for i in issues)


def test_check_reports_unreplaced():
    tpl = MailTemplate(subject="s", html="<p>{KOL </p><p>ID}</p>", images={})
    issues = _issues(templates={"강하니": tpl, "고도연": tpl})
    assert any(i.row == 2 and "{KOL" in i.message for i in issues)


def test_check_template_error_is_error():
    issues = _issues(templates={"강하니": ValueError("탭 첫 줄이 '제목:' 으로 시작하지 않습니다"), "고도연": TPL})
    assert any(i.row == 2 and "제목:" in i.message and i.level == "error" for i in issues)


def test_check_stale_directory_warns():
    issues = _issues(now=NOW + timedelta(hours=2))
    assert any(i.row is None and "탭 목록" in i.message for i in issues)


def test_check_rejects_oversized_template():
    big = MailTemplate(subject="s", html="<p>x</p>", images={"a@m": (b"0" * (MAX_MESSAGE_BYTES + 1), "image/png")})
    issues = _issues(templates={"강하니": big, "고도연": TPL})
    assert any(i.row == 2 and "용량" in i.message and i.level == "error" for i in issues)


def test_check_daily_limit():
    issues = _issues(sent_today=1999)
    assert any(i.row is None and "한도" in i.message and i.level == "error" for i in issues)
```

- [ ] **Step 2: 실패 확인** — Run: `python -m pytest tests/test_mail_sender.py -q` → FAIL (모듈 없음)

- [ ] **Step 3: 구현**

```python
# app/core/mail_sender.py
"""메일 발송 — 시트 행·탭 목록 파싱과 발송 전 점검. 순수 함수, 조회·LLM 없음."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.core.mail_template import MailTemplate, normalize_key, render, unreplaced

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
    variables = {normalize_key(h[1:-1]): i for i, h in enumerate(headers)
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
            if len(raw) >= 4 and raw[3] and updated is None:
                updated = datetime.fromisoformat(str(raw[3]).replace("Z", "+00:00"))
    return tabs, updated


def pending(rows: list[Row]) -> list[Row]:
    return [r for r in rows if r.email and r.status != "O"]


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
        left = unreplaced(subject) + unreplaced(html)
        if left:
            issues.append(Issue(r.row, "error", f"치환 안 된 변수: {', '.join(left)} "
                                                "(시트 헤더에 없거나 Docs 에서 서식이 쪼개짐)"))
    return issues
```

- [ ] **Step 4: 통과 확인** — Run: `python -m pytest tests/test_mail_sender.py -q` → 13 passed

- [ ] **Step 5: 커밋**

```bash
git add app/core/mail_sender.py tests/test_mail_sender.py
git commit -m "feat(mail-sender): 시트 행·탭 목록 파싱과 발송 전 점검"
```

---

### Task 5: 구글 권한(증분 동의)과 `mail_google` 호출부

**Files:**
- Modify: `app/core/google_auth.py:27-31` (상수), `:175` (로드), `:311-334` (`get_auth_url`), `:336-` (`exchange_code`)
- Modify: `app/api/auth_routes.py:192` (`/login`)
- Create: `app/core/mail_google.py`
- Test: `tests/test_mail_google_auth.py`

**Interfaces:**
- Produces:
  - `google_auth.MAIL_SCOPES: list[str]` = `["https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/spreadsheets"]`
  - `GoogleAuthManager.get_auth_url(user_email, *, state, redirect_uri="", extra_scopes: list[str] | None = None) -> str`
  - `mail_google.granted_scopes(creds) -> set[str]` (tokeninfo)
  - `mail_google.missing_mail_scopes(creds) -> list[str]`
  - `mail_google.export_tab_zip(creds, doc_id: str, tab_id: str) -> bytes`
  - `mail_google.sheet_titles(creds, spreadsheet_id: str) -> list[str]`
  - `mail_google.read_sheet(creds, spreadsheet_id: str, title: str) -> list[list[str]]`
  - `mail_google.read_cell(creds, spreadsheet_id: str, title: str, row: int, col: int) -> str` (col 0-based)
  - `mail_google.write_cells(creds, spreadsheet_id: str, title: str, row: int, updates: dict[int, str]) -> None` (col 0-based)
  - `mail_google.send_as_addresses(creds) -> list[dict]` — `[{"email","display","default"}]`, accepted 만
  - `mail_google.send_raw(creds, raw: bytes) -> str` — Gmail message id
  - `mail_google.spreadsheet_id_from(url_or_id: str) -> str`

- [ ] **Step 1: 실패하는 테스트**

```python
# tests/test_mail_google_auth.py
import json
from unittest.mock import MagicMock, patch

from app.core import google_auth, mail_google


def test_mail_scopes_not_in_default_scopes():
    assert "https://www.googleapis.com/auth/gmail.send" in google_auth.MAIL_SCOPES
    assert not set(google_auth.MAIL_SCOPES) & set(google_auth.SCOPES)


def test_auth_url_requests_extra_scopes_incrementally(monkeypatch):
    m = google_auth.GoogleAuthManager()
    flow = MagicMock()
    flow.authorization_url.return_value = ("https://accounts.google.com/x", "s")
    with patch.object(google_auth.Flow, "from_client_config", return_value=flow) as fc:
        m.get_auth_url("a@b.com", state="s", redirect_uri="http://h/cb", extra_scopes=google_auth.MAIL_SCOPES)
    assert set(google_auth.MAIL_SCOPES) <= set(fc.call_args.kwargs["scopes"])
    assert flow.authorization_url.call_args.kwargs["include_granted_scopes"] == "true"


def test_credentials_keep_stored_scopes(tmp_path, monkeypatch):
    m = google_auth.GoogleAuthManager()
    stored = google_auth.SCOPES + google_auth.MAIL_SCOPES
    p = tmp_path / "a_at_b_com.json"
    p.write_text(json.dumps({"token": "t", "refresh_token": "r", "client_id": "c", "client_secret": "s",
                             "token_uri": "https://oauth2.googleapis.com/token", "scopes": stored}))
    monkeypatch.setattr(m, "_token_path", lambda email: p, raising=False)
    with patch("google.oauth2.credentials.Credentials.refresh"):
        creds = m.get_credentials("a@b.com")
    assert set(google_auth.MAIL_SCOPES) <= set(creds.scopes)


def test_missing_mail_scopes_uses_tokeninfo():
    creds = MagicMock(token="t")
    resp = MagicMock(ok=True)
    resp.json.return_value = {"scope": "https://www.googleapis.com/auth/gmail.send openid"}
    with patch("app.core.mail_google.requests.get", return_value=resp):
        assert mail_google.missing_mail_scopes(creds) == ["https://www.googleapis.com/auth/spreadsheets"]


def test_spreadsheet_id_from_url():
    url = "https://docs.google.com/spreadsheets/d/1n08f1VIna9hZH03Zdws-31-G7RFaYJ3T7Eg7vQk636c/edit?gid=0"
    assert mail_google.spreadsheet_id_from(url) == "1n08f1VIna9hZH03Zdws-31-G7RFaYJ3T7Eg7vQk636c"


def test_export_rejects_non_zip():
    sess = MagicMock()
    sess.get.return_value = MagicMock(status_code=200, content=b"<html>login</html>")
    with patch("app.core.mail_google.AuthorizedSession", return_value=sess):
        try:
            mail_google.export_tab_zip(MagicMock(), "doc", "t.a")
        except RuntimeError as e:
            assert "zip" in str(e)
        else:
            raise AssertionError("expected RuntimeError")
```

⚠️ `test_credentials_keep_stored_scopes` 의 `_token_path` 는 실제 메서드 이름에 맞춘다 — 먼저 `google_auth.py:80-83` 에서 토큰 경로를 만드는 코드를 읽고, 메서드가 없으면 그 3줄을 `_token_path(self, email) -> Path` 로 뽑아낸 뒤(동작 변경 없음) 테스트가 그것을 패치하게 한다.

- [ ] **Step 2: 실패 확인** — Run: `python -m pytest tests/test_mail_google_auth.py -q` → FAIL (`MAIL_SCOPES` 없음)

- [ ] **Step 3: `google_auth.py` 수정**

`SCOPES` 바로 아래(31줄 뒤)에 추가:

```python
# 메일 발송 페이지(/mail-sender)에서만 증분 동의로 받는다 — SCOPES 에 넣으면
# 인증서류 찾기만 쓰는 사람까지 재동의해야 한다.
MAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/spreadsheets",
]
# include_granted_scopes 로 받은 토큰은 요청보다 넓은 스코프를 돌려준다 — oauthlib 의 '스코프 변경' 예외를 끈다
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
```

(`import os` 가 없으면 상단에 추가)

`:175` 의 저장 토큰 로드를 **저장된 스코프 그대로** 불러오게 바꾼다:

```python
# 전: creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
creds = Credentials.from_authorized_user_file(str(token_path))
```

⛔ 이유: `scopes=SCOPES` 로 불러 갱신하면 갱신 요청이 옛 스코프로 나가 새로 받은 `gmail.send` 가 빠진 토큰이 돌아온다.

`get_auth_url` 시그니처와 본문:

```python
def get_auth_url(self, user_email: str, *, state: str, redirect_uri: str = "",
                 extra_scopes: list[str] | None = None) -> str:
    uri = redirect_uri or self.settings.google_oauth_redirect_uri
    flow = Flow.from_client_config(self._client_config(), scopes=SCOPES + list(extra_scopes or []),
                                   redirect_uri=uri)
    url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        state=state,
        login_hint=user_email,
        include_granted_scopes="true",
    )
    return url
```

(`self._client_config()` 는 기존 코드가 `Flow.from_client_config` 에 넘기던 dict 를 그대로 쓴다 — 기존 줄이 인라인 dict 면 그대로 둔다)

`exchange_code` 의 `Flow.from_client_config(..., scopes=SCOPES, ...)` 를 `scopes=SCOPES + MAIL_SCOPES` 로 바꾼다 (OAUTHLIB_RELAX 로 실제 부여 범위와 달라도 통과).

- [ ] **Step 4: `auth_routes.py` `/login` 수정** — 쿼리 `feature=mail` 이면 메일 스코프를 함께 요청

```python
@auth_router.get("/login")
async def google_login(request: Request, feature: str = "", user: User = Depends(get_current_user)):
    ...  # 기존 redirect URI 확인·issue_state 그대로
    extra = MAIL_SCOPES if feature == "mail" else None
    url = _get_auth_manager().get_auth_url(user.email, state=state, redirect_uri=redirect_uri, extra_scopes=extra)
    return RedirectResponse(url)
```

(`from app.core.google_auth import MAIL_SCOPES` 추가)

- [ ] **Step 5: `mail_google.py` 작성**

```python
# app/core/mail_google.py
"""메일 발송용 구글 호출 — 전부 사용자 본인 토큰. WAS 프록시는 HTTPS_PROXY 환경변수로 탄다."""
from __future__ import annotations

import io
import re

import requests
from google.auth.transport.requests import AuthorizedSession, Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from app.core.google_auth import MAIL_SCOPES


def _fresh(creds):
    if not creds.valid:
        creds.refresh(Request())
    return creds


def granted_scopes(creds) -> set[str]:
    r = requests.get("https://oauth2.googleapis.com/tokeninfo",
                     params={"access_token": _fresh(creds).token}, timeout=20)
    return set((r.json().get("scope") or "").split()) if r.ok else set()


def missing_mail_scopes(creds) -> list[str]:
    have = granted_scopes(creds)
    return [s for s in MAIL_SCOPES if s not in have]


def spreadsheet_id_from(url_or_id: str) -> str:
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", url_or_id)
    return m.group(1) if m else url_or_id.strip()


def export_tab_zip(creds, doc_id: str, tab_id: str) -> bytes:
    r = AuthorizedSession(_fresh(creds)).get(
        f"https://docs.google.com/document/d/{doc_id}/export",
        params={"format": "zip", "tab": tab_id}, timeout=180)
    if r.status_code != 200 or r.content[:2] != b"PK":
        raise RuntimeError(f"탭 내보내기 실패 (HTTP {r.status_code}) — zip 이 아닙니다. 문서 권한을 확인하세요")
    return r.content


def _sheets(creds):
    return build("sheets", "v4", credentials=_fresh(creds), cache_discovery=False)


def sheet_titles(creds, spreadsheet_id: str) -> list[str]:
    meta = _sheets(creds).spreadsheets().get(spreadsheetId=spreadsheet_id,
                                             fields="sheets(properties(title))").execute()
    return [s["properties"]["title"] for s in meta.get("sheets", [])]


def read_sheet(creds, spreadsheet_id: str, title: str) -> list[list[str]]:
    res = _sheets(creds).spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{title}'", valueRenderOption="FORMATTED_VALUE").execute()
    return res.get("values", [])


def _col(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def read_cell(creds, spreadsheet_id: str, title: str, row: int, col: int) -> str:
    res = _sheets(creds).spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{title}'!{_col(col)}{row}").execute()
    vals = res.get("values", [])
    return str(vals[0][0]).strip() if vals and vals[0] else ""


def write_cells(creds, spreadsheet_id: str, title: str, row: int, updates: dict[int, str]) -> None:
    data = [{"range": f"'{title}'!{_col(c)}{row}", "values": [[v]]} for c, v in updates.items()]
    _sheets(creds).spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id, body={"valueInputOption": "USER_ENTERED", "data": data}).execute()


def send_as_addresses(creds) -> list[dict]:
    gm = build("gmail", "v1", credentials=_fresh(creds), cache_discovery=False)
    items = gm.users().settings().sendAs().list(userId="me").execute().get("sendAs", [])
    out = []
    for s in items:
        if s.get("isPrimary") or s.get("verificationStatus") == "accepted":
            out.append({"email": s["sendAsEmail"], "display": s.get("displayName", ""),
                        "default": bool(s.get("isDefault"))})
    return out


def send_raw(creds, raw: bytes) -> str:
    gm = build("gmail", "v1", credentials=_fresh(creds), cache_discovery=False)
    media = MediaIoBaseUpload(io.BytesIO(raw), mimetype="message/rfc822", resumable=True)
    return gm.users().messages().send(userId="me", body={}, media_body=media).execute()["id"]
```

⚠️ `sendAs.list` 는 `gmail.readonly` 로 된다(설계 문서의 열린 확인). Task 9 실물 확인에서 403 이면 `MAIL_SCOPES` 에 `https://www.googleapis.com/auth/gmail.settings.basic` 을 더한다.

- [ ] **Step 6: 통과 확인** — Run: `python -m pytest tests/test_mail_google_auth.py tests/test_coa_finder_api.py -q` → 전부 PASS (기존 구글 연결 회귀 포함)

- [ ] **Step 7: 커밋**

```bash
git add app/core/google_auth.py app/api/auth_routes.py app/core/mail_google.py tests/test_mail_google_auth.py
git commit -m "feat(mail-sender): 메일 스코프 증분 동의 + 구글 호출부(탭 내보내기·시트·sendAs·발송)"
```

---

### Task 6: 페이지·점검·미리보기·테스트 발송 API + 사이드바

**Files:**
- Create: `app/api/mail_sender_api.py`, `app/static/mail_sender.html`, `app/static/mail_sender.js`
- Modify: `app/main.py:33-34,405-406`, `app/api/middleware.py:61`, `app/frontend/chat.html:82-87 뒤`, `app/frontend/chat.js:1638 뒤`
- Test: `tests/test_mail_sender_api.py`

**Interfaces:**
- Consumes: Task 2·3·4·5 전부
- Produces (JSON API, 모두 `Depends(get_current_user)`):
  - `GET /mail-sender` — 페이지 (비로그인 → 302 `/login`)
  - `GET /api/mail-sender/status` → `{"connected": bool, "missing_scopes": [..], "senders": [{"email","display","default"}], "login_url": "/auth/google/login?feature=mail"}`
  - `POST /api/mail-sender/sheets` `{"spreadsheet": url}` → `{"spreadsheet_id", "sheets": ["gmail_…"]}`
  - `POST /api/mail-sender/check` `{"spreadsheet_id","sheet"}` → `{"rows":[{"row","manager","email","status"}], "issues":[{"row","level","message"}], "pending": n, "errors": n}`
  - `POST /api/mail-sender/preview` `{"spreadsheet_id","sheet","row"}` → `{"subject","html"}` (cid 이미지는 `data:` URI 로 바꿔 화면용)
  - `POST /api/mail-sender/test-send` `{"spreadsheet_id","sheet","row","sender"}` → `{"message_id"}` — **수신자 = 로그인 사용자 본인**
  - 내부 헬퍼 `_load(creds, spreadsheet_id, sheet) -> tuple[list[Row], Columns, dict[str, MailTemplate|Exception], dict[str,str], datetime|None]` — Task 7 이 재사용
  - 공통 가드 `_ready_creds(user) -> creds` — 미연결 409 `{"detail": "구글 계정이 연결되어 있지 않습니다"}`, 스코프 누락 403 `{"detail": {"missing_scopes": [...]}}`

- [ ] **Step 1: 실패하는 테스트**

```python
# tests/test_mail_sender_api.py
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.test_coa_finder_api import client  # noqa: F401  (같은 로그인 픽스처 재사용)

ROOT = Path(__file__).resolve().parents[1]


def test_wiring():
    main = (ROOT / "app/main.py").read_text(encoding="utf-8")
    assert "include_router(mail_sender_router)" in main
    mw = (ROOT / "app/api/middleware.py").read_text(encoding="utf-8")
    assert '"/mail-sender"' in mw
    html = (ROOT / "app/frontend/chat.html").read_text(encoding="utf-8")
    assert 'id="btn-mail-sender"' in html
    js = (ROOT / "app/frontend/chat.js").read_text(encoding="utf-8")
    assert re.search(r'getElementById\("btn-mail-sender"\)[\s\S]{0,200}"/mail-sender"', js)


def test_page_redirects_without_session(client):
    client.cookies.clear()
    r = client.get("/mail-sender", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/login"


def test_status_not_connected(client):
    with patch("app.api.mail_sender_api._credentials", return_value=None):
        r = client.get("/api/mail-sender/status")
    assert r.status_code == 200 and r.json()["connected"] is False


def test_check_blocks_missing_scopes(client):
    with patch("app.api.mail_sender_api._credentials", return_value=object()), \
         patch("app.api.mail_sender_api.mail_google.missing_mail_scopes",
               return_value=["https://www.googleapis.com/auth/gmail.send"]):
        r = client.post("/api/mail-sender/check", json={"spreadsheet_id": "x", "sheet": "gmail_a"})
    assert r.status_code == 403
    assert "gmail.send" in str(r.json()["detail"])


def test_test_send_goes_only_to_self(client):
    from app.core.mail_template import MailTemplate
    tpl = MailTemplate(subject="[S] {kol id}", html="<p>{kol id}</p>", images={})
    rows_cols = ([__import__("app.core.mail_sender", fromlist=["Row"]).Row(2, "강하니", "kol@x.com", "", {"kol id": "k"})],
                 None, {"강하니": tpl}, {"강하니": "t.a"}, None)
    sent = {}
    with patch("app.api.mail_sender_api._credentials", return_value=object()), \
         patch("app.api.mail_sender_api.mail_google.missing_mail_scopes", return_value=[]), \
         patch("app.api.mail_sender_api.mail_google.send_as_addresses",
               return_value=[{"email": "me@skin1004korea.com", "display": "", "default": True}]), \
         patch("app.api.mail_sender_api._load", return_value=rows_cols), \
         patch("app.api.mail_sender_api.mail_google.send_raw",
               side_effect=lambda c, raw: sent.setdefault("raw", raw) and "id1"):
        r = client.post("/api/mail-sender/test-send",
                        json={"spreadsheet_id": "x", "sheet": "gmail_a", "row": 2, "sender": "me@skin1004korea.com"})
    assert r.status_code == 200
    assert b"kol@x.com" not in sent["raw"]


def test_sender_not_in_sendas_is_rejected(client):
    with patch("app.api.mail_sender_api._credentials", return_value=object()), \
         patch("app.api.mail_sender_api.mail_google.missing_mail_scopes", return_value=[]), \
         patch("app.api.mail_sender_api.mail_google.send_as_addresses",
               return_value=[{"email": "me@skin1004korea.com", "display": "", "default": True}]):
        r = client.post("/api/mail-sender/test-send",
                        json={"spreadsheet_id": "x", "sheet": "gmail_a", "row": 2, "sender": "marketing@x.com"})
    assert r.status_code == 400 and "보내는 주소" in r.json()["detail"]
```

⚠️ 테스트의 로그인 사용자 이메일은 `tests/test_coa_finder_api.py` 의 `_User` 값이다 — 그 값을 읽어 `test_test_send_goes_only_to_self` 가 To 헤더가 그 주소인지도 `assert` 하도록 한 줄 더한다.

- [ ] **Step 2: 실패 확인** — Run: `python -m pytest tests/test_mail_sender_api.py -q` → FAIL

- [ ] **Step 3: API 구현**

```python
# app/api/mail_sender_api.py
"""메일 발송 전용 페이지 — 점검·미리보기·테스트 발송·작업. LLM 없음."""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

from app.api.auth_middleware import _extract_user_id, get_current_user
from app.core import mail_google
from app.core.google_auth import GoogleAuthManager
from app.core.mail_mime import build_mime
from app.core.mail_sender import DOC_ID, TAB_DIR_SHEET, check, parse_rows, parse_tab_directory, pending
from app.core.mail_template import TemplateError, render, template_from_export_zip

router = APIRouter()
_auth = GoogleAuthManager()


def _credentials(email: str):
    return _auth.get_credentials(email)


def _ready_creds(user):
    creds = _credentials(user.email)
    if creds is None:
        raise HTTPException(409, "구글 계정이 연결되어 있지 않습니다")
    missing = mail_google.missing_mail_scopes(creds)
    if missing:
        raise HTTPException(403, {"missing_scopes": missing,
                                  "message": "메일 발송 권한이 빠져 있습니다. '구글 다시 연결' 에서 모든 항목을 허용해 주세요"})
    return creds


def _check_sender(creds, sender: str) -> str:
    allowed = {s["email"].lower() for s in mail_google.send_as_addresses(creds)}
    if sender.lower() not in allowed:
        raise HTTPException(400, f"보내는 주소 '{sender}' 는 이 계정의 Gmail 에 등록된 주소가 아닙니다")
    return sender


def _load(creds, spreadsheet_id: str, sheet: str):
    rows, cols = parse_rows(mail_google.read_sheet(creds, spreadsheet_id, sheet))
    tab_dir, updated = parse_tab_directory(mail_google.read_sheet(creds, spreadsheet_id, TAB_DIR_SHEET))
    templates = {}
    for manager in {r.manager for r in pending(rows)}:
        tab_id = tab_dir.get(manager)
        if not tab_id:
            continue
        try:
            templates[manager] = template_from_export_zip(mail_google.export_tab_zip(creds, DOC_ID, tab_id))
        except (TemplateError, RuntimeError) as e:
            templates[manager] = e
    return rows, cols, templates, tab_dir, updated


async def _sent_today(email: str) -> int:
    return 0  # Task 7 에서 jobs.sent_today 로 바꾼다 (발송 기록 테이블이 생긴 뒤)


class SheetsIn(BaseModel):
    spreadsheet: str


class SheetIn(BaseModel):
    spreadsheet_id: str
    sheet: str


class RowIn(SheetIn):
    row: int
    sender: str = ""


@router.get("/mail-sender")
async def mail_sender_page(request: Request):
    try:
        _extract_user_id(request)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=302)
    return FileResponse("app/static/mail_sender.html", media_type="text/html")


@router.get("/api/mail-sender/status")
async def status(user=Depends(get_current_user)):
    creds = _credentials(user.email)
    out = {"connected": creds is not None, "missing_scopes": [], "senders": [],
           "login_url": "/auth/google/login?feature=mail", "me": user.email}
    if creds is None:
        return out
    out["missing_scopes"] = await asyncio.to_thread(mail_google.missing_mail_scopes, creds)
    if not out["missing_scopes"]:
        out["senders"] = await asyncio.to_thread(mail_google.send_as_addresses, creds)
    return out


@router.post("/api/mail-sender/sheets")
async def sheets(body: SheetsIn, user=Depends(get_current_user)):
    creds = _ready_creds(user)
    sid = mail_google.spreadsheet_id_from(body.spreadsheet)
    titles = await asyncio.to_thread(mail_google.sheet_titles, creds, sid)
    return {"spreadsheet_id": sid, "sheets": [t for t in titles if t.startswith("gmail_")]}


@router.post("/api/mail-sender/check")
async def check_sheet(body: SheetIn, user=Depends(get_current_user)):
    creds = _ready_creds(user)
    rows, _, templates, tab_dir, updated = await asyncio.to_thread(_load, creds, body.spreadsheet_id, body.sheet)
    issues = check(pending(rows), tab_dir, updated, templates, datetime.now(timezone.utc),
                   sent_today=await _sent_today(user.email))
    return {"rows": [{"row": r.row, "manager": r.manager, "email": r.email, "status": r.status} for r in rows],
            "issues": [i.__dict__ for i in issues],
            "pending": len(pending(rows)),
            "errors": sum(1 for i in issues if i.level == "error")}


def _row_message(rows, templates, row: int):
    r = next((x for x in rows if x.row == row), None)
    if r is None:
        raise HTTPException(404, f"{row}행을 찾지 못했습니다")
    tpl = templates.get(r.manager)
    if tpl is None or isinstance(tpl, Exception):
        raise HTTPException(400, f"{r.manager} 템플릿 오류: {tpl}")
    subject, html = render(tpl, r.values)
    return r, tpl, subject, html


@router.post("/api/mail-sender/preview")
async def preview(body: RowIn, user=Depends(get_current_user)):
    creds = _ready_creds(user)
    rows, _, templates, _, _ = await asyncio.to_thread(_load, creds, body.spreadsheet_id, body.sheet)
    _, tpl, subject, html = _row_message(rows, templates, body.row)
    for cid, (data, mime) in tpl.images.items():
        html = html.replace(f"cid:{cid}", f"data:{mime};base64," + base64.b64encode(data).decode())
    return {"subject": subject, "html": html}


@router.post("/api/mail-sender/test-send")
async def test_send(body: RowIn, user=Depends(get_current_user)):
    creds = _ready_creds(user)
    sender = await asyncio.to_thread(_check_sender, creds, body.sender)
    rows, _, templates, _, _ = await asyncio.to_thread(_load, creds, body.spreadsheet_id, body.sheet)
    _, tpl, subject, html = _row_message(rows, templates, body.row)
    raw = build_mime(sender=sender, to=user.email, subject="[테스트] " + subject, html=html, images=tpl.images)
    return {"message_id": await asyncio.to_thread(mail_google.send_raw, creds, raw)}
```

- [ ] **Step 4: 배선** — 

`app/main.py` 34줄 뒤: `from app.api.mail_sender_api import router as mail_sender_router`
406줄 뒤: `    app.include_router(mail_sender_router)  # /mail-sender, /api/mail-sender/*`

`app/api/middleware.py:61` 집합에 `"/mail-sender",` 를 `"/sales-carryover",` 뒤에 추가.

`app/frontend/chat.html` 87줄(매출 이월 버튼 `</div>`) 뒤:

```html
      <!-- 메일 발송 -->
      <div class="sidebar-dashboard sidebar-bottom-btn">
        <button class="sidebar-dashboard-btn" id="btn-mail-sender">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/></svg>
          <span>메일 발송</span>
        </button>
      </div>
```

`app/frontend/chat.js` 1639줄(매출 이월 핸들러 끝) 뒤:

```javascript
    // 메일 발송 — 전용 페이지를 새 탭에서 연다
    document.getElementById("btn-mail-sender").addEventListener("click", () => {
      window.open("/mail-sender", "_blank", "noopener");
    });
```

- [ ] **Step 5: 화면** — `app/static/mail_sender.html` (coa_finder.html 의 `<head>`·폰트·`style.css` 링크를 그대로 복사하고 본문만 아래로)

```html
<main class="ms-page">
  <header class="ms-head"><h1>메일 발송</h1><span id="ms-me" class="ms-muted"></span></header>
  <section id="ms-connect" class="ms-card" hidden>
    <p id="ms-connect-msg"></p>
    <button id="ms-connect-btn" class="ms-btn">구글 연결 (메일 발송 권한)</button>
  </section>
  <section id="ms-main" class="ms-card" hidden>
    <label>스프레드시트 링크 <input id="ms-url" class="ms-input"></label>
    <button id="ms-load" class="ms-btn">불러오기</button>
    <label>시트 <select id="ms-sheet"></select></label>
    <label>보내는 주소 <select id="ms-sender"></select></label>
    <p class="ms-muted">공용 주소가 없으면 Gmail 설정 → 계정 → '다른 주소에서 메일 보내기' 에 등록하세요.
      한 시트는 한 툴로만 보내세요 (기존 Apps Script 와 동시에 돌리지 마세요).</p>
    <button id="ms-check" class="ms-btn">점검</button>
  </section>
  <section id="ms-result" class="ms-card" hidden>
    <p id="ms-summary"></p>
    <ul id="ms-global-issues"></ul>
    <table class="ms-table"><thead><tr><th>행</th><th>담당자</th><th>이메일</th><th>상태</th><th>점검</th><th></th></tr></thead>
      <tbody id="ms-rows"></tbody></table>
    <button id="ms-send-all" class="ms-btn ms-btn-primary">전체 발송</button>
  </section>
  <section id="ms-job" class="ms-card" hidden>
    <p id="ms-job-text"></p><progress id="ms-job-bar" max="100" value="0"></progress>
    <button id="ms-stop" class="ms-btn">중지</button>
  </section>
  <dialog id="ms-preview"><h3 id="ms-pv-subject"></h3><div id="ms-pv-body"></div>
    <form method="dialog"><button class="ms-btn">닫기</button></form></dialog>
</main>
<script src="/static/mail_sender.js"></script>
```

스타일은 `app/static/style.css` 끝에 `.ms-*` 규칙을 기존 토큰만으로 추가(예: `.ms-card{background:var(--bg-surface);border:1px solid var(--border);border-radius:8px;padding:16px;margin:12px 0}` · `.ms-muted{color:var(--text-muted)}` · `.ms-err{color:var(--danger, #d33)}` 대신 style.css 에 이미 있는 오류 색 토큰 이름을 grep 해서 사용).

`app/static/mail_sender.js`:

```javascript
"use strict";
const $ = (id) => document.getElementById(id);
let state = { spreadsheet_id: "", sheet: "", job: null };

async function api(path, body) {
  const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) } : {});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : (j.detail && j.detail.message) || r.status);
  return j;
}

function text(el, s) { el.textContent = s; }

async function init() {
  const s = await api("/api/mail-sender/status");
  text($("ms-me"), s.me);
  if (!s.connected || s.missing_scopes.length) {
    $("ms-connect").hidden = false;
    text($("ms-connect-msg"), s.connected ? "메일 발송 권한이 빠져 있습니다: " + s.missing_scopes.join(", ") +
      " — 연결 화면에서 모든 항목을 체크하고 허용하세요" : "구글 계정을 연결해야 합니다");
    $("ms-connect-btn").onclick = () => {
      const w = window.open(s.login_url, "gws_auth", "width=500,height=650");
      const t = setInterval(() => { if (w.closed) { clearInterval(t); location.reload(); } }, 1000);
    };
    return;
  }
  $("ms-main").hidden = false;
  for (const a of s.senders) {
    const o = new Option(a.display ? `${a.display} <${a.email}>` : a.email, a.email, a.default, a.default);
    $("ms-sender").add(o);
  }
  const running = await api("/api/mail-sender/jobs/active").catch(() => null);
  if (running && running.job) watchJob(running.job.id);
}

$("ms-load").onclick = async () => {
  const j = await api("/api/mail-sender/sheets", { spreadsheet: $("ms-url").value });
  state.spreadsheet_id = j.spreadsheet_id;
  $("ms-sheet").replaceChildren(...j.sheets.map((t) => new Option(t, t)));
};

$("ms-check").onclick = async () => {
  state.sheet = $("ms-sheet").value;
  text($("ms-summary"), "점검 중… (Docs 탭을 받아오는 데 수십 초 걸릴 수 있습니다)");
  $("ms-result").hidden = false;
  const j = await api("/api/mail-sender/check", { spreadsheet_id: state.spreadsheet_id, sheet: state.sheet });
  text($("ms-summary"), `대상 ${j.pending}명 · 오류 ${j.errors}건`);
  $("ms-global-issues").replaceChildren(...j.issues.filter((i) => i.row === null).map((i) => {
    const li = document.createElement("li"); li.className = i.level === "error" ? "ms-err" : "ms-warn";
    li.textContent = i.message; return li; }));
  const byRow = {};
  for (const i of j.issues) if (i.row !== null) (byRow[i.row] = byRow[i.row] || []).push(i);
  $("ms-rows").replaceChildren(...j.rows.map((r) => {
    const tr = document.createElement("tr");
    const iss = byRow[r.row] || [];
    const mark = r.status === "O" ? "보냄" : iss.some((i) => i.level === "error") ? "❌" : iss.length ? "⚠️" : "✅";
    for (const v of [r.row, r.manager, r.email, r.status || "", mark + " " + iss.map((i) => i.message).join(" / ")]) {
      const td = document.createElement("td"); td.textContent = v; tr.append(td);
    }
    const td = document.createElement("td");
    if (r.status !== "O") {
      const pv = document.createElement("button"); pv.className = "ms-btn"; pv.textContent = "미리보기";
      pv.onclick = () => preview(r.row);
      const ts = document.createElement("button"); ts.className = "ms-btn"; ts.textContent = "나에게 테스트";
      ts.onclick = () => testSend(r.row);
      td.append(pv, ts);
    }
    tr.append(td); return tr;
  }));
  $("ms-send-all").disabled = j.errors > 0 || j.pending === 0;
  $("ms-send-all").dataset.count = j.pending;
};

async function preview(row) {
  const j = await api("/api/mail-sender/preview", { spreadsheet_id: state.spreadsheet_id, sheet: state.sheet, row });
  text($("ms-pv-subject"), j.subject);
  const frame = document.createElement("iframe"); frame.className = "ms-frame"; frame.srcdoc = j.html;
  $("ms-pv-body").replaceChildren(frame);
  $("ms-preview").showModal();
}

async function testSend(row) {
  await api("/api/mail-sender/test-send", { spreadsheet_id: state.spreadsheet_id, sheet: state.sheet, row,
    sender: $("ms-sender").value });
  alert("본인 메일함으로 테스트 메일을 보냈습니다");
}

$("ms-send-all").onclick = async () => {
  const n = $("ms-send-all").dataset.count;
  if (!confirm(`${state.sheet} 시트의 ${n}명에게 '${$("ms-sender").value}' 로 발송합니다. 진행할까요?`)) return;
  const j = await api("/api/mail-sender/jobs", { spreadsheet_id: state.spreadsheet_id, sheet: state.sheet,
    sender: $("ms-sender").value, confirm_count: Number(n) });
  watchJob(j.job_id);
};

function watchJob(id) {
  $("ms-job").hidden = false;
  $("ms-stop").onclick = () => api(`/api/mail-sender/jobs/${id}/stop`, {});
  const tick = async () => {
    const j = await api(`/api/mail-sender/jobs/${id}`);
    const done = j.sent + j.failed + j.skipped;
    $("ms-job-bar").value = j.total ? Math.round((done / j.total) * 100) : 0;
    text($("ms-job-text"), `${j.status} · 성공 ${j.sent} · 실패 ${j.failed} · 건너뜀 ${j.skipped} / ${j.total}` +
      (j.stop_reason ? ` — ${j.stop_reason}` : ""));
    if (["running", "stopping"].includes(j.status)) setTimeout(tick, 2000);
  };
  tick();
}

init().catch((e) => { $("ms-connect").hidden = false; text($("ms-connect-msg"), "오류: " + e.message); });
```

(`/api/mail-sender/jobs*` 는 Task 7 에서 만든다. 이 태스크에서는 버튼이 404 를 받아도 된다 — Task 7 끝에서 함께 확인)

- [ ] **Step 6: 통과 확인**

Run: `python -m pytest tests/test_mail_sender_api.py -q` → PASS
Run: `node --check app/static/mail_sender.js` → 출력 없음

- [ ] **Step 7: 커밋**

```bash
git add app/api/mail_sender_api.py app/static/mail_sender.html app/static/mail_sender.js app/static/style.css app/main.py app/api/middleware.py app/frontend/chat.html app/frontend/chat.js tests/test_mail_sender_api.py
git commit -m "feat(mail-sender): /mail-sender 페이지·점검·미리보기·본인 테스트 발송"
```

---

### Task 7: 발송 작업 — MariaDB 기록 + 백그라운드 스레드

**Files:**
- Create: `app/core/mail_sender_jobs.py`
- Modify: `app/api/mail_sender_api.py` (작업 엔드포인트 4개), `app/main.py` lifespan(`:161` 근처, self_check DDL 옆)
- Test: `tests/test_mail_sender_jobs.py`

**Interfaces:**
- Consumes: `_load`, `_check_sender` (Task 6), `build_mime`, `render`, `mail_google.*`
- Produces:
  - `ensure_mail_sender_tables() -> None` — DDL + `running/stopping` 작업을 `interrupted` 로, 그 작업의 `sending` 항목을 `unknown` 으로
  - `sent_today(email: str) -> int`
  - `create_job(owner: str, spreadsheet_id: str, sheet: str, sender: str, rows: list[Row]) -> int` — 같은 시트 진행 중이면 `RuntimeError("이 시트는 이미 발송 중입니다")`
  - `run_job(job_id: int, creds, cols: Columns, templates: dict, deps: JobDeps | None = None) -> None` (스레드 대상)
  - `start_job_thread(job_id, creds, cols, templates) -> None` — `threading.Thread(daemon=True)`
  - `get_job(job_id: int, owner: str) -> dict | None` · `active_job(owner: str) -> dict | None` · `request_stop(job_id: int, owner: str) -> bool`
  - `@dataclass JobDeps: read_cell, write_cells, send_raw, sleep` (테스트 주입용, 기본값 = mail_google 함수 · `time.sleep`)
  - 상수 `MAX_SAME_ERROR = 3`

- [ ] **Step 1: 실패하는 테스트** (DB 는 메모리 가짜로 바꾼다)

```python
# tests/test_mail_sender_jobs.py
from unittest.mock import MagicMock

import pytest

from app.core import mail_sender_jobs as J
from app.core.mail_sender import Columns, Row
from app.core.mail_template import MailTemplate

COLS = Columns(email=3, manager=1, status=6, note=7, date=0, variables={"kol id": 2})
TPL = MailTemplate(subject="[S] {kol id}", html="<p>{kol id}</p>", images={})


@pytest.fixture
def db(monkeypatch):
    d = J.MemoryStore()
    monkeypatch.setattr(J, "_store", d)
    return d


def _rows(n):
    return [Row(row=i + 2, manager="강하니", email=f"u{i}@x.com", status="", values={"kol id": f"k{i}"}) for i in range(n)]


def _deps(sent, g_values=None, send_side=None):
    g_values = g_values or {}
    return J.JobDeps(
        read_cell=lambda c, sid, sh, row, col: g_values.get(row, ""),
        write_cells=MagicMock(),
        send_raw=send_side or (lambda c, raw: sent.append(raw) or f"id{len(sent)}"),
        sleep=lambda s: None)


def test_sends_all_and_marks_O(db):
    sent = []
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(3))
    deps = _deps(sent)
    J.run_job(jid, object(), COLS, {"강하니": TPL}, deps)
    job = J.get_job(jid, "me@x.com")
    assert (job["status"], job["sent"], job["failed"]) == ("done", 3, 0)
    assert len(sent) == 3
    assert deps.write_cells.call_args_list[0].args[3] == 2  # 행 번호
    assert deps.write_cells.call_args_list[0].args[4][COLS.status] == "O"


def test_row_marked_O_midway_is_skipped(db):
    sent = []
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(3))
    J.run_job(jid, object(), COLS, {"강하니": TPL}, _deps(sent, g_values={3: "O"}))
    job = J.get_job(jid, "me@x.com")
    assert (job["sent"], job["skipped"]) == (2, 1)


def test_same_error_three_times_stops_job(db):
    def boom(c, raw):
        raise RuntimeError("insufficientPermissions")
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(10))
    J.run_job(jid, object(), COLS, {"강하니": TPL}, _deps([], send_side=boom))
    job = J.get_job(jid, "me@x.com")
    assert job["status"] == "stopped" and job["failed"] == 3 and "insufficientPermissions" in job["stop_reason"]


def test_same_sheet_concurrent_job_rejected(db):
    J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(1))
    with pytest.raises(RuntimeError, match="이미 발송 중"):
        J.create_job("you@x.com", "sid", "gmail_a", "you@x.com", _rows(1))


def test_restart_marks_interrupted_not_resumed(db):
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(2))
    db.set_item_status(jid, 2, "sending")
    J.recover_after_restart()
    job = J.get_job(jid, "me@x.com")
    assert job["status"] == "interrupted"
    assert db.item_status(jid, 2) == "unknown"


def test_stop_request(db):
    sent = []
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(5))
    deps = _deps(sent)
    deps.sleep = lambda s: J.request_stop(jid, "me@x.com")
    J.run_job(jid, object(), COLS, {"강하니": TPL}, deps)
    assert J.get_job(jid, "me@x.com")["status"] == "stopped" and len(sent) == 1
```

(저장소는 `MemoryStore`(테스트)/`MariaStore`(운영) 두 구현이 같은 메서드를 가진다. 테스트는 `_store` 를 `MemoryStore` 로 바꿔 DB 없이 돈다)

- [ ] **Step 2: 실패 확인** — Run: `python -m pytest tests/test_mail_sender_jobs.py -q` → FAIL

- [ ] **Step 3: 구현**

```python
# app/core/mail_sender_jobs.py
"""메일 발송 작업 — 기록은 MariaDB, 실행은 데몬 스레드.

⛔ 재시작 후 자동 재개하지 않는다: 'sending' 이던 행이 실제로 나갔는지 서버는 모른다.
⛔ 같은 원인 실패가 MAX_SAME_ERROR 번 연속이면 멈춘다 — 권한 문제로 전 행 X 가 되는 것을 막는다.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from app.core import mail_google
from app.core.mail_mime import build_mime
from app.core.mail_sender import Columns, Row
from app.core.mail_template import render

MAX_SAME_ERROR = 3
SEND_DELAY = 1.0
_ACTIVE = ("running", "stopping")

_DDL = [
    """CREATE TABLE IF NOT EXISTS mail_send_jobs (
        id INT AUTO_INCREMENT PRIMARY KEY, owner VARCHAR(255) NOT NULL, spreadsheet_id VARCHAR(128) NOT NULL,
        sheet VARCHAR(255) NOT NULL, sender VARCHAR(255) NOT NULL, status VARCHAR(16) NOT NULL,
        total INT NOT NULL DEFAULT 0, sent INT NOT NULL DEFAULT 0, failed INT NOT NULL DEFAULT 0,
        skipped INT NOT NULL DEFAULT 0, stop_reason TEXT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, finished_at DATETIME NULL,
        INDEX idx_owner (owner, created_at), INDEX idx_sheet (spreadsheet_id, sheet, status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS mail_send_items (
        id INT AUTO_INCREMENT PRIMARY KEY, job_id INT NOT NULL, sheet_row INT NOT NULL,
        email VARCHAR(320) NOT NULL, manager VARCHAR(255) NOT NULL, row_values JSON NOT NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'pending', error TEXT NULL, message_id VARCHAR(64) NULL,
        sent_at DATETIME NULL, UNIQUE KEY uq_job_row (job_id, sheet_row), INDEX idx_sent (status, sent_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
]


class MemoryStore:
    """테스트용 저장소 — MariaStore 와 같은 메서드."""
    def __init__(self):
        self.jobs: dict[int, dict] = {}
        self.items: dict[int, list[dict]] = {}
        self._lock = threading.Lock()

    def active_for_sheet(self, sid, sheet):
        return any(j["spreadsheet_id"] == sid and j["sheet"] == sheet and j["status"] in _ACTIVE
                   for j in self.jobs.values())

    def insert_job(self, owner, sid, sheet, sender, rows):
        with self._lock:
            jid = len(self.jobs) + 1
            self.jobs[jid] = dict(id=jid, owner=owner, spreadsheet_id=sid, sheet=sheet, sender=sender,
                                  status="running", total=len(rows), sent=0, failed=0, skipped=0, stop_reason=None)
            self.items[jid] = [dict(sheet_row=r.row, email=r.email, manager=r.manager, row_values=dict(r.values),
                                    status="pending", error=None, message_id=None, sent_at=None) for r in rows]
            return jid

    def job(self, jid):
        j = self.jobs.get(jid)
        return dict(j) if j else None

    def pending_items(self, jid):
        return [dict(i) for i in self.items[jid] if i["status"] == "pending"]

    def set_item_status(self, jid, row, status, error=None, message_id=None):
        for i in self.items[jid]:
            if i["sheet_row"] == row:
                i.update(status=status, error=error, message_id=message_id,
                         sent_at=datetime.now() if status == "sent" else i["sent_at"])

    def item_status(self, jid, row):
        return next(i["status"] for i in self.items[jid] if i["sheet_row"] == row)

    def bump(self, jid, col):
        self.jobs[jid][col] += 1

    def set_job(self, jid, **kw):
        self.jobs[jid].update(kw)

    def interrupt_all(self):
        for jid, j in self.jobs.items():
            if j["status"] in _ACTIVE:
                j["status"] = "interrupted"
                for i in self.items[jid]:
                    if i["status"] == "sending":
                        i["status"] = "unknown"

    def sent_today(self, owner):
        today = datetime.now().date()
        return sum(1 for jid, j in self.jobs.items() if j["owner"] == owner
                   for i in self.items[jid] if i["status"] == "sent" and i["sent_at"] and i["sent_at"].date() == today)

    def active_job(self, owner):
        for j in self.jobs.values():
            if j["owner"] == owner and j["status"] in _ACTIVE:
                return dict(j)
        return None


class MariaStore:
    def __init__(self):
        from app.db import mariadb  # 지연 import — 테스트가 DB 없이 돈다
        self.db = mariadb

    def active_for_sheet(self, sid, sheet):
        return bool(self.db.fetch_one(
            "SELECT id FROM mail_send_jobs WHERE spreadsheet_id=%s AND sheet=%s AND status IN ('running','stopping')",
            (sid, sheet)))

    def insert_job(self, owner, sid, sheet, sender, rows):
        import json
        jid = self.db.execute_lastid(
            "INSERT INTO mail_send_jobs (owner, spreadsheet_id, sheet, sender, status, total) "
            "VALUES (%s,%s,%s,%s,'running',%s)", (owner, sid, sheet, sender, len(rows)))
        for r in rows:
            self.db.execute(
                "INSERT INTO mail_send_items (job_id, sheet_row, email, manager, row_values) VALUES (%s,%s,%s,%s,%s)",
                (jid, r.row, r.email, r.manager, json.dumps(r.values, ensure_ascii=False)))
        return jid

    def job(self, jid):
        return self.db.fetch_one("SELECT * FROM mail_send_jobs WHERE id=%s", (jid,))

    def pending_items(self, jid):
        import json
        rows = self.db.fetch_all(
            "SELECT * FROM mail_send_items WHERE job_id=%s AND status='pending' ORDER BY sheet_row", (jid,))
        for r in rows:
            r["row_values"] = json.loads(r["row_values"]) if isinstance(r["row_values"], str) else r["row_values"]
        return rows

    def set_item_status(self, jid, row, status, error=None, message_id=None):
        self.db.execute(
            "UPDATE mail_send_items SET status=%s, error=%s, message_id=%s, "
            "sent_at=IF(%s='sent', NOW(), sent_at) WHERE job_id=%s AND sheet_row=%s",
            (status, error, message_id, status, jid, row))

    def item_status(self, jid, row):
        r = self.db.fetch_one("SELECT status FROM mail_send_items WHERE job_id=%s AND sheet_row=%s", (jid, row))
        return r["status"] if r else None

    def bump(self, jid, col):
        assert col in ("sent", "failed", "skipped")
        self.db.execute(f"UPDATE mail_send_jobs SET {col}={col}+1 WHERE id=%s", (jid,))

    def set_job(self, jid, **kw):
        cols = ", ".join(f"{k}=%s" for k in kw)
        self.db.execute(f"UPDATE mail_send_jobs SET {cols} WHERE id=%s", (*kw.values(), jid))

    def interrupt_all(self):
        self.db.execute(
            "UPDATE mail_send_items i JOIN mail_send_jobs j ON i.job_id=j.id SET i.status='unknown' "
            "WHERE j.status IN ('running','stopping') AND i.status='sending'")
        self.db.execute("UPDATE mail_send_jobs SET status='interrupted', finished_at=NOW() "
                        "WHERE status IN ('running','stopping')")

    def sent_today(self, owner):
        r = self.db.fetch_one(
            "SELECT COUNT(*) AS n FROM mail_send_items i JOIN mail_send_jobs j ON i.job_id=j.id "
            "WHERE j.owner=%s AND i.status='sent' AND i.sent_at >= CURDATE()", (owner,))
        return int(r["n"]) if r else 0

    def active_job(self, owner):
        return self.db.fetch_one(
            "SELECT * FROM mail_send_jobs WHERE owner=%s AND status IN ('running','stopping') "
            "ORDER BY id DESC LIMIT 1", (owner,))


_store = None


def _s():
    global _store
    if _store is None:
        _store = MariaStore()
    return _store


def ensure_mail_sender_tables() -> None:
    from app.db.mariadb import execute
    for ddl in _DDL:
        execute(ddl)
    recover_after_restart()


def recover_after_restart() -> None:
    _s().interrupt_all()


def sent_today(email: str) -> int:
    return _s().sent_today(email)


def create_job(owner: str, spreadsheet_id: str, sheet: str, sender: str, rows: list[Row]) -> int:
    if _s().active_for_sheet(spreadsheet_id, sheet):
        raise RuntimeError("이 시트는 이미 발송 중입니다")
    return _s().insert_job(owner, spreadsheet_id, sheet, sender, rows)


def get_job(job_id: int, owner: str) -> dict | None:
    j = _s().job(job_id)
    return j if j and j["owner"] == owner else None


def active_job(owner: str) -> dict | None:
    return _s().active_job(owner)


def request_stop(job_id: int, owner: str) -> bool:
    j = get_job(job_id, owner)
    if not j or j["status"] != "running":
        return False
    _s().set_job(job_id, status="stopping")
    return True


@dataclass
class JobDeps:
    read_cell: Callable = mail_google.read_cell
    write_cells: Callable = mail_google.write_cells
    send_raw: Callable = mail_google.send_raw
    sleep: Callable = time.sleep


def _error_kind(e: Exception) -> str:
    return type(e).__name__ + ":" + str(e)[:80]


def run_job(job_id: int, creds, cols: Columns, templates: dict, deps: JobDeps | None = None) -> None:
    deps = deps or JobDeps()
    st = _s()
    job = st.job(job_id)
    last_kind, streak = None, 0
    for item in st.pending_items(job_id):
        if st.job(job_id)["status"] == "stopping":
            st.set_job(job_id, status="stopped", stop_reason="사용자가 중지함", finished_at=datetime.now())
            return
        row = item["sheet_row"]
        try:
            if deps.read_cell(creds, job["spreadsheet_id"], job["sheet"], row, cols.status) == "O":
                st.set_item_status(job_id, row, "skipped", error="다른 툴이 이미 보냄(O)")
                st.bump(job_id, "skipped")
                continue
            tpl = templates[item["manager"]]
            subject, html = render(tpl, item["row_values"])
            raw = build_mime(sender=job["sender"], to=item["email"], subject=subject, html=html, images=tpl.images)
            st.set_item_status(job_id, row, "sending")
            mid = deps.send_raw(creds, raw)
            st.set_item_status(job_id, row, "sent", message_id=mid)
            st.bump(job_id, "sent")
            last_kind, streak = None, 0
            updates = {cols.status: "O"}
            if cols.date is not None:
                updates[cols.date] = datetime.now().strftime("%Y-%m-%d")
            try:
                deps.write_cells(creds, job["spreadsheet_id"], job["sheet"], row, updates)
            except Exception as we:  # 발송은 됐다 — DB 에는 sent, 시트 기록 실패만 남긴다
                st.set_item_status(job_id, row, "sent", error=f"시트 기록 실패: {we}", message_id=mid)
        except Exception as e:
            st.set_item_status(job_id, row, "failed", error=str(e)[:500])
            st.bump(job_id, "failed")
            try:
                deps.write_cells(creds, job["spreadsheet_id"], job["sheet"], row,
                                 {cols.status: "X", cols.note: str(e)[:200]})
            except Exception:
                pass
            kind = _error_kind(e)
            streak = streak + 1 if kind == last_kind else 1
            last_kind = kind
            if streak >= MAX_SAME_ERROR:
                st.set_job(job_id, status="stopped", finished_at=datetime.now(),
                           stop_reason=f"같은 오류 {streak}회 연속으로 멈춤: {e}")
                return
        deps.sleep(SEND_DELAY)
    st.set_job(job_id, status="done", finished_at=datetime.now())


def start_job_thread(job_id: int, creds, cols: Columns, templates: dict) -> None:
    threading.Thread(target=run_job, args=(job_id, creds, cols, templates), daemon=True,
                     name=f"mail-send-{job_id}").start()
```

⚠️ 테스트의 `monkeypatch.setattr(J, "_store", d)` 가 `_s()` 에 반영되도록 `_s()` 는 모듈 전역 `_store` 를 매번 읽는다(위 코드 그대로).

- [ ] **Step 4: 작업 엔드포인트** — `app/api/mail_sender_api.py` 에 추가

```python
from app.core import mail_sender_jobs as jobs


class JobIn(SheetIn):
    sender: str
    confirm_count: int


@router.post("/api/mail-sender/jobs")
async def start_job(body: JobIn, user=Depends(get_current_user)):
    creds = _ready_creds(user)
    sender = await asyncio.to_thread(_check_sender, creds, body.sender)
    rows, cols, templates, tab_dir, updated = await asyncio.to_thread(_load, creds, body.spreadsheet_id, body.sheet)
    targets = pending(rows)
    issues = check(targets, tab_dir, updated, templates, datetime.now(timezone.utc),
                   sent_today=await asyncio.to_thread(jobs.sent_today, user.email))
    errors = [i for i in issues if i.level == "error"]
    if errors:
        raise HTTPException(400, f"점검 오류 {len(errors)}건이 있어 시작하지 않습니다: {errors[0].message}")
    if len(targets) != body.confirm_count:  # 확인 창 이후 시트가 바뀌었다
        raise HTTPException(409, f"대상이 바뀌었습니다 ({body.confirm_count} → {len(targets)}명). 다시 점검하세요")
    try:
        job_id = await asyncio.to_thread(jobs.create_job, user.email, body.spreadsheet_id, body.sheet, sender, targets)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    jobs.start_job_thread(job_id, creds, cols, templates)
    return {"job_id": job_id}


@router.get("/api/mail-sender/jobs/active")
async def active(user=Depends(get_current_user)):
    return {"job": await asyncio.to_thread(jobs.active_job, user.email)}


@router.get("/api/mail-sender/jobs/{job_id}")
async def job_status(job_id: int, user=Depends(get_current_user)):
    j = await asyncio.to_thread(jobs.get_job, job_id, user.email)
    if not j:
        raise HTTPException(404, "작업이 없습니다")
    return {k: (str(v) if isinstance(v, datetime) else v) for k, v in j.items()}


@router.post("/api/mail-sender/jobs/{job_id}/stop")
async def stop(job_id: int, user=Depends(get_current_user)):
    return {"ok": await asyncio.to_thread(jobs.request_stop, job_id, user.email)}
```

⚠️ `/jobs/active` 를 `/jobs/{job_id}` **보다 먼저** 선언한다 (FastAPI 경로 매칭 순서).
그리고 Task 6 의 `_sent_today` 본문을 실제 기록으로 바꾼다:

```python
async def _sent_today(email: str) -> int:
    return await asyncio.to_thread(jobs.sent_today, email)
```

- [ ] **Step 5: lifespan** — `app/main.py:162` (`ensure_self_check_tables`) 뒤에:

```python
        from app.core.mail_sender_jobs import ensure_mail_sender_tables
        try:
            await asyncio.to_thread(ensure_mail_sender_tables)
        except Exception as e:  # DB 가 없어도 앱은 뜬다 — 메일 작업 API 만 실패한다
            logger.warning("mail_sender_tables_failed", error=str(e))
```

(로거 이름은 main.py 가 쓰는 것에 맞춘다)

- [ ] **Step 6: API 테스트 추가** — `tests/test_mail_sender_api.py` 에

```python
def test_start_job_rejects_changed_count(client):
    from app.core.mail_sender import Columns, Row
    from app.core.mail_template import MailTemplate
    tpl = MailTemplate(subject="s", html="<p>x</p>", images={})
    rows = [Row(2, "강하니", "a@x.com", "", {}), Row(3, "강하니", "b@x.com", "", {})]
    cols = Columns(3, 1, 6, 7, 0, {})
    with patch("app.api.mail_sender_api._credentials", return_value=object()), \
         patch("app.api.mail_sender_api.mail_google.missing_mail_scopes", return_value=[]), \
         patch("app.api.mail_sender_api.mail_google.send_as_addresses",
               return_value=[{"email": "me@x.com", "display": "", "default": True}]), \
         patch("app.api.mail_sender_api._load", return_value=(rows, cols, {"강하니": tpl}, {"강하니": "t.a"}, None)), \
         patch("app.api.mail_sender_api.jobs.sent_today", return_value=0), \
         patch("app.api.mail_sender_api.jobs.start_job_thread") as th:
        r = client.post("/api/mail-sender/jobs", json={"spreadsheet_id": "x", "sheet": "gmail_a",
                                                       "sender": "me@x.com", "confirm_count": 1})
    assert r.status_code == 409 and "바뀌었" in r.json()["detail"]
    th.assert_not_called()
```

- [ ] **Step 7: 통과 확인**

Run: `python -m pytest tests/test_mail_sender_jobs.py tests/test_mail_sender_api.py tests/test_mail_sender.py tests/test_mail_template.py tests/test_mail_mime.py tests/test_mail_google_auth.py -q`
Expected: 전부 PASS

Run: `python -m pytest tests/test_coa_finder_api.py tests/test_sales_carryover.py tests/test_no_silent_failures.py -q`
Expected: PASS (기존 화면 회귀)

- [ ] **Step 8: 커밋**

```bash
git add app/core/mail_sender_jobs.py app/api/mail_sender_api.py app/main.py tests/test_mail_sender_jobs.py tests/test_mail_sender_api.py
git commit -m "feat(mail-sender): 백그라운드 발송 작업 (재시작 자동재개 금지·연속오류 중지·병행 O 건너뜀)"
```

---

### Task 8: 자가 점검 + 문서

**Files:**
- Modify: `app/core/self_check.py` (검사 1종), `CLAUDE.md` (새 절), `docs/superpowers/specs/2026-09-30-mail-sender-design.md` (상태 줄)
- Test: 기존 `tests/test_self_check*.py` 패턴에 맞춰 1개

**Interfaces:**
- Consumes: `mail_google.export_tab_zip`, `mail_sender.DOC_ID`, `TAB_DIR_SHEET`

- [ ] **Step 1: 자가 점검 검사** — `self_check.py` 의 기존 구글 계정 검사(`test_self_check_google_accounts.py` 가 보는 함수)를 읽고 같은 모양으로 `check_mail_sender_tab_directory()` 를 추가: 서비스 계정이 아니라 **관리자 토큰**이 있을 때만 `_탭목록` 의 갱신 시각을 읽어 1시간 넘게 낡았으면 `warn("메일 발송 탭 목록 갱신 멈춤 — gmailv2 트리거 확인")`. 토큰이 없으면 `skip` (조용한 통과 아님 — skip 사유를 적는다)

- [ ] **Step 2: 테스트** — 낡은 시각이면 warn, 최근이면 ok, 토큰 없으면 skip — 3문항. 기존 self_check 테스트 파일의 픽스처를 그대로 쓴다

- [ ] **Step 3: CLAUDE.md** — "매출 이월 확인" 절 뒤에 `## 메일 발송 (/mail-sender)` 절: 화면/판정/API 파일, B안(탭 목록 시트 + 탭별 내보내기)과 그 이유(WAS 프록시 실측표 요약), Global Constraints 의 ⛔ 6줄, 회귀 파일 목록

- [ ] **Step 4: 통과 확인 + 커밋**

Run: `python -m pytest tests -q -k "mail_sender or self_check"` → PASS

```bash
git add app/core/self_check.py tests CLAUDE.md docs/superpowers/specs/2026-09-30-mail-sender-design.md
git commit -m "docs(mail-sender): 자가 점검 + CLAUDE.md 절"
```

---

### Task 9: 실물 확인 — 개발 서버(3001) → 비교 발송 → 운영 배포

자동 테스트가 못 보는 것(실제 구글 응답·서식 모양)을 사람이 본다. **모든 발송은 본인 주소로만.**

- [ ] **Step 1: 개발 서버 기동** — `pm2 restart skin1004-dev` → `http://172.16.1.250:3001/mail-sender` 접속

- [ ] **Step 2: 권한 흐름** — "구글 연결 (메일 발송 권한)" → 동의 화면에서 **일부 항목 체크 해제**하고 허용 → 화면이 "빠진 권한: …" 을 보여주는지(발송 버튼 없음) → 다시 연결해 전부 허용 → 보내는 주소 목록이 뜨는지 (공용 별칭 있으면 함께)

- [ ] **Step 3: 점검** — `gmail_v2` 링크 → 테스트용 시트(본인 주소로만 채운 `gmail_test`) → 점검 → ✅/⚠️/❌ 와 탭 목록 갱신 시각 확인

- [ ] **Step 4: 비교 발송 (서식 위험 확인)** — 대표 탭 10개(이미지 있는 탭·표 있는 탭·불릿 있는 탭·Shift+Enter 서명 있는 탭 포함)마다:
  - 기존 툴 `테스트 발송` (본인 주소 행) 1통
  - 새 화면 `나에게 테스트` 1통
  - 둘을 Gmail 에서 나란히 열어 글자·굵게·링크·이미지 크기·줄 간격 비교. 차이를 설계 문서 "내보낸 HTML → 메일 HTML" 절에 표로 기록. **줄 간격이 크게 다르면** `mail_template` 에 `p{margin:0}` 류 보정을 넣고 Task 2 테스트에 그 경우를 추가

- [ ] **Step 5: 전체 발송 리허설** — 본인·동료 테스트 주소 5행으로 전체 발송 → 진행률·시트 O·날짜 기록 확인 → 작업 중 `중지` → `stopped` 확인 → 같은 시트 재발송 시 O 행 건너뜀 확인 → 개발 서버 재시작 중 발송 → `interrupted` + `unknown` 행 표시 확인

- [ ] **Step 6: 운영 배포** — `./sshenv/Scripts/python scripts/deploy_new_server.py was` → `systemctl is-active ai-craver` 와 `/health` 직접 확인 → `http://10.1.100.5/mail-sender` 에서 Step 2·3 반복(발송은 본인 1통)

- [ ] **Step 7: 공지·가이드** — 마케팅팀 공지(재연결 시 "모든 항목 체크", 한 시트는 한 툴로), 노션 가이드 1-3 에 새 화면 사용법 추가. 설계 문서 상태 줄을 "운영 배포 완료(날짜)" 로 바꾸고 커밋
