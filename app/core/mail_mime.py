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
