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
import urllib.parse
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
    # Strip @import statements (both url() and quoted forms)
    css = re.sub(r"@import[^;]*;", "", css)
    # Strip /* */ comments
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)

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
        # Skip variable substitution if src contains a template variable
        if "{" in src:
            continue
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
    variables = {normalize_key(m) for m in _VAR.findall(_text_of(template))}
    # Also find variables in attribute values
    soup = BeautifulSoup(template.html, "lxml")
    for tag in soup.find_all():
        for attr_value in tag.attrs.values():
            if isinstance(attr_value, str):
                variables.update(normalize_key(m) for m in _VAR.findall(attr_value))
    return variables


def _safe_url(url: str) -> bool:
    """Check if URL scheme is safe (http, https, mailto, tel, or relative)."""
    # Browsers strip ASCII tab/newline/C0 controls and spaces before parsing the scheme
    url = re.sub(r"[\x00-\x20]", "", url).lower()
    # Relative URLs: start with /, #, or don't have a scheme
    if url.startswith(("/", "#")):
        return True
    # Check for scheme
    scheme_match = re.match(r"^([a-z][a-z0-9+.\-]*):", url)
    if not scheme_match:
        return True  # No scheme = relative URL
    scheme = scheme_match.group(1)
    return scheme in ("http", "https", "mailto", "tel")


def _mailto_addr(v: str) -> str:
    """Quote a mailto address value if it could inject headers."""
    if re.search(r"[?&\r\n]", v):
        return urllib.parse.quote(v, safe="@")
    return v


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

    # Replace variables in text nodes
    for node in list(soup.find_all(string=True)):
        if isinstance(node, NavigableString) and "{" in node:
            node.replace_with(BeautifulSoup(_sub(_html.escape(str(node)), vals, escape=True), "html.parser"))

    # Replace variables in attribute values
    for tag in soup.find_all():
        for attr_name, attr_value in list(tag.attrs.items()):
            if isinstance(attr_value, str) and "{" in attr_value:
                # URL attributes: smart quoting with security
                if attr_name in ("href", "src"):
                    stripped = attr_value.strip()
                    # Check if entire value is a single variable
                    full_var_match = re.fullmatch(r"\{\s*([^{}<>]+?)\s*\}", stripped)
                    # Check if value starts with mailto:
                    is_mailto = stripped.lower().startswith("mailto:")

                    def repl_url(m: re.Match) -> str:
                        key = normalize_key(m.group(1))
                        if key not in vals:
                            return m.group(0)
                        v = vals[key] or ""

                        # Handle full variable substitution (entire attribute is {var})
                        if full_var_match:
                            # Security check: only allow safe schemes
                            if not _safe_url(v):
                                return urllib.parse.quote(v, safe='')
                            return v

                        # Handle mailto: with query string
                        if is_mailto:
                            # Check if there's a query string (after ?)
                            if "?" in attr_value:
                                # Only quote the variable if it's in the query part
                                # Simple heuristic: if variable position is after ?, quote it
                                before_var = attr_value[:m.start()]
                                if "?" in before_var:
                                    # Variable is in query part, quote it
                                    return urllib.parse.quote(v, safe='')
                                else:
                                    # Variable is in address part (before ?), use raw
                                    return _mailto_addr(v)
                            else:
                                # No query string, use raw value
                                return _mailto_addr(v)

                        # For embedded variables in URLs, always quote
                        return urllib.parse.quote(v, safe='')
                    tag[attr_name] = _VAR.sub(repl_url, attr_value)
                else:
                    # Other attributes: plain value
                    def repl_plain(m: re.Match) -> str:
                        key = normalize_key(m.group(1))
                        if key not in vals:
                            return m.group(0)
                        return vals[key] or ""
                    tag[attr_name] = _VAR.sub(repl_plain, attr_value)

    body = soup.body or soup
    return _sub(template.subject, vals, escape=False), "".join(str(c) for c in body.contents)


def unreplaced(text: str) -> list[str]:
    """Find unreplaced variables in plain text (no HTML parsing)."""
    return [m.group(0) for m in re.finditer(r"\{[^{}]*\}", text)]


def unreplaced_html(html: str) -> list[str]:
    """Find unreplaced variables in HTML (text nodes and attribute values)."""
    soup = BeautifulSoup(html, "lxml")
    found = []

    # Find in concatenated text (handles split variables across nodes)
    plain_text = soup.get_text()
    found.extend(m.group(0) for m in re.finditer(r"\{[^{}]*\}", plain_text))

    # Find in attribute values
    for tag in soup.find_all():
        for attr_value in tag.attrs.values():
            if isinstance(attr_value, str):
                found.extend(m.group(0) for m in re.finditer(r"\{[^{}]*\}", attr_value))

    # Remove duplicates while preserving order
    seen = set()
    result = []
    for item in found:
        if item not in seen:
            result.append(item)
            seen.add(item)
    return result
