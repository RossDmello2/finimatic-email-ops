from __future__ import annotations

import asyncio
import base64
import os
import smtplib
import socket
import ssl
import uuid
from dataclasses import dataclass
from datetime import timezone
from email.message import EmailMessage
from email.utils import make_msgid
from functools import partial
from typing import Callable, Literal

import httpx

from app.core.time import utcnow
from app.send.fake_transport import FakeTransport
from app.send.outcomes import SendOutcome, TransportResolution, simulated_outcome, validate_send_outcome

SenderReadiness = Literal["not_configured", "configured", "smtp_verified", "provider_verified", "canary_verified", "failed"]


class AmbiguousProviderResult(RuntimeError):
    def __init__(self, *, provider_contacted: bool, classification: str):
        super().__init__(classification)
        self.provider_contacted = provider_contacted
        self.classification = classification


class ProviderNotContacted(RuntimeError):
    def __init__(self, classification: str):
        super().__init__(classification)
        self.classification = classification


@dataclass(frozen=True)
class CanaryResult:
    outcome: SendOutcome
    nonce: str
    timestamp: str
    idempotency_key: str
    sender_identity: str


class SMTPTransport:
    def verify(self, user: str, password: str) -> bool:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=30) as server:
            server.login(user, password)
        return True

    def send(
        self,
        *,
        sender: str,
        password: str,
        to: str,
        subject: str,
        body: str,
        resolution: TransportResolution | None = None,
    ) -> SendOutcome:
        resolution = resolution or TransportResolution("smtp", "smtp", "explicit_injection", False)
        message = EmailMessage()
        message["From"] = sender
        message["To"] = to
        message["Subject"] = subject
        message["Message-ID"] = make_msgid(domain=(sender.rsplit("@", 1)[1] if "@" in sender else None))
        message.set_content(body)
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=30) as server:
            server.login(sender, password)
            try:
                refused = server.send_message(message)
            except (
                TimeoutError,
                socket.timeout,
                smtplib.SMTPServerDisconnected,
                ConnectionResetError,
                ConnectionAbortedError,
                BrokenPipeError,
                ssl.SSLError,
            ) as exc:
                raise AmbiguousProviderResult(
                    provider_contacted=True,
                    classification="smtp_message_transaction_timeout",
                ) from exc
        if refused:
            return SendOutcome(
                attempt_status="failed",
                configured_transport=resolution.configured_transport,
                effective_transport=resolution.effective_transport,
                transport_source=resolution.transport_source,
                simulated=False,
                provider_contacted=True,
                provider_accepted=False,
                tracking_message_id=message.get("Message-ID"),
                provider_response_classification="smtp_recipients_refused",
                error_code="smtp_recipients_refused",
                error_detail_redacted="SMTP provider refused one or more recipients",
            )
        return SendOutcome(
            attempt_status="provider_accepted",
            configured_transport=resolution.configured_transport,
            effective_transport=resolution.effective_transport,
            transport_source=resolution.transport_source,
            simulated=False,
            provider_contacted=True,
            provider_accepted=True,
            tracking_message_id=message.get("Message-ID"),
            provider_response_classification="smtp_transaction_completed",
        )


