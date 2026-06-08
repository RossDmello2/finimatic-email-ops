from __future__ import annotations

import imaplib
import os
import asyncio
import threading
from datetime import timedelta, timezone
from email import message_from_bytes, policy
from email.message import Message
from email.utils import parsedate_to_datetime, parseaddr

from groq import Groq
from sqlalchemy.orm import Session

from app.audit.service import emit_event
from app.core.time import utcnow
from app.db.models import Contact, ProviderHealth, Reply, SendAttempt
from app.replies.service import classify_intent, create_reply_record
from app.send.sequence import provider_acceptance_evidence_present
from app.settings.service import get_key_list, get_secret, get_value


CLASSIFICATIONS = {"reply", "unsubscribe", "bounce", "auto_reply", "unknown"}
CLASSIFICATION_SNIPPET_CHARS = 200
STORED_REPLY_BODY_CHARS = 4000
_FETCH_LOCK = threading.Lock()


def run_imap_fetch_with_lock(db: Session) -> dict:
    if not _FETCH_LOCK.acquire(blocking=False):
        return {"checked": 0, "matched": 0, "inserted": 0, "duplicates": 0, "skipped": True, "error_code": "imap_fetch_in_progress"}
    try:
        return IMAPReplyFetcher(db).run()
    finally:
        _FETCH_LOCK.release()


