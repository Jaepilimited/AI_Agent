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
