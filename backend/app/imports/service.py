from __future__ import annotations

import csv
import io
import json
import re
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.ai.gateway import AIGateway, GROQ_MODEL_DEFAULT
from app.audit.service import emit_event
from app.contacts.utils import custom_fields_with_tags, is_domain_blocked
from app.db.models import Contact, ImportBatch, ImportRow, Suppression
from app.db.session import SessionLocal
from app.settings.service import get_key_list, get_value

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PREVIEWS: dict[str, dict[str, Any]] = {}


class PreviewExpiredError(Exception):
    pass

HEADER_ALIASES = {
    "email": "email",
    "email_address": "email",
    "email_id": "email",
    "e_mail": "email",
    "creator_name": "creator_name",
    "creator": "creator_name",
    "name": "creator_name",
    "full_name": "creator_name",
    "business_name": "business_name",
    "business": "business_name",
    "company": "business_name",
    "website": "website_url",
    "website_url": "website_url",
    "url": "website_url",
    "youtube": "website_url",
    "youtube_url": "website_url",
    "channel": "website_url",
    "channel_url": "website_url",
    "notes": "notes",
    "note": "notes",
    "personalization": "personalization",
    "info": "personalization",
    "creator_info": "personalization",
    "creator_context": "personalization",
    "context": "personalization",
    "about": "personalization",
    "description": "personalization",
    "lead_category": "lead_category",
    "niche": "lead_category",
    "tags": "tags",
    "source": "source",
}

CANONICAL_IMPORT_FIELDS = {
    "email",
    "creator_name",
    "business_name",
    "website_url",
    "notes",
    "personalization",
    "lead_category",
    "tags",
    "source",
}

POSITIONAL_FIELDS = ["email", "creator_name", "website_url", "notes", "tags", "personalization"]


def normalize_email(value: str | None) -> str:
    return (value or "").strip().lower()


def valid_email(value: str) -> bool:
    return bool(EMAIL_RE.match(value))


def normalize_header(value: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower()).strip("_")
    return HEADER_ALIASES.get(key, key)


def default_source_for(format_name: str) -> str:
    normalized = (format_name or "").strip().lower()
    if normalized == "csv":
        return "csv_import"
    if normalized == "txt":
        return "txt_import"
    return normalized or "manual"


def normalize_import_row(raw: dict, default_source: str) -> dict:
    normalized: dict[str, Any] = {}
    extras: dict[str, Any] = {}
    for key, value in (raw or {}).items():
        normalized_key = normalize_header(str(key))
        if normalized_key in CANONICAL_IMPORT_FIELDS:
            if value not in (None, ""):
                normalized[normalized_key] = value
        elif value not in (None, ""):
            extras[str(key)] = value

    normalized["source"] = normalized.get("source") or default_source
    if extras:
        normalized["_extra"] = extras
    return normalized


def read_delimited_rows(content: str, delimiter: str) -> list[list[str]]:
    return [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(content), delimiter=delimiter) if any(cell.strip() for cell in row)]


def parse_structured_content(content: str, default_source: str) -> list[dict]:
    delimiter = "\t" if "\t" in content and "," not in content.splitlines()[0] else ","
    rows = read_delimited_rows(content, delimiter)
    if not rows:
        return []
    raw_headers = [cell.strip() for cell in rows[0]]
    headers = [normalize_header(cell) for cell in raw_headers]
    has_header = "email" in headers
    parsed: list[dict] = []
    if has_header:
        for cells in rows[1:]:
            row = {raw_headers[index]: cells[index] for index in range(min(len(raw_headers), len(cells))) if raw_headers[index]}
            parsed.append(normalize_import_row(row, default_source))
        return parsed
    for cells in rows:
        row = {field: cells[index] for index, field in enumerate(POSITIONAL_FIELDS) if index < len(cells)}
        parsed.append(normalize_import_row(row, default_source))
    return parsed


def parse_payload(format_name: str, rows: list[dict] | None = None, content: str | None = None) -> list[dict]:
    default_source = default_source_for(format_name)
    if rows is not None:
        return [normalize_import_row(row, default_source) for row in rows]
    if not content:
        return []
    if format_name == "csv" or "," in content or "\t" in content:
        return parse_structured_content(content, default_source)
    parsed = []
    for line in content.splitlines():
        text = line.strip()
        if not text:
            continue
        parsed.append(normalize_import_row({"email": text.split()[0]}, default_source))
    return parsed


def evaluate_rows(db: Session, rows: list[dict]) -> list[dict]:
    results = []
    seen_in_payload: set[str] = set()
    for index, raw in enumerate(rows, start=1):
        email = normalize_email(raw.get("email"))
        creator = raw.get("creator_name")
        business = raw.get("business_name")
        status = "accepted"
        reason = None
        if not email or not valid_email(email):
            status, reason = "invalid_email", "Email address is invalid"
        elif not creator and not business:
            status, reason = "missing_field", "creator_name or business_name is required"
        elif email in seen_in_payload:
            status, reason = "duplicate", "Duplicate in submitted rows"
        else:
            existing_contact = db.query(Contact).filter(Contact.email == email).first()
            if existing_contact and existing_contact.deleted_at is not None:
                status, reason = "restore", "Contact was deleted and will be restored"
            elif existing_contact:
                status, reason = "duplicate", "Contact already exists"
        if status == "accepted" and is_domain_blocked(db, email):
            status, reason = "suppressed", "domain_blocked"
        elif status == "accepted" and db.query(Suppression).filter_by(email=email).first():
            status, reason = "suppressed", "Email is suppressed"

        if status in {"accepted", "restore"}:
            seen_in_payload.add(email)
        results.append(
            {
                "row_num": index,
                "raw_data": raw,
                "email": email,
                "status": status,
                "reason": reason,
                "parsed_data": {**raw, "email": email, "source": raw.get("source") or "manual"},
            }
        )
    return results


