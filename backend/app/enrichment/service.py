from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.audit.service import emit_event, redact_payload
from app.core.time import utcnow
from app.db.models import AccountFact, Contact, Draft, DraftEvidenceCheck, EvidenceSource, LeadFact


SNIPPET_MAX_CHARS = 240
FIELD_VALUE_MAX_CHARS = 500
USABLE_FACT_STATUSES = {"active", "approved"}
ALLOWED_SOURCE_USES = {"draft_context", "personalization", "both"}
UNSUPPORTED_PERSONALIZATION_PATTERNS = (
    r"\bI noticed your\b",
    r"\bnoticed your\b",
    r"\bI came across your\b",
    r"\bcame across your\b",
    r"\bimpressed by\b",
    r"\bimpressed with\b",
    r"\bfollowing your\b",
    r"\byour course\b",
    r"\byour platform\b",
    r"\byour content\b",
    r"\byour recent work\b",
)


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _aware(value, now):
    return value.replace(tzinfo=now.tzinfo) if value and value.tzinfo is None else value


def _redact_text(value: str | None, max_chars: int = SNIPPET_MAX_CHARS) -> str | None:
    if value is None:
        return None
    redacted = str(redact_payload(value))
    return redacted[:max_chars]


def _freshness(row: LeadFact | AccountFact, now=None) -> str:
    now = now or utcnow()
    if row.status not in USABLE_FACT_STATUSES:
        return "rejected" if row.status == "rejected" else "inactive"
    if row.expires_at and _aware(row.expires_at, now) < now:
        return "stale"
    if not row.source_id:
        return "missing_source"
    source = getattr(row, "source", None)
    if source and source.allowed_use not in ALLOWED_SOURCE_USES:
        return "source_not_allowed"
    return "fresh"


def is_usable_lead_fact(row: LeadFact, now=None) -> bool:
    return _freshness(row, now) == "fresh"


def _meaningful_tokens(value: str | None) -> set[str]:
    if not value:
        return set()
    return {token for token in re.findall(r"[a-z0-9]{4,}", value.lower()) if token not in {"your", "with", "that", "this", "from", "have"}}


def draft_requires_evidence(contact: Contact, draft_body: str | None = None) -> bool:
    body = (draft_body or "").lower()
    if not draft_body:
        return bool(contact.personalization)
    if any(re.search(pattern, body, flags=re.IGNORECASE) for pattern in UNSUPPORTED_PERSONALIZATION_PATTERNS):
        return True
    for value in (contact.business_name, contact.lead_category):
        normalized = (value or "").strip().lower()
        if normalized and len(normalized) >= 4 and normalized in body:
            return True
    if contact.website_url:
        domain = re.sub(r"^https?://", "", contact.website_url.strip().lower()).split("/", 1)[0]
        if domain and domain in body:
            return True
    personalization_tokens = _meaningful_tokens(contact.personalization)
    if personalization_tokens and len(personalization_tokens.intersection(_meaningful_tokens(draft_body))) >= 2:
        return True
    return False


def neutral_copy_for_contact(contact: Contact) -> dict[str, str]:
    name = contact.creator_name or contact.business_name or "there"
    first = name.split(" ", 1)[0] if name else "there"
    return {
        "subject": "Practical outreach idea",
        "body": (
            f"Hi {first},\n\n"
            "I wanted to share a practical idea in case improving outbound operations is a current priority.\n\n"
            "Would it be worth a quick look?\n\n"
            "Best regards"
        ),
    }


def evidence_source_to_dict(row: EvidenceSource | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "url": row.url,
        "publisher": row.publisher,
        "source_type": row.source_type,
        "retrieved_at": _iso(row.retrieved_at),
        "http_status": row.http_status,
        "reliability_tier": row.reliability_tier,
        "allowed_use": row.allowed_use,
        "raw_excerpt_redacted": _redact_text(row.raw_excerpt_redacted),
        "created_at": _iso(row.created_at),
    }