class IMAPReplyFetcher:
    def __init__(self, db: Session):
        self.db = db

    def run(self) -> dict:
        gmail_user = get_value(self.db, "gmail_user")
        gmail_app_password = get_secret(self.db, "gmail_app_password")
        if not gmail_user or not gmail_app_password:
            self._update_provider_health("failed", "imap_not_configured", "0 new replies found")
            self.db.commit()
            return {"checked": 0, "matched": 0, "inserted": 0, "duplicates": 0, "error_code": "imap_not_configured"}

        since_dt = utcnow() - timedelta(hours=24)
        contacts = self.db.query(Contact).filter(Contact.deleted_at.is_(None)).all()
        contacts_by_email = {contact.email.lower(): contact for contact in contacts}
        latest_sent_by_contact = self._latest_sent_by_contact()
        attempts_by_message_id = self._sent_attempts_by_message_id()
        checked = 0
        matched = 0
        inserted = 0
        duplicates = 0

        try:
            with imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=30) as client:
                client.login(gmail_user, gmail_app_password)
                client.select("INBOX")
                status, data = client.search(None, "SINCE", since_dt.strftime("%d-%b-%Y"))
                if status != "OK":
                    self._update_provider_health("failed", "imap_search_failed", "0 new replies found")
                    self.db.commit()
                    return {"checked": 0, "matched": 0, "inserted": 0, "duplicates": 0, "error_code": "imap_search_failed"}
                msg_ids = data[0].split()
                headers_by_id = self._fetch_headers_batch(client, msg_ids)
                for msg_id in msg_ids:
                    raw_headers = headers_by_id.get(msg_id)
                    if not raw_headers:
                        continue
                    message = message_from_bytes(raw_headers, policy=policy.default)
                    received_at = self._received_at(message)
                    if received_at and received_at < since_dt:
                        continue
                    checked += 1
                    sender = parseaddr(message.get("From", ""))[1].lower()
                    contact = self._contact_from_thread_headers(message, attempts_by_message_id) or contacts_by_email.get(sender)
                    if not contact:
                        continue
                    latest_sent_at = latest_sent_by_contact.get(contact.id)
                    comparable_received_at = self._comparable_datetime(received_at) if received_at else None
                    if not latest_sent_at or not comparable_received_at or comparable_received_at < latest_sent_at:
                        continue
                    matched += 1
                    subject = str(message.get("Subject", ""))
                    external_message_id = str(message.get("Message-ID", "") or "").strip() or None
                    if external_message_id:
                        existing_reply = (
                            self.db.query(Reply)
                            .filter(Reply.contact_id == contact.id, Reply.external_message_id == external_message_id)
                            .first()
                        )
                        if existing_reply:
                            create_reply_record(
                                self.db,
                                contact,
                                existing_reply.classified_as,
                                existing_reply.raw_summary,
                                subject=subject,
                                external_message_id=external_message_id,
                                stop_followups=True,
                                intent=existing_reply.intent,
                                received_at=received_at,
                            )
                            duplicates += 1
                            continue
                    raw_email = self._fetch_message(client, msg_id)
                    if not raw_email:
                        continue
                    message = message_from_bytes(raw_email, policy=policy.default)
                    body_text = self._body_text(message)
                    classified_as, intent = self._classify_with_intent(subject, body_text)
                    row, created = create_reply_record(
                        self.db,
                        contact,
                        classified_as,
                        body_text,
                        subject=subject,
                        external_message_id=external_message_id,
                        stop_followups=True,
                        intent=intent,
                        received_at=received_at,
                    )
                    if created:
                        inserted += 1
                        self.db.commit()
                        self._maybe_auto_reply(contact, row)
                        self.db.commit()
                    else:
                        duplicates += 1
                self._update_provider_health("ok", None, f"{inserted} new replies found")
                self.db.commit()
        except Exception as exc:
            self.db.rollback()
            emit_event(
                self.db,
                "provider.health_changed",
                entity_type="provider",
                entity_id="gmail_imap",
                payload={"provider": "gmail_imap", "status": "failed", "error_code": exc.__class__.__name__},
            )
            self._update_provider_health("failed", exc.__class__.__name__, f"{inserted} new replies found before failure")
            self.db.commit()
            return {"checked": checked, "matched": matched, "inserted": inserted, "duplicates": duplicates, "error_code": "imap_connection_failed"}

        return {"checked": checked, "matched": matched, "inserted": inserted, "duplicates": duplicates}

    def _update_provider_health(self, status: str, error_code: str | None, details: str) -> None:
        row = self.db.query(ProviderHealth).filter(ProviderHealth.provider == "imap").first()
        if row is None:
            row = ProviderHealth(provider="imap")
            self.db.add(row)
        row.status = status
        row.error_code = error_code
        row.details = details
        row.last_checked = utcnow()
        emit_event(
            self.db,
            "provider.health_changed",
            entity_type="provider",
            entity_id="imap",
            payload={"provider": "imap", "status": status, "error_code": error_code, "details": details},
        )

    def _maybe_auto_reply(self, contact: Contact, reply: Reply) -> None:
        try:
            from app.conversations.auto_reply_service import AutoReplyService

            asyncio.run(AutoReplyService().process_reply(contact.id, reply.id, self.db))
        except Exception as exc:
            emit_event(
                self.db,
                "auto_reply.failed",
                entity_type="contact",
                entity_id=contact.id,
                payload={"reply_id": reply.id, "error_code": exc.__class__.__name__},
            )

    def _latest_sent_by_contact(self) -> dict[str, object]:
        rows = (
            self.db.query(SendAttempt)
            .filter(SendAttempt.provider_accepted.is_(True), SendAttempt.contact_id != "canary", SendAttempt.sent_at.is_not(None))
            .order_by(SendAttempt.sent_at.desc())
            .all()
        )
        latest: dict[str, object] = {}
        for row in rows:
            if not provider_acceptance_evidence_present(row):
                continue
            if row.contact_id not in latest:
                sent_at = row.sent_at
                if getattr(sent_at, "tzinfo", None) is not None:
                    sent_at = sent_at.astimezone(timezone.utc).replace(tzinfo=None)
                latest[row.contact_id] = sent_at
        return latest

    def _sent_attempts_by_message_id(self) -> dict[str, SendAttempt]:
        rows = (
            self.db.query(SendAttempt)
            .filter(SendAttempt.provider_accepted.is_(True), SendAttempt.contact_id != "canary")
            .order_by(SendAttempt.sent_at.desc())
            .all()
        )
        attempts: dict[str, SendAttempt] = {}
        for row in rows:
            if not provider_acceptance_evidence_present(row):
                continue
            for raw_message_id in (row.provider_msg_id, row.tracking_message_id):
                normalized = self._normalize_message_id(raw_message_id)
                if normalized and normalized not in attempts:
                    attempts[normalized] = row
        return attempts

    def _contact_from_thread_headers(self, message: Message, attempts_by_message_id: dict[str, SendAttempt]) -> Contact | None:
        for message_id in self._thread_message_ids(message):
            attempt = attempts_by_message_id.get(message_id)
            if not attempt:
                continue
            contact = self.db.get(Contact, attempt.contact_id)
            if contact:
                return contact
        return None

    def _thread_message_ids(self, message: Message) -> list[str]:
        values: list[str] = []
        for header in ("In-Reply-To", "References"):
            raw = str(message.get(header, "") or "")
            for part in raw.replace("\r", " ").replace("\n", " ").split():
                normalized = self._normalize_message_id(part)
                if normalized and normalized not in values:
                    values.append(normalized)
        return values

    def _normalize_message_id(self, value: str | None) -> str:
        raw = (value or "").strip().strip("<>").lower()
        return raw

    def _comparable_datetime(self, value):
        if getattr(value, "tzinfo", None) is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def _fetch_message(self, client: imaplib.IMAP4_SSL, msg_id: bytes) -> bytes | None:
        status, parts = client.fetch(msg_id, "(RFC822)")
        if status != "OK":
            return None
        for part in parts:
            if isinstance(part, tuple):
                return part[1]
        return None

    def _fetch_headers_batch(self, client: imaplib.IMAP4_SSL, msg_ids: list[bytes]) -> dict[bytes, bytes]:
        if not msg_ids:
            return {}
        fields = "FROM DATE SUBJECT MESSAGE-ID IN-REPLY-TO REFERENCES"
        status, parts = client.fetch(b",".join(msg_ids).decode("ascii"), f"(BODY.PEEK[HEADER.FIELDS ({fields})])")
        if status != "OK":
            return {}
        headers: dict[bytes, bytes] = {}
        for part in parts:
            if isinstance(part, tuple):
                msg_id = bytes(part[0]).split(b" ", 1)[0]
                headers[msg_id] = part[1]
        return headers

    def _received_at(self, message: Message):
        try:
            parsed = parsedate_to_datetime(message.get("Date", ""))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _body_text(self, message: Message) -> str:
        body = ""
        if message.is_multipart():
            for part in message.walk():
                if part.get_content_type() == "text/plain" and not part.get_filename():
                    body = part.get_content()
                    break
        elif message.get_content_type() == "text/plain":
            body = message.get_content()
        if not body:
            body = str(message.get_payload() or "")
        return " ".join(str(body).split())[:STORED_REPLY_BODY_CHARS]

    def _classify(self, subject: str, snippet: str) -> str:
        subject_lower = (subject or "").lower()
        snippet_lower = (snippet or "").lower()
        lowered = f"{subject_lower} {snippet_lower}"
        if any(cue in lowered for cue in ("unsubscribe", "remove me", "remove from your mailing list", "do not email", "don't email", "do not contact me again", "stop emailing", "stop sending")):
            return "unsubscribe"
        if any(cue in lowered for cue in ("delivery status notification", "undeliverable", "address not found", "mail delivery failed")):
            return "bounce"
        auto_subject = subject_lower.startswith(("automatic reply", "out of office"))
        auto_body = any(cue in snippet_lower for cue in ("out of office", "automatic reply", "vacation responder", "auto-generated"))
        if auto_subject or auto_body:
            return "auto_reply"
        keys = get_key_list(self.db, "groq_keys")
        if not keys:
            return "unknown"
        prompt = (
            "Classify this email reply as exactly one of: "
            "reply | unsubscribe | bounce | auto_reply | unknown.\n"
            f"Subject: {subject}\n"
            f"Snippet: {snippet}\n"
            "Return only the classification word."
        )
        try:
            client = Groq(api_key=keys[0])
            response = client.chat.completions.create(
                model=os.getenv("GROQ_MODEL_FAST", "llama-3.3-70b-versatile"),
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                timeout=float(os.getenv("GROQ_TIMEOUT_S", "30")),
            )
            raw = (response.choices[0].message.content or "").strip().lower()
        except Exception as exc:
            emit_event(
                self.db,
                "provider.health_changed",
                entity_type="provider",
                entity_id="groq",
                payload={"provider": "groq", "status": "degraded", "error_code": exc.__class__.__name__},
            )
            return "unknown"
        for value in CLASSIFICATIONS:
            if raw == value or raw.startswith(value):
                if value == "auto_reply":
                    if "?" in snippet_lower:
                        return "reply"
                    return "unknown"
                return value
        return "unknown"

    def _classify_with_intent(self, subject: str, body_text: str) -> tuple[str, str]:
        snippet = (body_text or "")[:CLASSIFICATION_SNIPPET_CHARS]
        if not snippet.strip():
            return "unknown", "unknown"
        classified_as = self._classify(subject, snippet)
        intent = (
            "unsubscribe"
            if classified_as == "unsubscribe"
            else "bounce"
            if classified_as == "bounce"
            else "auto_reply"
            if classified_as == "auto_reply"
            else classify_intent(subject, snippet, self.db)
        )
        return classified_as, intent