class GmailApiTransport:
    token_url = "https://oauth2.googleapis.com/token"
    profile_url = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
    send_url = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

    def __init__(self, *, client_id: str, client_secret: str, refresh_token: str, timeout: float = 30.0):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.timeout = timeout

    def _access_token(self) -> str:
        if not self.client_id or not self.client_secret or not self.refresh_token:
            raise ValueError("Gmail API OAuth credentials are not configured")
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                self.token_url,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": self.refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            response.raise_for_status()
            token = response.json().get("access_token")
            if not token:
                raise ValueError("Gmail API did not return an access token")
            return str(token)

    def verify(self, user: str, password: str) -> bool:
        del password
        try:
            token = self._access_token()
        except httpx.TimeoutException as exc:
            raise AmbiguousProviderResult(
                provider_contacted=False,
                classification="gmail_oauth_token_timeout",
            ) from exc
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(self.profile_url, headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
            email_address = str(response.json().get("emailAddress") or "")
        if not email_address:
            raise ValueError("Gmail API profile did not return an email identity")
        if user and email_address.lower() != user.lower():
            raise ValueError("Gmail API OAuth account does not match configured sender")
        return True

    def send(
        self,
        *,
        sender: str,
        password: str,
        to: str,
        subject: str,
        body: str,
        resolution: TransportResolution | None = None,
    ) -> SendOutcome:
        resolution = resolution or TransportResolution("gmail_api", "gmail_api", "explicit_injection", False)
        del password
        token = self._access_token()
        message = EmailMessage()
        message["From"] = sender
        message["To"] = to
        message["Subject"] = subject
        message["Message-ID"] = make_msgid(domain=(sender.rsplit("@", 1)[1] if "@" in sender else None))
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    self.send_url,
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    json={"raw": raw},
                )
                response.raise_for_status()
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise AmbiguousProviderResult(
                        provider_contacted=True,
                        classification="gmail_api_invalid_success_response",
                    ) from exc
        except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
            raise ProviderNotContacted("gmail_message_endpoint_connect_failed") from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code == 408 or status_code >= 500:
                raise AmbiguousProviderResult(
                    provider_contacted=True,
                    classification=f"gmail_api_http_{status_code}_ambiguous",
                ) from exc
            return SendOutcome(
                attempt_status="failed",
                configured_transport=resolution.configured_transport,
                effective_transport=resolution.effective_transport,
                transport_source=resolution.transport_source,
                simulated=False,
                provider_contacted=True,
                provider_accepted=False,
                tracking_message_id=message.get("Message-ID"),
                provider_response_classification=f"gmail_api_http_{status_code}_rejected",
                error_code="gmail_api_request_rejected",
                error_detail_redacted="Gmail API rejected the message request",
            )
        except httpx.TransportError as exc:
            raise AmbiguousProviderResult(
                provider_contacted=True,
                classification="gmail_message_endpoint_timeout",
            ) from exc
        provider_id = str(payload.get("id") or "").strip()
        if not provider_id:
            return SendOutcome(
                attempt_status="reconciliation_required",
                configured_transport=resolution.configured_transport,
                effective_transport=resolution.effective_transport,
                transport_source=resolution.transport_source,
                simulated=False,
                provider_contacted=True,
                provider_accepted=False,
                tracking_message_id=message.get("Message-ID"),
                provider_response_classification="gmail_api_missing_message_id",
                error_code="provider_response_ambiguous",
                error_detail_redacted="Gmail API returned success without a native message identifier",
            )
        return SendOutcome(
            attempt_status="provider_accepted",
            configured_transport=resolution.configured_transport,
            effective_transport=resolution.effective_transport,
            transport_source=resolution.transport_source,
            simulated=False,
            provider_contacted=True,
            provider_accepted=True,
            provider_message_id=provider_id,
            tracking_message_id=message.get("Message-ID"),
            provider_response_classification="gmail_api_accepted",
        )


_global_fake_transport = FakeTransport()
_test_transport_factory: Callable[[], object] | None = None


def set_test_transport_factory(factory: Callable[[], object] | None) -> None:
    global _test_transport_factory
    _test_transport_factory = factory


def resolve_transport(db=None, transport=None) -> tuple[object, TransportResolution]:
    configured = "smtp"
    if db is not None:
        from app.settings.service import get_value

        configured = get_value(db, "email_transport", "smtp")
    if transport is not None:
        effective = transport_name(transport)
        return transport, TransportResolution(
            configured,
            effective,
            "explicit_injection",
            isinstance(transport, FakeTransport) or effective == "fake",
        )
    if _test_transport_factory is not None:
        selected = _test_transport_factory()
        effective = transport_name(selected)
        return selected, TransportResolution(
            configured,
            effective,
            "test_fixture",
            isinstance(selected, FakeTransport) or effective == "fake",
        )
    if os.getenv("FINIMATIC_TRANSPORT", "").strip().lower() == "fake":
        return _global_fake_transport, TransportResolution(configured, "fake", "environment:FINIMATIC_TRANSPORT", True)
    if configured == "gmail_api" and db is not None:
        from app.settings.service import get_secret

        return (
            GmailApiTransport(
                client_id=get_secret(db, "gmail_api_client_id"),
                client_secret=get_secret(db, "gmail_api_client_secret"),
                refresh_token=get_secret(db, "gmail_api_refresh_token"),
            ),
            TransportResolution(configured, "gmail_api", "persisted:email_transport", False),
        )
    return SMTPTransport(), TransportResolution(configured, "smtp", "persisted:email_transport", False)


def resolve_transport_metadata(db) -> TransportResolution:
    from app.settings.service import get_value

    configured = get_value(db, "email_transport", "smtp")
    if _test_transport_factory is not None:
        selected = _test_transport_factory()
        effective = transport_name(selected)
        return TransportResolution(
            configured,
            effective,
            "test_fixture",
            isinstance(selected, FakeTransport) or effective == "fake",
        )
    if os.getenv("FINIMATIC_TRANSPORT", "").strip().lower() == "fake":
        return TransportResolution(configured, "fake", "environment:FINIMATIC_TRANSPORT", True)
    return TransportResolution(configured, configured, "persisted:email_transport", False)


def transport_name(transport: object) -> str:
    if isinstance(transport, FakeTransport):
        return "fake"
    if isinstance(transport, GmailApiTransport):
        return "gmail_api"
    if isinstance(transport, SMTPTransport):
        return "smtp"
    return getattr(transport, "transport_name", transport.__class__.__name__.lower())