def lead_fact_to_dict(row: LeadFact) -> dict:
    freshness = _freshness(row)
    return {
        "id": row.id,
        "contact_id": row.contact_id,
        "field_key": row.field_key,
        "field_value": _redact_text(row.field_value, FIELD_VALUE_MAX_CHARS) or "",
        "source_id": row.source_id,
        "source_url": row.source_url,
        "source_label": row.source_label,
        "source_type": row.source_type,
        "confidence": row.confidence,
        "status": row.status,
        "freshness": freshness,
        "usable": freshness == "fresh",
        "fetched_at": _iso(row.fetched_at),
        "expires_at": _iso(row.expires_at),
        "extractor": row.extractor,
        "raw_snippet_redacted": _redact_text(row.raw_snippet_redacted),
        "created_at": _iso(row.created_at),
        "source": evidence_source_to_dict(row.source),
    }


def account_fact_to_dict(row: AccountFact) -> dict:
    freshness = _freshness(row)
    return {
        "id": row.id,
        "contact_id": row.contact_id,
        "account_key": row.account_key,
        "field_key": row.field_key,
        "field_value": _redact_text(row.field_value, FIELD_VALUE_MAX_CHARS) or "",
        "source_id": row.source_id,
        "source_url": row.source_url,
        "source_label": row.source_label,
        "confidence": row.confidence,
        "status": row.status,
        "freshness": freshness,
        "usable": freshness == "fresh",
        "fetched_at": _iso(row.fetched_at),
        "expires_at": _iso(row.expires_at),
        "extractor": row.extractor,
        "raw_snippet_redacted": _redact_text(row.raw_snippet_redacted),
        "created_at": _iso(row.created_at),
        "source": evidence_source_to_dict(row.source),
    }


def active_lead_facts(db: Session, contact_id: str) -> list[LeadFact]:
    now = utcnow()
    rows = (
        db.query(LeadFact)
        .filter(LeadFact.contact_id == contact_id, LeadFact.status == "active")
        .order_by(LeadFact.created_at.desc())
        .all()
    )
    return [row for row in rows if is_usable_lead_fact(row, now)]


def evidence_check(db: Session, contact: Contact | None, draft_body: str | None = None, *, draft_id: str | None = None, persist: bool = False) -> dict:
    if not contact:
        return {
            "contact_id": None,
            "status": "missing_contact",
            "supported_claims": [],
            "missing_evidence": ["contact"],
            "stale_facts": [],
            "neutral_copy_required": True,
            "neutral_subject": None,
            "neutral_body": None,
            "policy_version": "evidence_v1",
        }
    now = utcnow()
    facts = db.query(LeadFact).filter(LeadFact.contact_id == contact.id).order_by(LeadFact.created_at.desc()).all()
    active = []
    stale = []
    for fact in facts:
        if is_usable_lead_fact(fact, now):
            active.append(fact)
        else:
            stale.append(lead_fact_to_dict(fact))
    supported = [lead_fact_to_dict(fact) for fact in active]
    missing = []
    requires_evidence = draft_requires_evidence(contact, draft_body)
    if not supported and requires_evidence:
        missing.append("source_backed_personalization")
    neutral = neutral_copy_for_contact(contact) if missing else {"subject": None, "body": None}
    status = "passed" if supported or not requires_evidence else "neutral_required"
    payload = {
        "contact_id": contact.id,
        "status": status,
        "supported_claims": supported,
        "missing_evidence": missing,
        "stale_facts": stale,
        "neutral_copy_required": status == "neutral_required",
        "neutral_subject": neutral["subject"],
        "neutral_body": neutral["body"],
        "policy_version": "evidence_v1",
    }
    if persist:
        row = DraftEvidenceCheck(
            draft_id=draft_id,
            contact_id=contact.id,
            status=status,
            supported_claims_json=json.dumps(supported, sort_keys=True),
            missing_evidence_json=json.dumps(missing, sort_keys=True),
            stale_facts_json=json.dumps(stale, sort_keys=True),
            neutral_copy_required=status == "neutral_required",
            neutral_subject=neutral["subject"],
            neutral_body=neutral["body"],
            checked_at=now,
            policy_version="evidence_v1",
        )
        db.add(row)
        emit_event(
            db,
            "evidence.draft_checked",
            entity_type="draft" if draft_id else "contact",
            entity_id=draft_id or contact.id,
            payload={
                "contact_id": contact.id,
                "status": status,
                "supported_claim_count": len(supported),
                "missing_evidence": missing,
                "neutral_copy_required": status == "neutral_required",
            },
        )
    return payload


