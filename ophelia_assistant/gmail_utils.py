from __future__ import annotations

import re
from urllib.parse import quote, urlencode


RECIPIENT_LABEL_RE = re.compile(
    r"^(Recipients?|To|收件人|收件者|收件者地址|收件人地址)[：:]?$", re.I
)
SUBJECT_LABEL_RE = re.compile(r"^(Subject|主题|主旨)[：:]?$", re.I)


def gmail_compose_url(recipient: str, subject: str, body: str) -> str:
    query = urlencode(
        {
            "view": "cm",
            "fs": "1",
            "tf": "1",
            "to": recipient,
            "su": subject,
            "body": body,
        },
        quote_via=quote,
    )
    return f"https://mail.google.com/mail/u/0/?{query}"


def gmail_new_message_url() -> str:
    return "https://mail.google.com/mail/u/0/#inbox?compose=new"
