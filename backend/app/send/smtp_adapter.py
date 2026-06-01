from __future__ import annotations

import asyncio
import base64
import os
import smtplib
import ssl
import uuid
from dataclasses import dataclass
from datetime import timezone
from email.message import EmailMessage
from email.utils import make_msgid
from functools import partial
from typing import Literal

import httpx

from app.core.time import utcnow
from app.send.fake_transport import FakeTransport

SenderReadiness = Literal["not_configured", "configured", "smtp_verified", "provider_verified", "canary_verified", "failed"]


@dataclass
class SendResult:
    status: str
    provider_msg_id: str | None
    smtp_response: str | None = None
    error_code: str | None = None
    error_detail: str | None = None


@dataclass
class CanaryResult:
    status: str
    nonce: str
    timestamp: str
    idempotency_key: str
    provider_msg_id: str | None
    sender_identity: str


class SMTPTransport:
    def verify(self, user: str, password: str) -> bool:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=30) as server:
            server.login(user, password)
        return True

    def send(self, *, sender: str, password: str, to: str, subject: str, body: str) -> dict:
        message = EmailMessage()
        message["From"] = sender
        message["To"] = to
        message["Subject"] = subject
        message["Message-ID"] = make_msgid(domain=(sender.rsplit("@", 1)[1] if "@" in sender else None))
        message.set_content(body)
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=30) as server:
            server.login(sender, password)
            response = server.send_message(message)
        return {
            "message_id": message.get("Message-ID") or f"smtp-{uuid.uuid4().hex}",
            "smtp_response": str(response or "sent"),
            "sender": sender,
            "to": to,
            "subject": subject,
            "body": body,
        }


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
        token = self._access_token()
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(self.profile_url, headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
            email_address = str(response.json().get("emailAddress") or "")
        return not email_address or not user or email_address.lower() == user.lower()

    def send(self, *, sender: str, password: str, to: str, subject: str, body: str) -> dict:
        del password
        token = self._access_token()
        message = EmailMessage()
        message["From"] = sender
        message["To"] = to
        message["Subject"] = subject
        message["Message-ID"] = make_msgid(domain=(sender.rsplit("@", 1)[1] if "@" in sender else None))
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                self.send_url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"raw": raw},
            )
            response.raise_for_status()
            payload = response.json()
        provider_id = payload.get("id") or message.get("Message-ID") or f"gmail-api-{uuid.uuid4().hex}"
        return {
            "message_id": str(provider_id),
            "smtp_response": "gmail_api:sent",
            "sender": sender,
            "to": to,
            "subject": subject,
            "body": body,
        }


_global_fake_transport = FakeTransport()


def default_transport():
    if os.getenv("FINIMATIC_TRANSPORT") == "fake":
        return _global_fake_transport
    return SMTPTransport()


class GmailAdapter:
    def __init__(self, transport=None):
        self.transport = transport or default_transport()

    @classmethod
    def from_settings(cls, db, transport=None) -> "GmailAdapter":
        if transport is not None:
            return cls(transport=transport)
        if os.getenv("FINIMATIC_TRANSPORT") == "fake":
            return cls(transport=_global_fake_transport)
        from app.settings.service import get_secret, get_value

        if get_value(db, "email_transport", "smtp") == "gmail_api":
            return cls(
                transport=GmailApiTransport(
                    client_id=get_secret(db, "gmail_api_client_id"),
                    client_secret=get_secret(db, "gmail_api_client_secret"),
                    refresh_token=get_secret(db, "gmail_api_refresh_token"),
                )
            )
        return cls()

    async def verify(self, user: str, password: str) -> SenderReadiness:
        if not user or (not password and not isinstance(self.transport, GmailApiTransport)):
            return "not_configured"
        loop = asyncio.get_running_loop()
        verified = await loop.run_in_executor(None, self.transport.verify, user, password)
        if not verified:
            return "failed"
        return "provider_verified" if isinstance(self.transport, GmailApiTransport) else "smtp_verified"

    async def send_message(self, to: str, subject: str, body: str, sender: str, password: str) -> SendResult:
        try:
            loop = asyncio.get_running_loop()
            send_call = partial(self.transport.send, sender=sender, password=password, to=to, subject=subject, body=body)
            result = await loop.run_in_executor(None, send_call)
        except Exception:
            return SendResult(status="failed", provider_msg_id=None, error_code="smtp_send_failed", error_detail="SMTP send failed")
        return SendResult(
            status="success",
            provider_msg_id=result.get("message_id"),
            smtp_response=result.get("smtp_response"),
        )

    async def canary_send(self, user: str, password: str, report_recipient: str, idempotency_key: str) -> CanaryResult:
        timestamp = utcnow().astimezone(timezone.utc).isoformat()
        nonce = f"{uuid.uuid4().hex}-{timestamp}"
        subject = f"Finimatic Canary {nonce}"
        body = f"Finimatic canary send\nnonce={nonce}\ntimestamp={timestamp}\nsender={user}\n"
        send_result = await self.send_message(report_recipient, subject, body, user, password)
        if send_result.status != "success":
            return CanaryResult("failed", nonce, timestamp, idempotency_key, None, user)
        return CanaryResult("success", nonce, timestamp, idempotency_key, send_result.provider_msg_id, user)