def evidence_policy_for_draft(db: Session, draft: Draft | None, contact: Contact | None, *, persist: bool = False) -> dict:
    if not draft or not contact:
        return evidence_check(db, contact, None, draft_id=getattr(draft, "id", None), persist=persist)
    combined_content = f"{draft.subject or ''}\n{draft.body or ''}"
    return evidence_check(db, contact, combined_content, draft_id=draft.id, persist=persist)


def create_manual_lead_fact(
    db: Session,
    contact: Contact,
    *,
    field_key: str,
    field_value: str,
    source_url: str | None = None,
    source_label: str = "operator",
    source_type: str = "manual",
    confidence: float = 0.7,
    raw_snippet: str | None = None,
) -> LeadFact:
    now = utcnow()
    if not source_label.strip() and not (source_url or "").strip():
        raise ValueError("source_label_or_url_required")
    safe_field_value = _redact_text(field_value, FIELD_VALUE_MAX_CHARS) or ""
    redacted_snippet = _redact_text(raw_snippet or safe_field_value) or ""
    source = EvidenceSource(
        url=source_url,
        publisher=source_label.strip() or source_url,
        source_type=source_type,
        retrieved_at=now,
        reliability_tier="operator_supplied",
        allowed_use="draft_context",
        raw_excerpt_redacted=redacted_snippet,
    )
    db.add(source)
    db.flush()
    fact = LeadFact(
        contact_id=contact.id,
        field_key=field_key,
        field_value=safe_field_value,
        source_id=source.id,
        source_url=source_url,
        source_label=source_label.strip() or source_url or "operator",
        source_type=source_type,
        confidence=max(0.0, min(1.0, confidence)),
        status="active",
        fetched_at=now,
        expires_at=now + timedelta(days=90),
        extractor="operator",
        raw_snippet_redacted=redacted_snippet,
    )
    db.add(fact)
    emit_event(db, "evidence.fact_created", entity_type="contact", entity_id=contact.id, payload={"field_key": field_key, "source_label": source_label})
    return fact


def ensure_seed_facts_for_contact(db: Session, contact: Contact) -> list[LeadFact]:
    existing = active_lead_facts(db, contact.id)
    if existing:
        return existing
    facts: list[LeadFact] = []
    seed_values: list[tuple[str, str, str]] = []
    if contact.website_url:
        seed_values.append(("website_url", contact.website_url, "contact_import"))
    if contact.lead_category:
        seed_values.append(("lead_category", contact.lead_category, "contact_import"))
    if contact.notes:
        seed_values.append(("operator_notes", contact.notes[:500], "operator_notes"))
    if contact.personalization:
        seed_values.append(("personalization_note", contact.personalization[:500], "operator_personalization"))
    for key, value, label in seed_values:
        facts.append(
            create_manual_lead_fact(
                db,
                contact,
                field_key=key,
                field_value=value,
                source_url=contact.website_url if key == "website_url" else None,
                source_label=label,
                confidence=0.55,
            )
        )
    return facts


def enrichment_summary(db: Session) -> dict:
    contacts = db.query(Contact).filter(Contact.deleted_at.is_(None)).all()
    lead_count = db.query(LeadFact).count()
    source_count = db.query(EvidenceSource).count()
    with_active = 0
    stale = 0
    now = utcnow()
    for contact in contacts:
        facts = db.query(LeadFact).filter(LeadFact.contact_id == contact.id).all()
        if any(is_usable_lead_fact(fact, now) for fact in facts):
            with_active += 1
        stale += sum(1 for fact in facts if fact.expires_at and fact.expires_at.replace(tzinfo=now.tzinfo) < now)
    return {
        "contacts": len(contacts),
        "contacts_with_evidence": with_active,
        "lead_facts": lead_count,
        "evidence_sources": source_count,
        "stale_facts": stale,
    }


def contact_evidence_payload(db: Session, contact: Contact) -> dict[str, Any]:
    return {
        "contact_id": contact.id,
        "contact_email": contact.email,
        "lead_facts": [lead_fact_to_dict(row) for row in db.query(LeadFact).filter(LeadFact.contact_id == contact.id).order_by(LeadFact.created_at.desc()).all()],
        "account_facts": [account_fact_to_dict(row) for row in db.query(AccountFact).filter(AccountFact.contact_id == contact.id).order_by(AccountFact.created_at.desc()).all()],
        "evidence_check": evidence_check(db, contact),
    }
