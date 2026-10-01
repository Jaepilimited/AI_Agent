import io
import zipfile

import pytest

from app.core.mail_template import (
    TemplateError, find_variables, render, template_from_export_zip, unreplaced, unreplaced_html,
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
    assert unreplaced_html(out) == ["{KOL ID}"]


def test_css_with_import_and_comments():
    html_with_import = EXPORT.replace(
        '<style type="text/css">.c1{font-weight:700}',
        '<style type="text/css">@import url(https://themes.googleusercontent.com/x);.c1{font-weight:700}'
    )
    t = template_from_export_zip(_zip(html_with_import, {"images/image1.png": PNG}))
    assert "font-weight:700" in t.html


def test_css_with_quoted_import():
    html_with_quoted_import = EXPORT.replace(
        '<style type="text/css">.c1{font-weight:700}',
        '<style type="text/css">@import "https://themes.googleusercontent.com/a.css";.c1{font-weight:700}'
    )
    t = template_from_export_zip(_zip(html_with_quoted_import, {"images/image1.png": PNG}))
    assert "font-weight:700" in t.html


def test_variables_in_href_attribute():
    html = EXPORT.replace(
        '<a class="c2" href="https://form.example">FORM</a>',
        '<a class="c2" href="https://www.example.com?id={user_id}">FORM</a>'
    )
    t = template_from_export_zip(_zip(html, {"images/image1.png": PNG}))
    assert "user_id" in find_variables(t)
    subject, html_out = render(t, {"name": "test", "user_id": "abc"})
    assert "id=abc" in html_out
    assert "{user_id}" not in html_out


def test_unreplaced_plain_text_with_angle_brackets():
    text = "a<b {x}"
    assert unreplaced(text) == ["{x}"]


def test_unreplaced_html_finds_attribute_variables():
    html = '<a href="https://example.com?id={missing_id}">Link</a>'
    assert unreplaced_html(html) == ["{missing_id}"]


def test_href_full_variable_not_quoted():
    html = EXPORT.replace(
        '<a class="c2" href="https://form.example">FORM</a>',
        '<a class="c2" href="{link}">FORM</a>'
    )
    t = template_from_export_zip(_zip(html, {"images/image1.png": PNG}))
    _, html_out = render(t, {"name": "test", "link": "https://a.com/x?y=1"})
    assert 'href="https://a.com/x?y=1"' in html_out
    assert "%3A" not in html_out  # Should not be URL-encoded


def test_href_mailto_variable_not_quoted():
    html = EXPORT.replace(
        '<a class="c2" href="https://form.example">FORM</a>',
        '<a class="c2" href="mailto:{email}">FORM</a>'
    )
    t = template_from_export_zip(_zip(html, {"images/image1.png": PNG}))
    _, html_out = render(t, {"name": "test", "email": "a@b.com"})
    assert 'href="mailto:a@b.com"' in html_out
    assert "%40" not in html_out  # Should not be URL-encoded


def test_href_embedded_variable_is_quoted():
    html = EXPORT.replace(
        '<a class="c2" href="https://form.example">FORM</a>',
        '<a class="c2" href="https://www.example.com/search?q={query}">FORM</a>'
    )
    t = template_from_export_zip(_zip(html, {"images/image1.png": PNG}))
    _, html_out = render(t, {"name": "test", "query": "hello world"})
    assert "hello%20world" in html_out  # Should be URL-encoded


def test_javascript_href_blocked():
    html = EXPORT.replace(
        '<a class="c2" href="https://form.example">FORM</a>',
        '<a class="c2" href="{link}">FORM</a>'
    )
    t = template_from_export_zip(_zip(html, {"images/image1.png": PNG}))
    _, html_out = render(t, {"name": "test", "link": "javascript:alert(1)"})
    assert "javascript:" not in html_out  # Should be neutralized


def test_data_url_blocked():
    html = EXPORT.replace(
        '<img src="images/image1.png" style="width: 200px; height: 100px;">',
        '<img src="{image_src}" style="width: 200px; height: 100px;">'
    )
    t = template_from_export_zip(_zip(html, {"images/image1.png": PNG}))
    _, html_out = render(t, {"name": "test", "image_src": "data:text/html,<script>alert(1)</script>"})
    assert "data:" not in html_out


def test_mailto_with_query_string():
    html = EXPORT.replace(
        '<a class="c2" href="https://form.example">FORM</a>',
        '<a class="c2" href="mailto:{email}?subject={subject}">FORM</a>'
    )
    t = template_from_export_zip(_zip(html, {"images/image1.png": PNG}))
    _, html_out = render(t, {"name": "test", "email": "a@b.com", "subject": "a b&c"})
    assert 'href="mailto:a@b.com?subject=a%20b%26c"' in html_out  # Email raw, query encoded


def _href_template():
    html = EXPORT.replace('href="https://form.example"', 'href="{link}"')
    return template_from_export_zip(_zip(html, {"images/image1.png": PNG}))


@pytest.mark.parametrize("evil", [
    "JavaScript:alert(1)", " javascript:alert(1)", "java\tscript:alert(1)",
    "java\nscript:alert(1)", "\x01javascript:alert(1)",
])
def test_obfuscated_javascript_href_blocked(evil):
    import re
    _, out = render(_href_template(), {"name": "x", "link": evil})
    href = re.search(r'href="([^"]*)"', out).group(1)
    assert "script:" not in re.sub(r"[\x00-\x20]", "", href).lower()


def test_https_href_stays_raw():
    _, out = render(_href_template(), {"name": "x", "link": "https://a.com/x?y=1"})
    assert 'href="https://a.com/x?y=1"' in out


def test_mailto_address_injection_quoted():
    html = EXPORT.replace('href="https://form.example"', 'href="mailto:{email}"')
    t = template_from_export_zip(_zip(html, {"images/image1.png": PNG}))
    _, out = render(t, {"name": "x", "email": "a@b.com?bcc=x"})
    assert "?bcc=" not in out


# ---------------------------------------------------------------------------
# Final review — Docs 링크 감싸기 풀기, 요소 선택자 CSS
# ---------------------------------------------------------------------------
def _doc(body: str, css: str = "") -> bytes:
    return _zip('<html><head><style type="text/css">' + css + '</style></head><body>'
                '<p><span>&#51228;&#47785;: S</span></p>' + body + "</body></html>")


def test_docs_wrapped_href_is_unwrapped():
    href = ("https://www.google.com/url?q=https://form.example/a?x%3D1%26y%3D2"
            "&amp;sa=D&amp;source=editors&amp;ust=1759300000000&amp;usg=AOvVaw0abc")
    t = template_from_export_zip(_doc(f'<p><a href="{href}">FORM</a></p>'))
    assert 'href="https://form.example/a?x=1&amp;y=2"' in t.html
    assert "google.com/url" not in t.html and "usg=" not in t.html


def test_docs_wrapped_variable_link_becomes_variable_again():
    t = template_from_export_zip(_doc(
        '<p><a href="https://www.google.com/url?q=%7Blink%7D&amp;sa=D&amp;source=editors&amp;usg=x">L</a></p>'
        '<p><a href="https://www.google.com/url?q=https://x.com/?id%3D%257Bkol%2520id%257D&amp;sa=D">M</a></p>'))
    assert find_variables(t) >= {"link", "kol id"}
    _, html = render(t, {"link": "https://ok.example/p", "kol id": "k 1"})
    assert 'href="https://ok.example/p"' in html
    assert 'href="https://x.com/?id=k%201"' in html and not unreplaced_html(html)


def test_non_wrapper_google_link_is_kept():
    t = template_from_export_zip(_doc('<p><a href="https://www.google.com/maps?q=seoul">map</a></p>'))
    assert 'href="https://www.google.com/maps?q=seoul"' in t.html


def test_element_rules_are_inlined():
    css = ('p{margin:0;color:#000000}li{font-size:11pt}table td,table th{padding:0}'
           'ol.lst-kix_a-0{margin:9px}p:first-child{color:red}ul>li{color:blue}.c3{margin:5px}')
    t = template_from_export_zip(_doc(
        '<p>one</p><p class="c3">two</p><ul><li>i</li></ul><table><tr><td>a</td><th>b</th></tr></table>', css))
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(t.html, "lxml")
    ps = soup.find_all("p")
    assert all("margin:0" in p.get("style", "") for p in ps)
    two = ps[1]["style"]
    assert two.index("margin:0") < two.index("margin:5px")  # 클래스가 요소 규칙을 덮는다
    assert "red" not in t.html and "blue" not in t.html and "9px" not in t.html
    assert soup.find("li")["style"].startswith("font-size:11pt")
    assert soup.find("td")["style"] == "padding:0;" and soup.find("th")["style"] == "padding:0;"


def test_inline_style_wins_over_rules():
    t = template_from_export_zip(_doc('<p class="c3" style="margin:7px">x</p>', "p{margin:0}.c3{margin:5px}"))
    assert 'style="margin:0;margin:5px;margin:7px"' in t.html
