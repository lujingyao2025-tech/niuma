from __future__ import annotations

import re
from html import unescape

from .config import DEFAULT_BODY_TEMPLATE, DEFAULT_SUBJECT_TEMPLATE


NAME_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}


def city_only(location: str) -> str:
    """Normalize manual input and keep only the city or named city area."""
    clean = unescape(str(location or ""))
    clean = re.sub(r"^(?:location|地区|城市)\s*[:：]\s*", "", clean, flags=re.I)
    clean = re.sub(r"\s+", " ", clean).strip(" ·|-")
    city = clean.split(",", 1)[0].strip()
    return re.sub(r"^greater\s+", "", city, flags=re.I).strip()


def has_city(location: str) -> bool:
    city = city_only(location)
    return len(city) >= 2 and not any(character.isdigit() for character in city)


def salutation_name(name: str) -> str:
    """Use a surname when a full name is entered, otherwise use the given value."""
    clean = " ".join(str(name or "").strip().split())
    if not clean:
        return ""
    if "," in clean:
        surname = clean.split(",", 1)[0].strip()
        return surname or clean
    parts = clean.split()
    if len(parts) > 2 and parts[-1].lower() in NAME_SUFFIXES:
        return parts[-2]
    return parts[-1] if len(parts) > 1 else clean


def render_email(
    contact_name: str,
    location: str,
    sender_name: str,
    subject_template: str = DEFAULT_SUBJECT_TEMPLATE,
    body_template: str = DEFAULT_BODY_TEMPLATE,
    custom_variables: dict[str, str] | None = None,
) -> tuple[str, str]:
    values = {
        "first_name": contact_name,
        "location": location,
        "sender_name": sender_name,
    }
    custom_values = custom_variables if isinstance(custom_variables, dict) else {}
    for key, value in custom_values.items():
        values[str(key)] = str(value or "")

    def fill(template: str) -> str:
        def repl(match: re.Match) -> str:
            key = match.group(1)
            if key in values:
                return str(values[key] or "")
            if re.fullmatch(r"custom_\d+", key):
                return ""
            raise KeyError(key)

        return re.sub(r"\{([^{}]+)\}", repl, template)

    try:
        subject = fill(subject_template).strip()
        body = fill(body_template).strip()
    except KeyError as exc:
        raise ValueError(
            f"邮件模板包含无效占位符 {{{exc.args[0]}}}；"
            "请使用模板页面已标注的变量"
        ) from exc
    except (re.error, ValueError) as exc:
        raise ValueError("邮件模板包含无效占位符；请使用模板页面已标注的变量") from exc
    if not subject or not body:
        raise ValueError("邮件主题和正文不能为空")
    return subject, body
