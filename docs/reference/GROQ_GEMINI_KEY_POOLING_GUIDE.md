# Groq and Gemini Key Pooling Guide

Date: 2026-05-25

Purpose: this is a reusable implementation guide for adding Groq and Gemini key pooling to a chatbot, web application, or agent backend. It is grounded in the Finimatic implementation in this workspace, but written so it can be copied into a new project.

Important: this file contains code patterns and placeholders only. It does not contain real API keys.

## 0. Read This First

Key pooling is useful for reliability, failover, cooldown handling, and routing across providers. It is not a guaranteed way to multiply free quota.

Facts checked against current provider docs:

- Groq rate limits are measured by request and token dimensions such as RPM, RPD, TPM, and TPD. Groq also states that rate limits apply at the organization level, not individual users. Multiple keys under the same Groq organization may not multiply quota. See [Groq rate limits](https://console.groq.com/docs/rate-limits).
- Groq's Chat Completions API accepts standard `system`, `user`, and `assistant` messages through the Groq SDK. See [Groq text generation](https://console.groq.com/docs/text-chat).
- Gemini API access requires an API key, and Google documents the current Python SDK as `google-genai` with `genai.Client()` and `client.models.generate_content(...)`. See [Gemini quickstart](https://ai.google.dev/gemini-api/docs/quickstart).
- Gemini rate limits are evaluated per project, not per API key. Multiple keys from the same Google project may not multiply quota. See [Gemini rate limits](https://ai.google.dev/gemini-api/docs/rate-limits).

Use only keys you own and are allowed to use. Do not use pooling to evade abuse controls, provider terms, billing controls, or organization policy. A good pool respects 429 responses, cools down the affected key or project, and fails cleanly when all available capacity is exhausted.

## 1. What Finimatic Already Does

Finimatic stores Groq and Gemini keys in the backend settings table, encrypted with Fernet. The frontend never receives raw keys.

Relevant files:

```text
backend/app/ai/key_utils.py
backend/app/core/crypto.py
backend/app/settings/service.py
backend/app/ai/groq_pool.py
backend/app/ai/gemini_pool.py
backend/app/ai/groq_scheduler.py
backend/app/ai/gemini_scheduler.py
backend/app/ai/gateway.py
frontend/src/api/client.ts
frontend/src/features/floating-assistant/assistantApi.ts
backend/tests/test_settings_smtp_canary.py
backend/tests/test_import_policy_ai_followups.py
```

Current Finimatic secret path:

```text
Settings UI
  -> POST /api/settings with groq_keys and gemini_keys textareas
  -> backend parses and dedupes keys
  -> backend encrypts each key with Fernet
  -> settings.value stores encrypted JSON only
  -> GET /api/settings returns counts and sha256[:12] fingerprints only
  -> AI calls load decrypted keys at request time with get_key_list(...)
  -> frontend uses only VITE_API_URL
```

Current Finimatic key parsing:

```python
import re

from app.core.crypto import fingerprint


def parse_keys(raw: str | list[str] | None) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        parts = raw
    else:
        parts = re.split(r"[\s,;]+", raw)
    seen: set[str] = set()
    parsed: list[str] = []
    for part in parts:
        key = str(part).strip()
        if key and key not in seen:
            seen.add(key)
            parsed.append(key)
    return parsed


def fingerprints(keys: list[str]) -> list[str]:
    return [fingerprint(key) for key in keys]
```

Current Finimatic pool utility:

```python
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.ai.key_utils import parse_keys


@dataclass
class KeyState:
    key: str
    last_used_at: datetime | None = None
    cooldown_until: datetime | None = None
    quarantined: bool = False


class GroqKeyPool:
    def __init__(self, keys: list[str] | str | None):
        self.states = [KeyState(key) for key in parse_keys(keys)]

    def active_states(self) -> list[KeyState]:
        now = datetime.now(timezone.utc)
        return [
            state
            for state in self.states
            if not state.quarantined and (state.cooldown_until is None or state.cooldown_until <= now)
        ]

    def acquire(self) -> str | None:
        active = self.active_states()
        if not active:
            return None
        chosen = sorted(active, key=lambda item: item.last_used_at or datetime.min.replace(tzinfo=timezone.utc))[0]
        chosen.last_used_at = datetime.now(timezone.utc)
        return chosen.key

    def record_rate_limit(self, key: str, retry_after_s: int = 60) -> None:
        for state in self.states:
            if state.key == key:
                state.cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=retry_after_s)

    def quarantine(self, key: str) -> None:
        for state in self.states:
            if state.key == key:
                state.quarantined = True

    def exhausted_error_code(self) -> str | None:
        return "model_unavailable_rate_limited" if self.states and not self.active_states() else None
```

Current Finimatic behavior worth copying:

- Parse keys from comma, semicolon, whitespace, or newline separated input.
- Deduplicate keys before storing.
- Encrypt each key before writing to the database.
- Return only key counts and fingerprints to the browser.
- On provider failure, return structured errors like `missing_api_key`, `invalid_api_key`, `model_unavailable_rate_limited`, `timeout`, `transport_error`, or `malformed_output`.
- Keep Groq and Gemini behind backend code. Do not add key values to frontend env vars.
- Run blocking SDK calls with `asyncio.to_thread(...)` or `run_in_executor(...)` from async routes.

## 2. Recommended Portable Architecture

Use this shape for any chatbot or web app:

```text
Browser
  -> sends chat text and selected model only
  -> never sends provider keys

Backend /api/chat
  -> loads encrypted key list from DB
  -> builds request-scoped key pools
  -> chooses provider route
  -> calls Groq or Gemini with retries
  -> cools down 429 keys
  -> quarantines invalid keys
  -> returns answer or safe structured failure

Database
  -> stores encrypted key arrays
  -> stores only safe fingerprints in read APIs
```

If your app is static-only and has no backend, do not commit keys. A static app can only store user-provided keys in browser storage, which is much weaker security. For serious apps, use a backend.

## 3. Portable File Layout

Use this layout for a FastAPI chatbot:

```text
backend/
  requirements.txt
  app/
    main.py
    core/
      crypto.py
    db/
      models.py
      session.py
    settings/
      service.py
    ai/
      key_utils.py
      pool.py
      provider_clients.py
      router.py
frontend/
  src/
    api/assistantClient.ts
```

## 4. Backend Requirements

File: `backend/requirements.txt`

```text
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
sqlalchemy>=2.0.0
pydantic>=2.7.0
pydantic-settings>=2.2.0
cryptography>=42.0.0
groq>=0.9.0
google-genai>=1.0.0
httpx>=0.27.0
pytest>=8.0.0
```

## 5. Secret Encryption

File: `backend/app/core/crypto.py`

```python
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from cryptography.fernet import Fernet


def fingerprint(value: str | None) -> str:
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_dotenv_key() -> str | None:
    env_path = _backend_root() / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("FERNET_KEY="):
            return line.split("=", 1)[1].strip()
    return None


def get_fernet_key() -> str:
    key = os.getenv("FERNET_KEY") or _read_dotenv_key()
    if key:
        return key

    generated = Fernet.generate_key().decode("utf-8")
    env_path = _backend_root() / ".env"
    env_path.write_text(f"FERNET_KEY={generated}\n", encoding="utf-8")
    os.environ["FERNET_KEY"] = generated
    return generated


def _fernet() -> Fernet:
    return Fernet(get_fernet_key().encode("utf-8"))


def encrypt_secret(value: str | None) -> str:
    if value is None or value == "":
        return ""
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str | None) -> str:
    if not value:
        return ""
    return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")


def redacted_error(exc: BaseException) -> str:
    text = str(exc) or exc.__class__.__name__
    for token in ("password", "secret", "token", "key"):
        text = text.replace(token, "<redacted>")
    return text[:300]
```

Notes:

- `.env` must be ignored by git.
- `FERNET_KEY` is the only secret that can be stored as an environment variable in this simple pattern.
- Provider keys are stored in the database after encryption.

## 6. Key Parsing and Fingerprints

File: `backend/app/ai/key_utils.py`

```python
from __future__ import annotations

import re

from app.core.crypto import fingerprint


def parse_keys(raw: str | list[str] | None) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        parts = raw
    else:
        parts = re.split(r"[\s,;]+", raw)

    seen: set[str] = set()
    parsed: list[str] = []
    for part in parts:
        key = str(part).strip()
        if key and key not in seen:
            seen.add(key)
            parsed.append(key)
    return parsed


def fingerprints(keys: list[str]) -> list[str]:
    return [fingerprint(key) for key in keys]
```

Accepted input examples:

```text
GROQ_KEY_1
GROQ_KEY_2

GROQ_KEY_1,GROQ_KEY_2

GROQ_KEY_1; GROQ_KEY_2
```

Do not use fake examples that look like real provider keys in docs, tests, screenshots, or logs.

## 7. Minimal Database Models

File: `backend/app/db/models.py`

```python
from __future__ import annotations

import uuid

from sqlalchemy import Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Setting(Base):
    __tablename__ = "settings"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    value: Mapped[str | None] = mapped_column(Text)
```

File: `backend/app/db/session.py`

```python
from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_db():
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

## 8. Settings Service

File: `backend/app/settings/service.py`

```python
from __future__ import annotations

import json
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.ai.key_utils import fingerprints, parse_keys
from app.core.crypto import decrypt_secret, encrypt_secret
from app.db.models import Setting

Provider = Literal["groq", "gemini"]

DEFAULT_SETTINGS: dict[str, str] = {
    "groq_keys": "[]",
    "gemini_keys": "[]",
    "groq_model": "llama-3.3-70b-versatile",
    "gemini_model": "gemini-2.5-flash",
}

SECRET_KEYS = {"groq_keys", "gemini_keys"}


def seed_settings(db: Session) -> None:
    changed = False
    for key, value in DEFAULT_SETTINGS.items():
        if not db.query(Setting).filter_by(key=key).first():
            db.add(Setting(key=key, value=value))
            changed = True
    if changed:
        db.commit()


def _setting(db: Session, key: str) -> Setting:
    row = db.query(Setting).filter_by(key=key).first()
    if row is None:
        row = Setting(key=key, value=DEFAULT_SETTINGS.get(key, ""))
        db.add(row)
        db.flush()
    return row


def get_value(db: Session, key: str, default: str = "") -> str:
    row = db.query(Setting).filter_by(key=key).first()
    if row is None or row.value is None:
        return default
    return row.value


def set_value(db: Session, key: str, value: str) -> None:
    row = _setting(db, key)
    row.value = value


def _provider_key_name(provider: Provider) -> str:
    return f"{provider}_keys"


def set_provider_keys(db: Session, provider: Provider, raw: str | list[str] | None) -> None:
    key_name = _provider_key_name(provider)
    keys = parse_keys(raw)
    encrypted = [encrypt_secret(key) for key in keys]
    set_value(db, key_name, json.dumps(encrypted))


def get_key_list(db: Session, key_name: str) -> list[str]:
    stored = get_value(db, key_name, "[]")
    try:
        encrypted = json.loads(stored)
    except json.JSONDecodeError:
        return []
    return [decrypt_secret(item) for item in encrypted if item]


def update_settings(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    seed_settings(db)

    if "groq_keys" in payload and payload["groq_keys"] is not None:
        set_provider_keys(db, "groq", payload["groq_keys"])

    if "gemini_keys" in payload and payload["gemini_keys"] is not None:
        set_provider_keys(db, "gemini", payload["gemini_keys"])

    if "groq_model" in payload and payload["groq_model"]:
        set_value(db, "groq_model", str(payload["groq_model"]))

    if "gemini_model" in payload and payload["gemini_model"]:
        set_value(db, "gemini_model", str(payload["gemini_model"]))

    db.commit()
    return settings_read(db)


def settings_read(db: Session) -> dict[str, Any]:
    seed_settings(db)
    groq = get_key_list(db, "groq_keys")
    gemini = get_key_list(db, "gemini_keys")
    return {
        "groq_keys_count": len(groq),
        "groq_keys_fingerprints": fingerprints(groq),
        "gemini_keys_count": len(gemini),
        "gemini_keys_fingerprints": fingerprints(gemini),
        "groq_model": get_value(db, "groq_model", DEFAULT_SETTINGS["groq_model"]),
        "gemini_model": get_value(db, "gemini_model", DEFAULT_SETTINGS["gemini_model"]),
    }
```

Do not create `get_groq_keys_decrypted()` and `get_gemini_keys_decrypted()` unless your project already uses that naming style. A generic `get_key_list(db, "groq_keys")` and `get_key_list(db, "gemini_keys")` is simpler.

## 9. Generic Key Pool

File: `backend/app/ai/pool.py`

```python
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal

from app.ai.key_utils import parse_keys
from app.core.crypto import fingerprint

Provider = Literal["groq", "gemini"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _status_code(exc: BaseException) -> int | None:
    value = getattr(exc, "status_code", None)
    if isinstance(value, int):
        return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    if isinstance(value, int):
        return value
    match = re.search(r"\b(400|401|403|408|409|429|500|502|503|504)\b", str(exc))
    return int(match.group(1)) if match else None


def _headers(exc: BaseException) -> dict[str, str]:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or getattr(exc, "headers", None) or {}
    try:
        return {str(k).lower(): str(v) for k, v in dict(headers).items()}
    except Exception:
        return {}


def retry_after_seconds(exc: BaseException, default: int = 60) -> int:
    headers = _headers(exc)
    retry_after = headers.get("retry-after")
    if retry_after:
        try:
            return max(1, min(3600, int(float(retry_after))))
        except ValueError:
            pass

    text = str(exc)
    match = re.search(r"retry\s*(?:in|after)?\s*([0-9]+(?:\.[0-9]+)?)\s*s", text, re.IGNORECASE)
    if match:
        return max(1, min(3600, int(float(match.group(1)))))

    match = re.search(r"retryDelay['\"]?\s*:\s*['\"]?([0-9]+)s", text, re.IGNORECASE)
    if match:
        return max(1, min(3600, int(match.group(1))))

    return default


def is_rate_limit(exc: BaseException) -> bool:
    text = str(exc).lower()
    return _status_code(exc) == 429 or "resource_exhausted" in text or "rate limit" in text or "too many requests" in text


def is_auth_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return _status_code(exc) in {401, 403} or "invalid api key" in text or "permission denied" in text


def is_transient_provider_error(exc: BaseException) -> bool:
    return _status_code(exc) in {408, 409, 500, 502, 503, 504}


@dataclass
class KeyState:
    key: str
    provider: Provider
    last_used_at: datetime | None = None
    cooldown_until: datetime | None = None
    quarantined: bool = False
    consecutive_failures: int = 0
    last_error_code: str | None = None
    last_error_at: datetime | None = None

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.key)

    def active(self) -> bool:
        now = utcnow()
        return not self.quarantined and (self.cooldown_until is None or self.cooldown_until <= now)


@dataclass
class AttemptRecord:
    provider: Provider
    key_fingerprint: str
    outcome: str
    error_code: str | None = None


@dataclass
class KeyPool:
    provider: Provider
    raw_keys: list[str] | str | None
    states: list[KeyState] = field(init=False)

    def __post_init__(self) -> None:
        self.states = [KeyState(key=key, provider=self.provider) for key in parse_keys(self.raw_keys)]

    def active_states(self) -> list[KeyState]:
        return [state for state in self.states if state.active()]

    def acquire(self) -> KeyState | None:
        active = self.active_states()
        if not active:
            return None
        oldest = datetime.min.replace(tzinfo=timezone.utc)
        chosen = sorted(active, key=lambda state: state.last_used_at or oldest)[0]
        chosen.last_used_at = utcnow()
        return chosen

    def record_success(self, state: KeyState) -> AttemptRecord:
        state.consecutive_failures = 0
        state.last_error_code = None
        state.last_error_at = None
        return AttemptRecord(self.provider, state.fingerprint, "success")

    def record_rate_limit(self, state: KeyState, retry_after_s: int) -> AttemptRecord:
        state.cooldown_until = utcnow() + timedelta(seconds=max(1, retry_after_s))
        state.consecutive_failures += 1
        state.last_error_code = "rate_limited"
        state.last_error_at = utcnow()
        return AttemptRecord(self.provider, state.fingerprint, "rate_limited", "rate_limited")

    def quarantine(self, state: KeyState, error_code: str = "invalid_api_key") -> AttemptRecord:
        state.quarantined = True
        state.consecutive_failures += 1
        state.last_error_code = error_code
        state.last_error_at = utcnow()
        return AttemptRecord(self.provider, state.fingerprint, "quarantined", error_code)

    def record_transient_error(self, state: KeyState, error_code: str = "transport_error", cooldown_s: int = 10) -> AttemptRecord:
        state.cooldown_until = utcnow() + timedelta(seconds=max(1, cooldown_s))
        state.consecutive_failures += 1
        state.last_error_code = error_code
        state.last_error_at = utcnow()
        return AttemptRecord(self.provider, state.fingerprint, "transient_error", error_code)

    def exhausted_error_code(self) -> str:
        if not self.states:
            return "missing_api_key"
        if not self.active_states():
            return "model_unavailable_rate_limited"
        return "provider_budget_exhausted"

    def safe_snapshot(self) -> list[dict[str, object]]:
        now = utcnow()
        return [
            {
                "fingerprint": state.fingerprint,
                "active": state.active(),
                "quarantined": state.quarantined,
                "cooldown_seconds_remaining": (
                    max(0, int((state.cooldown_until - now).total_seconds()))
                    if state.cooldown_until
                    else 0
                ),
                "last_error_code": state.last_error_code,
            }
            for state in self.states
        ]
```

Why request-scoped pools are enough for a first version:

- They rotate across keys inside one request.
- They avoid storing raw provider keys in a global memory cache.
- They are simple to test.

When to add a longer-lived pool registry:

- You want cooldown state to survive across requests.
- You are comfortable keeping raw keys in process memory.
- You have a single backend process or a shared cache such as Redis.

For a beginner implementation, start request-scoped. Add Redis or a DB-backed health table only after the simple version works.

## 10. Provider Client Code

File: `backend/app/ai/provider_clients.py`

```python
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Literal

from app.ai.pool import (
    AttemptRecord,
    KeyPool,
    is_auth_error,
    is_rate_limit,
    is_transient_provider_error,
    retry_after_seconds,
)

Provider = Literal["groq", "gemini"]
Message = dict[str, str]


@dataclass
class ProviderResult:
    ok: bool
    provider: Provider
    model: str
    text: str = ""
    error_code: str | None = None
    attempts: list[AttemptRecord] = field(default_factory=list)


def _extract_system_and_contents(messages: list[Message]) -> tuple[str, str]:
    system_parts: list[str] = []
    content_parts: list[str] = []
    for message in messages:
        role = message.get("role", "user")
        content = str(message.get("content", ""))
        if role == "system":
            system_parts.append(content)
        else:
            content_parts.append(f"{role.upper()}: {content}")
    return "\n\n".join(system_parts).strip(), "\n\n".join(content_parts).strip()


async def call_groq_with_pool(
    keys: list[str],
    messages: list[Message],
    *,
    model: str = "llama-3.3-70b-versatile",
    temperature: float = 0.3,
    max_tokens: int = 1024,
    json_mode: bool = False,
) -> ProviderResult:
    pool = KeyPool("groq", keys)
    attempts: list[AttemptRecord] = []

    while True:
        state = pool.acquire()
        if state is None:
            return ProviderResult(False, "groq", model, error_code=pool.exhausted_error_code(), attempts=attempts)

        def _call() -> str:
            from groq import Groq

            client = Groq(api_key=state.key)
            kwargs = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            response = client.chat.completions.create(**kwargs)
            return response.choices[0].message.content or ""

        try:
            text = await asyncio.to_thread(_call)
            attempts.append(pool.record_success(state))
            return ProviderResult(True, "groq", model, text=text, attempts=attempts)
        except Exception as exc:
            if is_rate_limit(exc):
                attempts.append(pool.record_rate_limit(state, retry_after_seconds(exc)))
                continue
            if is_auth_error(exc):
                attempts.append(pool.quarantine(state, "invalid_api_key"))
                continue
            if is_transient_provider_error(exc):
                attempts.append(pool.record_transient_error(state, "transport_error", cooldown_s=10))
                continue
            attempts.append(pool.record_transient_error(state, "transport_error", cooldown_s=10))
            return ProviderResult(False, "groq", model, error_code="transport_error", attempts=attempts)


async def call_gemini_with_pool(
    keys: list[str],
    messages: list[Message],
    *,
    model: str = "gemini-2.5-flash",
    temperature: float = 0.3,
    max_tokens: int = 1024,
    json_mode: bool = False,
) -> ProviderResult:
    pool = KeyPool("gemini", keys)
    attempts: list[AttemptRecord] = []
    system_instruction, contents = _extract_system_and_contents(messages)

    while True:
        state = pool.acquire()
        if state is None:
            return ProviderResult(False, "gemini", model, error_code=pool.exhausted_error_code(), attempts=attempts)

        def _call() -> str:
            from google import genai
            from google.genai import types

            config_kwargs = {
                "temperature": temperature,
                "max_output_tokens": max_tokens,
            }
            if system_instruction:
                config_kwargs["system_instruction"] = system_instruction
            if json_mode:
                config_kwargs["response_mime_type"] = "application/json"

            client = genai.Client(api_key=state.key)
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(**config_kwargs),
                )
                return response.text or ""
            finally:
                close = getattr(client, "close", None)
                if close:
                    close()

        try:
            text = await asyncio.to_thread(_call)
            attempts.append(pool.record_success(state))
            return ProviderResult(True, "gemini", model, text=text, attempts=attempts)
        except Exception as exc:
            if is_rate_limit(exc):
                attempts.append(pool.record_rate_limit(state, retry_after_seconds(exc)))
                continue
            if is_auth_error(exc):
                attempts.append(pool.quarantine(state, "invalid_api_key"))
                continue
            if is_transient_provider_error(exc):
                attempts.append(pool.record_transient_error(state, "transport_error", cooldown_s=10))
                continue
            attempts.append(pool.record_transient_error(state, "transport_error", cooldown_s=10))
            return ProviderResult(False, "gemini", model, error_code="transport_error", attempts=attempts)
```

Important implementation details:

- The result includes fingerprints only through `AttemptRecord`, never raw keys.
- A 429 cools down only the key that received the error.
- A 401 or 403 quarantines that key.
- 5xx and timeout-style errors are treated as transient.
- If all keys are cooled down, the call returns `model_unavailable_rate_limited`.

## 11. Provider Router

File: `backend/app/ai/router.py`

```python
from __future__ import annotations

from typing import Literal

from sqlalchemy.orm import Session

from app.ai.provider_clients import Message, ProviderResult, call_gemini_with_pool, call_groq_with_pool
from app.settings.service import get_key_list, get_value

ProviderChoice = Literal["auto", "groq", "gemini"]

GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"
GEMINI_DEFAULT_MODEL = "gemini-2.5-flash"
GROQ_CONTEXT_SOFT_LIMIT_CHARS = 90000


def _message_chars(messages: list[Message]) -> int:
    return sum(len(str(message.get("content", ""))) for message in messages)


def _provider_order(choice: ProviderChoice, messages: list[Message], has_groq: bool, has_gemini: bool) -> list[str]:
    if choice == "groq":
        return ["groq", "gemini"]
    if choice == "gemini":
        return ["gemini", "groq"]

    if _message_chars(messages) > GROQ_CONTEXT_SOFT_LIMIT_CHARS and has_gemini:
        return ["gemini", "groq"]

    if has_groq:
        return ["groq", "gemini"]
    return ["gemini", "groq"]


async def chat_with_provider_pool(
    db: Session,
    messages: list[Message],
    *,
    provider: ProviderChoice = "auto",
    temperature: float = 0.3,
    max_tokens: int = 1024,
    json_mode: bool = False,
) -> ProviderResult:
    groq_keys = get_key_list(db, "groq_keys")
    gemini_keys = get_key_list(db, "gemini_keys")
    groq_model = get_value(db, "groq_model", GROQ_DEFAULT_MODEL)
    gemini_model = get_value(db, "gemini_model", GEMINI_DEFAULT_MODEL)

    order = _provider_order(provider, messages, bool(groq_keys), bool(gemini_keys))
    failures: list[ProviderResult] = []

    for selected in order:
        if selected == "groq" and groq_keys:
            result = await call_groq_with_pool(
                groq_keys,
                messages,
                model=groq_model,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
            )
            if result.ok:
                return result
            failures.append(result)

        if selected == "gemini" and gemini_keys:
            result = await call_gemini_with_pool(
                gemini_keys,
                messages,
                model=gemini_model,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
            )
            if result.ok:
                return result
            failures.append(result)

    if failures:
        last = failures[-1]
        return ProviderResult(
            ok=False,
            provider=last.provider,
            model=last.model,
            error_code=last.error_code or "provider_unavailable",
            attempts=[attempt for failure in failures for attempt in failure.attempts],
        )

    return ProviderResult(ok=False, provider="groq", model=groq_model, error_code="missing_api_key")
```

Provider routing policy:

- `auto`: Groq first for normal short text, Gemini first for very large context if Gemini keys exist.
- `groq`: try Groq first, then Gemini fallback.
- `gemini`: try Gemini first, then Groq fallback.

Adjust the order for your product. For example, an image/PDF chatbot may choose Gemini first when attachments are present.

## 12. FastAPI Endpoints

File: `backend/app/main.py`

```python
from __future__ import annotations

from typing import Literal

from fastapi import Depends, FastAPI
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.ai.router import chat_with_provider_pool
from app.db.session import get_db, init_db
from app.settings.service import settings_read, update_settings

app = FastAPI(title="Key Pooling Chatbot")


class SettingsWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    groq_keys: str | None = None
    gemini_keys: str | None = None
    groq_model: str | None = None
    gemini_model: str | None = None


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1)
    provider: Literal["auto", "groq", "gemini"] = "auto"
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/api/settings")
def read_settings(db: Session = Depends(get_db)):
    return settings_read(db)


@app.post("/api/settings")
def write_settings(payload: SettingsWrite, db: Session = Depends(get_db)):
    return update_settings(db, payload.model_dump(exclude_unset=True))


@app.post("/api/chat")
async def chat(payload: ChatRequest, db: Session = Depends(get_db)):
    messages = [item.model_dump() for item in payload.history[-10:]]
    messages.append({"role": "user", "content": payload.message})

    result = await chat_with_provider_pool(
        db,
        messages,
        provider=payload.provider,
        temperature=0.3,
        max_tokens=1024,
    )

    return {
        "ok": result.ok,
        "provider": result.provider,
        "model": result.model,
        "response": result.text if result.ok else "The model provider is unavailable. Please retry later or switch providers.",
        "error_code": result.error_code,
        "attempts": [
            {
                "provider": attempt.provider,
                "key_fingerprint": attempt.key_fingerprint,
                "outcome": attempt.outcome,
                "error_code": attempt.error_code,
            }
            for attempt in result.attempts
        ],
    }
```

Production additions you should add later:

- Authentication.
- Per-user ownership checks.
- Request body size limits.
- Audit events.
- Structured logs with redaction.
- Database migrations instead of `create_all`.
- A provider health table if you need cross-request cooldown state.

## 13. Frontend API Client

File: `frontend/src/api/assistantClient.ts`

```typescript
const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export type ProviderChoice = "auto" | "groq" | "gemini";

export type ChatResponse = {
  ok: boolean;
  provider: "groq" | "gemini";
  model: string;
  response: string;
  error_code?: string | null;
  attempts: Array<{
    provider: "groq" | "gemini";
    key_fingerprint: string;
    outcome: string;
    error_code?: string | null;
  }>;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    }
  });

  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new Error(data?.detail || data?.error_code || `request_failed_${response.status}`);
  }
  return data as T;
}

