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


def test_korean_subject_and_sender_display_name():
    """Test that Korean characters in subject and sender display name are RFC 2047 encoded.

    Non-ASCII headers must be RFC 2047 encoded, and the raw bytes must be pure ASCII.
    """
    raw = build_mime(
        sender="마케팅팀 <mk@x.com>",
        to="a@b.com",
        subject="안녕하세요, 이것은 테스트 메일입니다",
        html="<p>Test</p>",
        images={}
    )
    # Verify raw bytes are pure ASCII
    raw.decode("ascii")  # Should not raise

    # Verify the decoded message has correct content
    m = _parse(raw)
    assert m["Subject"] == "안녕하세요, 이것은 테스트 메일입니다"
    assert "마케팅팀" in m["From"]
    assert "mk@x.com" in m["From"]