def default_transport():
    return resolve_transport()[0]


class GmailAdapter:
    def __init__(self, transport=None, resolution: TransportResolution | None = None):
        self.transport = transport or default_transport()
        self.resolution = resolution or TransportResolution(
            transport_name(self.transport),
            transport_name(self.transport),
            "explicit_injection",
            isinstance(self.transport, FakeTransport) or transport_name(self.transport) == "fake",
        )

    @classmethod
    def from_settings(cls, db, transport=None) -> "GmailAdapter":
        selected, resolution = resolve_transport(db, transport)
        return cls(selected, resolution)

    async def verify(self, user: str, password: str) -> SenderReadiness:
        if self.resolution.simulated:
            return "configured" if user else "not_configured"
        if not user or (not password and not isinstance(self.transport, GmailApiTransport)):
            return "not_configured"
        loop = asyncio.get_running_loop()
        verified = await loop.run_in_executor(None, self.transport.verify, user, password)
        if not verified:
            return "failed"
        return "provider_verified" if isinstance(self.transport, GmailApiTransport) else "smtp_verified"

    async def send_message(self, to: str, subject: str, body: str, sender: str, password: str) -> SendOutcome:
        if self.resolution.simulated:
            return simulated_outcome(
                self.resolution,
                error_code="effective_transport_simulated",
                detail="Effective transport is simulated; live provider execution is blocked",
                blocked=True,
            )
        try:
            loop = asyncio.get_running_loop()
            send_call = partial(
                self.transport.send,
                sender=sender,
                password=password,
                to=to,
                subject=subject,
                body=body,
                resolution=self.resolution,
            )
            outcome = await loop.run_in_executor(None, send_call)
            return validate_send_outcome(
                outcome,
                self.resolution,
                trusted_provider_adapter=(
                    isinstance(self.transport, (GmailApiTransport, SMTPTransport))
                    or self.resolution.transport_source == "test_fixture"
                ),
            )
        except ProviderNotContacted as exc:
            return SendOutcome(
                attempt_status="failed",
                configured_transport=self.resolution.configured_transport,
                effective_transport=self.resolution.effective_transport,
                transport_source=self.resolution.transport_source,
                simulated=False,
                provider_contacted=False,
                provider_accepted=False,
                provider_response_classification=exc.classification,
                error_code="provider_not_contacted",
                error_detail_redacted="Provider connection failed before the message transaction began",
            )
        except AmbiguousProviderResult as exc:
            return SendOutcome(
                attempt_status="reconciliation_required",
                configured_transport=self.resolution.configured_transport,
                effective_transport=self.resolution.effective_transport,
                transport_source=self.resolution.transport_source,
                simulated=False,
                provider_contacted=exc.provider_contacted,
                provider_accepted=False,
                provider_response_classification=exc.classification,
                error_code="provider_timeout_ambiguous",
                error_detail_redacted="Provider outcome is ambiguous after timeout; reconcile before retry",
            )
        except (TimeoutError, socket.timeout, httpx.TimeoutException):
            return SendOutcome(
                attempt_status="reconciliation_required",
                configured_transport=self.resolution.configured_transport,
                effective_transport=self.resolution.effective_transport,
                transport_source=self.resolution.transport_source,
                simulated=False,
                provider_contacted=False,
                provider_accepted=False,
                provider_response_classification="provider_timeout_before_message_transaction",
                error_code="provider_timeout_ambiguous",
                error_detail_redacted="Provider outcome is ambiguous after timeout; reconcile before retry",
            )
        except Exception as exc:
            return SendOutcome(
                attempt_status="failed",
                configured_transport=self.resolution.configured_transport,
                effective_transport=self.resolution.effective_transport,
                transport_source=self.resolution.transport_source,
                simulated=False,
                provider_contacted=False,
                provider_accepted=False,
                provider_response_classification="provider_call_failed",
                error_code="provider_send_failed",
                error_detail_redacted=f"Provider send failed: {exc.__class__.__name__}",
            )

    async def canary_send(self, user: str, password: str, report_recipient: str, idempotency_key: str) -> CanaryResult:
        timestamp = utcnow().astimezone(timezone.utc).isoformat()
        nonce = f"{uuid.uuid4().hex}-{timestamp}"
        subject = f"Finimatic Canary {nonce}"
        body = f"Finimatic canary send\nnonce={nonce}\ntimestamp={timestamp}\nsender={user}\n"
        outcome = (await self.send_message(report_recipient, subject, body, user, password)).with_context(
            idempotency_key=idempotency_key
        )
        return CanaryResult(outcome, nonce, timestamp, idempotency_key, user)
