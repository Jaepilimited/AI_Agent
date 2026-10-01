"""메일 MIME 조립 — 이미지는 cid 인라인 첨부 (multipart/related)."""
from __future__ import annotations

from email.message import EmailMessage
from email.utils import formataddr
from email.policy import SMTP


def build_mime(*, sender: str, to: str, subject: str, html: str,
               images: dict[str, tuple[bytes, str]], sender_name: str = "") -> bytes:
    """sender_name: Gmail '다른 주소에서 보내기' 에 등록된 표시 이름 — 있으면 '이름 <주소>' (한글은 RFC 2047)."""
    msg = EmailMessage()
    msg["From"] = formataddr((sender_name, sender)) if sender_name else sender
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
