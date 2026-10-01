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
