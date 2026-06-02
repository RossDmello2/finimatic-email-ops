from __future__ import annotations

import json
import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.db.models import Contact
from app.settings.service import get_value


TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", re.IGNORECASE)


def parse_tags(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw = value
    else:
        raw = str(value).split(",")
    tags: list[str] = []
    seen: set[str] = set()
    for item in raw:
        tag = str(item).strip()
        if tag and tag not in seen:
            tags.append(tag)
            seen.add(tag)
    return tags


def custom_fields_with_tags(existing: str | None, tags_value) -> str:
    try:
        data = json.loads(existing or "{}")
    except json.JSONDecodeError:
        data = {}
    tags = parse_tags(tags_value)
    if tags:
        data["tags"] = tags
    elif "tags" not in data:
        data["tags"] = []
    return json.dumps(data)


def contact_tags(contact: Contact) -> list[str]:
    try:
        data = json.loads(contact.custom_fields or "{}")
    except json.JSONDecodeError:
        return []
    return parse_tags(data.get("tags"))


def email_domain(email: str | None) -> str:
    value = (email or "").strip().lower()
    if "@" not in value:
        return ""
    return value.rsplit("@", 1)[1]


def blocked_domains(db: Session) -> set[str]:
    raw = get_value(db, "blocked_domains")
    domains: set[str] = set()
    for line in raw.replace(",", "\n").splitlines():
        domain = line.strip().lower()
        if domain:
            domains.add(domain)
    return domains


def is_domain_blocked(db: Session, email: str | None) -> bool:
    domain = email_domain(email)
    if not domain:
        return False
    return domain in blocked_domains(db)


def _text_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, (str, int, float, bool)):
        return str(value).strip()
    return ""


def _email_local_name(email: str | None) -> str:
    local = (email or "").split("@", 1)[0].strip()
    return re.sub(r"[._+-]+", " ", local).strip()


def resolve_tokens(text: str, contact: Contact) -> str:
    display_name = (
        _text_value(contact.creator_name)
        or _text_value(contact.business_name)
        or _email_local_name(contact.email)
        or "there"
    )
    first_name = display_name.split(" ", 1)[0].strip() or "there"
    website = _text_value(contact.website_url) or "your work"
    niche = _text_value(contact.lead_category) or "your work"
    replacements = {
        "email": _text_value(contact.email),
        "first_name": first_name,
        "full_name": display_name,
        "name": display_name,
        "creator_name": _text_value(contact.creator_name) or display_name,
        "business_name": _text_value(contact.business_name),
        "company": _text_value(contact.business_name),
        "website": website,
        "website_url": website,
        "niche": niche,
        "lead_category": niche,
        "notes": _text_value(contact.notes),
        "personalization": _text_value(contact.personalization),
        "source": _text_value(contact.source),
    }
    try:
        custom_fields = json.loads(contact.custom_fields or "{}")
    except json.JSONDecodeError:
        custom_fields = {}
    if isinstance(custom_fields, dict):
        for key, value in custom_fields.items():
            normalized_key = str(key).strip().lower()
            if normalized_key and normalized_key not in replacements:
                replacements[normalized_key] = _text_value(value)

    def replace(match: re.Match[str]) -> str:
        token = match.group(1).lower()
        if token in replacements:
            return replacements[token]
        return match.group(0)

    return TOKEN_RE.sub(replace, text or "")


def _send_window_parts(db: Session) -> tuple[time | None, time | None, ZoneInfo]:
    start = get_value(db, "send_window_start", "09:00") or "09:00"
    end = get_value(db, "send_window_end", "17:00") or "17:00"
    tz_name = get_value(db, "send_timezone", "Asia/Kolkata") or "Asia/Kolkata"
    try:
        zone = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("UTC")
    try:
        start_time = datetime.strptime(start, "%H:%M").time()
        end_time = datetime.strptime(end, "%H:%M").time()
    except ValueError:
        return None, None, zone
    return start_time, end_time, zone


def send_window_open(db: Session, now: datetime | None = None) -> bool:
    start_time, end_time, zone = _send_window_parts(db)
    if start_time is None or end_time is None:
        return True
    local_now = (now or datetime.now(timezone.utc)).astimezone(zone).time()
    if start_time <= end_time:
        return start_time <= local_now <= end_time
    return local_now >= start_time or local_now <= end_time


def next_send_window_open_at(db: Session, now: datetime | None = None) -> datetime:
    current = now or datetime.now(timezone.utc)
    start_time, end_time, zone = _send_window_parts(db)
    if start_time is None or end_time is None or send_window_open(db, current):
        return current

    local_now = current.astimezone(zone)
    if start_time <= end_time:
        target_date = local_now.date() if local_now.time() < start_time else local_now.date() + timedelta(days=1)
    else:
        target_date = local_now.date()
    return datetime.combine(target_date, start_time, tzinfo=zone).astimezone(timezone.utc)
