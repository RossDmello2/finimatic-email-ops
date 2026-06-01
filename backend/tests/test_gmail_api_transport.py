from app.send import smtp_adapter
from app.send.smtp_adapter import GmailApiTransport, GmailAdapter


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeHttpClient:
    calls = []

    def __init__(self, timeout):
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if url.endswith("/token"):
            return FakeResponse({"access_token": "access-token"})
        if url.endswith("/messages/send"):
            assert "Authorization" in kwargs["headers"]
            assert kwargs["json"]["raw"]
            return FakeResponse({"id": "gmail-message-id"})
        raise AssertionError(f"unexpected POST {url}")

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        assert "Authorization" in kwargs["headers"]
        return FakeResponse({"emailAddress": "sender@example.com"})


def test_gmail_api_transport_verifies_and_sends_over_https(monkeypatch):
    FakeHttpClient.calls = []
    monkeypatch.setattr(smtp_adapter.httpx, "Client", FakeHttpClient)
    transport = GmailApiTransport(
        client_id="client-id",
        client_secret="client-secret",
        refresh_token="refresh-token",
    )

    assert transport.verify("sender@example.com", "") is True
    result = transport.send(
        sender="sender@example.com",
        password="",
        to="recipient@example.com",
        subject="Hello",
        body="Body",
    )

    assert result["message_id"] == "gmail-message-id"
    assert result["smtp_response"] == "gmail_api:sent"
    urls = [call[1] for call in FakeHttpClient.calls]
    assert "https://oauth2.googleapis.com/token" in urls
    assert "https://gmail.googleapis.com/gmail/v1/users/me/messages/send" in urls


def test_gmail_adapter_reports_provider_verified_for_gmail_api(monkeypatch):
    FakeHttpClient.calls = []
    monkeypatch.setattr(smtp_adapter.httpx, "Client", FakeHttpClient)
    adapter = GmailAdapter(
        transport=GmailApiTransport(
            client_id="client-id",
            client_secret="client-secret",
            refresh_token="refresh-token",
        )
    )

    import asyncio

    readiness = asyncio.run(adapter.verify("sender@example.com", ""))
    assert readiness == "provider_verified"


def test_gmail_api_transport_rejects_oauth_sender_mismatch(monkeypatch):
    FakeHttpClient.calls = []
    monkeypatch.setattr(smtp_adapter.httpx, "Client", FakeHttpClient)
    transport = GmailApiTransport(
        client_id="client-id",
        client_secret="client-secret",
        refresh_token="refresh-token",
    )

    import pytest

    with pytest.raises(ValueError, match="OAuth account does not match configured sender"):
        transport.verify("other@example.com", "")