def preview_import(db: Session, format_name: str, rows: list[dict] | None, content: str | None, filename: str | None) -> dict:
    parsed = parse_payload(format_name, rows, content)
    evaluated = evaluate_rows(db, parsed)
    batch_id_temp = uuid.uuid4().hex
    PREVIEWS[batch_id_temp] = {"format": format_name, "rows": parsed, "filename": filename, "contact_ids": []}
    emit_event(db, "import.preview", payload={"total": len(evaluated), "format": format_name})
    db.commit()
    return {"batch_id_temp": batch_id_temp, "rows": evaluated, "summary": summarize(evaluated)}


def commit_import(db: Session, batch_id_temp: str) -> dict:
    preview = PREVIEWS.get(batch_id_temp)
    if not preview:
        raise PreviewExpiredError(batch_id_temp)
    evaluated = evaluate_rows(db, preview["rows"])
    summary = summarize(evaluated)
    batch = ImportBatch(
        filename=preview.get("filename"),
        format=preview.get("format") or "manual",
        total=len(evaluated),
        accepted=summary["accepted"],
        rejected=summary["rejected"],
        duplicate=summary["duplicate"],
        suppressed=summary["suppressed"],
    )
    db.add(batch)
    db.flush()
    contact_ids: list[str] = []
    for row in evaluated:
        contact_id = None
        if row["status"] in {"accepted", "restore"}:
            data = row["parsed_data"]
            contact = db.query(Contact).filter(Contact.email == data["email"], Contact.deleted_at.is_not(None)).first()
            if contact:
                contact.deleted_at = None
                contact.creator_name = data.get("creator_name") or contact.creator_name
                contact.business_name = data.get("business_name") or contact.business_name
                contact.website_url = data.get("website_url") or contact.website_url
                contact.source = data.get("source") or preview.get("format") or contact.source or "manual"
                contact.provenance = preview.get("filename") or contact.provenance
                contact.notes = data.get("notes") or contact.notes
                contact.personalization = data.get("personalization") or contact.personalization
                contact.lead_category = data.get("lead_category") or contact.lead_category
                contact.custom_fields = custom_fields_with_tags(contact.custom_fields, data.get("tags"))
                contact.import_batch_id = batch.id
                emit_event(db, "contact.restored", entity_type="contact", entity_id=contact.id, payload={"source": "import"})
            else:
                contact = Contact(
                    email=data["email"],
                    creator_name=data.get("creator_name"),
                    business_name=data.get("business_name"),
                    website_url=data.get("website_url"),
                    source=data.get("source") or preview.get("format") or "manual",
                    provenance=preview.get("filename"),
                    notes=data.get("notes"),
                    personalization=data.get("personalization"),
                    lead_category=data.get("lead_category"),
                    custom_fields=custom_fields_with_tags(None, data.get("tags")),
                    import_batch_id=batch.id,
                )
                db.add(contact)
            db.flush()
            contact_id = contact.id
            contact_ids.append(contact.id)
        else:
            emit_event(db, "import.row_rejected", payload={"status": row["status"], "email": row["email"]})
        db.add(
            ImportRow(
                batch_id=batch.id,
                row_num=row["row_num"],
                raw_data=json.dumps(row["raw_data"]),
                email=row["email"],
                status=row["status"],
                reason=row["reason"],
                contact_id=contact_id,
            )
        )
    preview["contact_ids"] = list(set((preview.get("contact_ids") or []) + contact_ids))
    emit_event(db, "import.committed", entity_type="import_batch", entity_id=batch.id, payload=summary)
    db.commit()
    PREVIEWS.pop(batch_id_temp, None)
    return {"batch_id": batch.id, "rows": evaluated, "summary": summary, "contact_ids": contact_ids}


def summarize(rows: list[dict]) -> dict:
    accepted = sum(1 for row in rows if row["status"] == "accepted")
    restored = sum(1 for row in rows if row["status"] == "restore")
    duplicate = sum(1 for row in rows if row["status"] == "duplicate")
    suppressed = sum(1 for row in rows if row["status"] == "suppressed")
    rejected = len(rows) - accepted - restored
    return {"accepted": accepted, "restored": restored, "rejected": rejected, "duplicate": duplicate, "suppressed": suppressed, "total": len(rows)}


async def enrich_imported_contacts(contact_ids: list[str]) -> None:
    if not contact_ids:
        return
    with SessionLocal() as db:
        gateway = AIGateway(
            get_key_list(db, "groq_keys"),
            get_key_list(db, "gemini_keys"),
            get_value(db, "campaign_context"),
            None,
            get_value(db, "groq_model", GROQ_MODEL_DEFAULT),
            None,
        )
        for contact_id in contact_ids:
            contact = db.get(Contact, contact_id)
            if not contact or not contact.website_url or contact.personalization:
                continue
            text = await gateway.enrich_contact(contact)
            if not text:
                continue
            contact.personalization = text
            emit_event(db, "contact.enriched", entity_type="contact", entity_id=contact.id)
        db.commit()