export const assistantClient = {
  getSettings: () => request("/api/settings"),
  saveKeys: (payload: { groq_keys?: string; gemini_keys?: string; groq_model?: string; gemini_model?: string }) =>
    request("/api/settings", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  chat: (message: string, provider: ProviderChoice = "auto") =>
    request<ChatResponse>("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message, provider })
    })
};
```

Frontend rule:

```text
VITE_API_URL is allowed.
VITE_GROQ_API_KEY is not allowed.
VITE_GEMINI_API_KEY is not allowed.
NEXT_PUBLIC_GROQ_API_KEY is not allowed.
NEXT_PUBLIC_GEMINI_API_KEY is not allowed.
```

The browser can show:

- `groq_keys_count`
- `groq_keys_fingerprints`
- `gemini_keys_count`
- `gemini_keys_fingerprints`

The browser must not show:

- raw provider keys
- Fernet encrypted values
- provider request headers
- provider SDK exceptions that include secrets

## 14. Minimal React Usage

```tsx
import { useState } from "react";
import { assistantClient, ProviderChoice } from "./api/assistantClient";

export function ChatBox() {
  const [message, setMessage] = useState("");
  const [provider, setProvider] = useState<ProviderChoice>("auto");
  const [reply, setReply] = useState("");
  const [loading, setLoading] = useState(false);

  async function send() {
    if (!message.trim()) return;
    setLoading(true);
    try {
      const result = await assistantClient.chat(message, provider);
      setReply(result.response);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section>
      <select value={provider} onChange={(event) => setProvider(event.target.value as ProviderChoice)}>
        <option value="auto">Auto</option>
        <option value="groq">Groq</option>
        <option value="gemini">Gemini</option>
      </select>
      <textarea value={message} onChange={(event) => setMessage(event.target.value)} />
      <button type="button" onClick={send} disabled={loading}>
        Send
      </button>
      <pre>{reply}</pre>
    </section>
  );
}
```

This component sends only the user message and provider choice. It does not handle keys.

## 15. Required Tests

File: `backend/tests/test_key_pooling.py`

```python
from __future__ import annotations

import sys
import types

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.ai.key_utils import parse_keys
from app.ai.provider_clients import call_groq_with_pool
from app.core.crypto import fingerprint
from app.db.models import Base, Setting
from app.db.session import engine
from app.main import app


@pytest.fixture(autouse=True)
def clean_db(monkeypatch):
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def test_parse_keys_separators_and_dedup():
    assert parse_keys("a,b; c\n a") == ["a", "b", "c"]


def test_settings_encrypts_and_returns_fingerprints_only():
    client = TestClient(app)
    key = "groq-test-one"
    body = client.post("/api/settings", json={"groq_keys": f"{key}\ngroq-test-two"}).json()

    assert key not in str(body)
    assert body["groq_keys_count"] == 2
    assert body["groq_keys_fingerprints"][0] == fingerprint(key)

    get_body = client.get("/api/settings").json()
    assert key not in str(get_body)


def test_raw_key_not_stored_plaintext():
    client = TestClient(app)
    key = "groq-test-one"
    client.post("/api/settings", json={"groq_keys": key})

    from app.db.session import SessionLocal

    with SessionLocal() as db:
        stored = db.query(Setting).filter_by(key="groq_keys").one().value
    assert key not in stored


@pytest.mark.asyncio
async def test_groq_rotates_after_rate_limit(monkeypatch):
    calls = []

    class FakeCompletions:
        def __init__(self, key):
            self.key = key

        def create(self, **_kwargs):
            calls.append(self.key)
            if self.key == "key-one":
                raise RuntimeError("429 rate limit retry-after: 1")
            return types.SimpleNamespace(
                choices=[
                    types.SimpleNamespace(
                        message=types.SimpleNamespace(content="Recovered")
                    )
                ]
            )

    class FakeGroq:
        def __init__(self, api_key):
            self.chat = types.SimpleNamespace(completions=FakeCompletions(api_key))

    monkeypatch.setitem(sys.modules, "groq", types.SimpleNamespace(Groq=FakeGroq))

    result = await call_groq_with_pool(
        ["key-one", "key-two"],
        [{"role": "user", "content": "hello"}],
        model="llama-3.3-70b-versatile",
    )

    assert result.ok is True
    assert result.text == "Recovered"
    assert calls == ["key-one", "key-two"]
    assert "key-one" not in str(result)
    assert "key-two" not in str(result)
```

Additional tests to add:

```text
test_groq_all_keys_rate_limited_returns_model_unavailable
test_groq_invalid_key_quarantines_and_tries_next
test_gemini_resource_exhausted_rotates_to_next_key
test_auto_provider_falls_back_to_gemini_when_groq_exhausted
test_auto_provider_falls_back_to_groq_when_gemini_exhausted
test_no_raw_provider_key_in_chat_response
test_no_raw_provider_key_in_error_response
test_frontend_env_uses_only_vite_api_url
```

## 16. Production Hardening

Add these when the basic implementation works:

- Audit event table: record provider, model, fingerprint, outcome, latency, and error code.
- Provider health table: store per-fingerprint cooldown and last error without storing raw keys.
- Redis lock or queue: limit concurrent calls per provider.
- Per-user rate limits: avoid one user burning all pooled capacity.
- Timeout budget: set hard request deadlines.
- Circuit breaker: stop trying a provider after repeated failures.
- Prompt/token accounting: estimate token use before sending huge context.
- Admin UI: show counts, fingerprints, and health, never raw keys.

## 17. Common Mistakes

Avoid these:

- Putting Groq or Gemini keys in frontend `.env` files.
- Returning raw keys from `/api/settings`.
- Logging provider exceptions without redaction.
- Treating 429 as a normal crash instead of cooling down and trying the next key.
- Retrying the same rate-limited key immediately in a tight loop.
- Assuming multiple keys from the same project or organization multiply quota.
- Failing open when no keys are configured.
- Letting the model decide which key to use.
- Asking the model to handle provider errors.

## 18. Small Model Implementation Contract

Give this exact contract to a small coding model:

```text
Goal:
Implement backend-only Groq and Gemini key pooling for a chatbot.

Hard rules:
1. Do not expose raw keys to the browser.
2. Store provider keys encrypted at rest.
3. GET /api/settings returns only key counts and sha256[:12] fingerprints.
4. POST /api/chat accepts user message and provider choice only.
5. Provider choices are auto, groq, and gemini.
6. auto tries Groq first for normal text and Gemini first for very large context.
7. On 429 or RESOURCE_EXHAUSTED, cool down the affected key and try the next key.
8. On 401 or 403, quarantine that key and try the next key.
9. If all keys fail, return a structured safe error. Do not crash.
10. Never log raw provider keys.
11. Frontend may use VITE_API_URL only.

Files to create:
- backend/app/core/crypto.py
- backend/app/ai/key_utils.py
- backend/app/db/models.py
- backend/app/db/session.py
- backend/app/settings/service.py
- backend/app/ai/pool.py
- backend/app/ai/provider_clients.py
- backend/app/ai/router.py
- backend/app/main.py
- frontend/src/api/assistantClient.ts
- backend/tests/test_key_pooling.py

Success criteria:
- Key parsing handles newline, comma, semicolon, and whitespace.
- Duplicate keys are removed.
- Stored database values do not contain plaintext keys.
- Settings API responses do not contain plaintext keys.
- A Groq 429 on key 1 causes key 2 to be tried.
- A Gemini RESOURCE_EXHAUSTED on key 1 causes key 2 to be tried.
- Chat response includes provider, model, response text, error_code, and safe attempts.
- Attempts include fingerprints only, not keys.
- Tests pass.
```

## 19. Final Copy-Paste Checklist

Before calling this complete:

```text
PASS/FAIL - backend starts.
PASS/FAIL - POST /api/settings stores Groq keys encrypted.
PASS/FAIL - POST /api/settings stores Gemini keys encrypted.
PASS/FAIL - GET /api/settings returns only counts and fingerprints.
PASS/FAIL - POST /api/chat provider=groq uses Groq.
PASS/FAIL - POST /api/chat provider=gemini uses Gemini.
PASS/FAIL - POST /api/chat provider=auto falls back when primary provider fails.
PASS/FAIL - 429 cools down only the affected key.
PASS/FAIL - invalid key is quarantined.
PASS/FAIL - browser network responses contain no raw provider keys.
PASS/FAIL - logs contain no raw provider keys.
PASS/FAIL - frontend env contains only VITE_API_URL.
```

## 20. Finimatic-Specific Notes

For this Finimatic workspace:

- `backend/app/settings/service.py` is the source of truth for encrypted key storage.
- `get_key_list(db, "groq_keys")` and `get_key_list(db, "gemini_keys")` are the correct decryption helpers.
- `backend/app/ai/groq_pool.py` and `backend/app/ai/gemini_pool.py` already provide the simple pool shape.
- `backend/app/ai/gateway.py` already rotates through configured keys for draft generation after rate-limit style errors.
- `frontend/src/api/client.ts` and `frontend/src/features/floating-assistant/assistantApi.ts` correctly use `VITE_API_URL` and do not need provider keys.
- Do not read or copy values from `KEYS.md` into this guide, code, logs, tests, screenshots, or prompts.

The reusable pattern is:

```text
Store encrypted keys -> expose fingerprints only -> build request-scoped pools -> route provider -> handle 429 per key -> fallback provider -> return safe response.
```
