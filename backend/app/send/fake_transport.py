from __future__ import annotations

import uuid

from app.send.outcomes import SendOutcome, TransportResolution


class FakeTransport:
    """Automated-test transport. It never calls smtp.gmail.com."""

    def __init__(self):
        self.sent: list[dict] = []

    def verify(self, user: str, password: str) -> bool:
        return bool(user and password and password != "wrong-password")

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
        resolution = resolution or TransportResolution("fake", "fake", "explicit_injection", True)
        if not self.verify(sender, password):
            raise RuntimeError("SMTP authentication failed")
        message = {
            "tracking_message_id": f"fake-{uuid.uuid4().hex}",
            "sender": sender,
            "to": to,
            "subject": subject,
            "body": body,
            "provider_response_classification": "simulated_fake_transport",
        }
        self.sent.append(message)
        return SendOutcome(
            attempt_status="simulated",
            configured_transport=resolution.configured_transport,
            effective_transport=resolution.effective_transport,
            transport_source=resolution.transport_source,
            simulated=True,
            provider_contacted=False,
            provider_accepted=False,
            tracking_message_id=message["tracking_message_id"],
            provider_response_classification=message["provider_response_classification"],
            error_code="simulated_transport",
            error_detail_redacted="Fake transport simulated the send; no provider was contacted",
        )
