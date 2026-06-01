# Finimatic — AI Integration

Groq and Gemini are optional accelerators. Every workflow must work manually
without them. AI is suggestion-only; application Python code owns all
execution, approval, and policy decisions.

---

## Key Storage & Retrieval

Keys are entered by the operator via the Settings UI, never via .env.

```
Operator → Settings UI:
  Groq Keys textarea  (one per line OR comma-separated)
  Gemini Keys textarea (one per line OR comma-separated)
    ↓
POST /api/settings  { groq_keys: "key1\nkey2", gemini_keys: "key1\nkey2" }
    ↓
Settings Service:
  groq_keys  → parse_keys(raw) → dedupe → Fernet-encrypt each key → store as JSON in settings.value
  gemini_keys → same
    ↓
AI Gateway (at request time):
  → decrypt keys from settings DB
  → parse_groq_keys(decrypted_value)   # from groq_only_resilience_reference.py
  → dedupe in memory (never re-store decrypted)
  → hand to Pool
```

**API contract**: `GET /api/settings` returns:
```json
{
  "groq_keys_count": 3,
  "groq_keys_fingerprints": ["a1b2c3d4e5f6", "..."],
  "gemini_keys_count": 2,
  "gemini_keys_fingerprints": ["..."]
}
```
Raw keys are NEVER returned. Fingerprints are sha256[:12] of each key.

---

## Groq Pool Contract

Follow `GROQ_ONLY_KEY_POOL_AND_SCHEDULER.md` and
`reference_python/groq_only_resilience_reference.py` exactly.

**Implementation files**:
- `backend/app/ai/groq_pool.py`    → GroqKeyPool (key state, cooldown, LRU selection)
- `backend/app/ai/groq_scheduler.py` → GroqAdmissionGovernor (concurrency, queue, deadline)

**Key lifecycle**:
```
Pool loads N keys from settings (decrypted at request time, not cached in memory long-term)
  ↓
LRU selection among active keys
  ↓
HTTP 429 received:
  - parse retry-after header → cool down ONLY that key
  - try next active key if budget allows
  - all keys cooling → structured failure: model_unavailable_rate_limited
  ↓
HTTP 401/403 → quarantine key as invalid
HTTP 5xx → mark error, retryable=true if budget
Success → record last_success_at, update remaining_requests/tokens hints
```

**Structured failure codes** (used in API responses and audit events):
```
model_unavailable_rate_limited
provider_budget_exhausted
missing_api_key
invalid_api_key
timeout
malformed_output
transport_error
```

**Configuration keys** (stored in settings table or env):
```
GROQ_MODEL_FAST          default: llama-3.3-70b-versatile
GROQ_TIMEOUT_S           default: 30
GROQ_MAX_RETRIES         default: 2
GROQ_RATE_LIMIT_MAX_RETRIES  default: 3
GROQ_MAX_CONCURRENT_CALLS    default: 3
GROQ_QUEUE_SIZE              default: 10
GROQ_QUEUE_DEADLINE_S        default: 45
```

---

## Gemini Pool Contract

Identical pattern to Groq pool, adapted for google-generativeai SDK.

**Implementation files**:
- `backend/app/ai/gemini_pool.py`
- `backend/app/ai/gemini_scheduler.py`

**Quota error mapping**:
```
google.api_core.exceptions.ResourceExhausted (429-equivalent) → cooldown key
google.api_core.exceptions.InvalidArgument                    → quarantine key
google.api_core.exceptions.ServiceUnavailable                 → retryable error
```

**Configuration**:
```
GEMINI_MODEL_DEFAULT    default: gemini-1.5-flash
GEMINI_TIMEOUT_S        default: 30
GEMINI_MAX_RETRIES      default: 2
GEMINI_MAX_CONCURRENT   default: 3
GEMINI_QUEUE_SIZE       default: 10
GEMINI_QUEUE_DEADLINE_S default: 45
```

---

## AI Gateway

`backend/app/ai/gateway.py` — single entry point for all AI calls.

```python
class AIGateway:
    async def generate_draft(
        self,
        contact: Contact,
        provider: Literal["groq", "gemini", "auto"],
        tone: str = "professional",
        length: str = "medium",
    ) -> DraftSuggestion | AIFailure:
        """
        Returns DraftSuggestion on success.
        Returns AIFailure (never raises) on any provider error.
        Caller must handle AIFailure gracefully (empty draft, show error).
        """

    async def rewrite_draft(self, draft: Draft, instruction: str, provider: str) -> DraftSuggestion | AIFailure:
        ...

    async def flag_risks(self, draft: Draft) -> list[str]:
        """Returns list of warning strings. Empty list on failure."""
        ...
```

**Auto provider selection**:
```
provider="auto":
  1. Try Groq (faster, cheaper)
  2. On failure → try Gemini
  3. On failure → return AIFailure
```

---

## AI Prompt Structure (for draft generation)

**System prompt** (never logged):
```
You are an email copywriter for cold outreach. Write personalized, concise,
honest outreach emails. Never use deceptive subject lines. Never claim a
relationship that doesn't exist. Return ONLY valid JSON matching the schema.
```

**User prompt** (contact evidence injected):
```
Write a cold outreach email to:
- Name: {creator_name or business_name}
- Website: {website_url or "unknown"}
- Evidence: {personalization field}
- Tone: {tone}
- Length: {length}

Return JSON: {"subject": "...", "body": "...", "warnings": ["..."]}
```

**Output schema** (pydantic, validated before storage):
```python
class DraftSuggestion(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=10, max_length=5000)
    warnings: list[str] = Field(default_factory=list, max_length=10)
```

Malformed JSON, schema violation, or empty response → `AIFailure`, emit
`audit_event(draft.ai_failed, {provider, error_code, fingerprint})`, return
empty draft suggestion with `error_code` for UI display.

---

## AI Rules (enforced in application code, not relying on model behavior)

| Capability                       | AI (Groq/Gemini)     | Application (Python)     |
|----------------------------------|----------------------|--------------------------|
| Suggest draft text               | ✓ YES                | stores as unapproved     |
| Approve draft for sending        | ✗ NEVER              | ✓ explicit operator POST |
| Trigger SMTP send                | ✗ NEVER              | ✓ queue worker only      |
| Add/remove suppression           | ✗ NEVER              | ✓ explicit operator POST |
| Override policy gate             | ✗ NEVER              | ✓ deterministic check    |
| Decide follow-up eligibility     | ✗ NEVER              | ✓ stop-condition checker |
| Choose source label / audit data | ✗ never trusted      | ✓ always application-set |

AI being unavailable (rate-limited, keys exhausted, unconfigured) MUST NOT
block: manual draft creation, draft editing, explicit approval, queue evaluation,
send dispatch, follow-up checks, suppression management, or audit logging.

---

## Required AI Tests

```
test_groq_key_parsing_separators       — comma, semicolon, newline all parsed
test_groq_key_dedup                    — duplicate keys collapsed
test_groq_fingerprint_no_raw_key       — fingerprint does not contain key
test_groq_pool_lru_distribution        — keys selected fairly
test_groq_429_cools_only_affected_key  — other keys remain active
test_groq_all_keys_exhausted           — returns model_unavailable_rate_limited
test_groq_invalid_key_quarantine       — 401 quarantines key
test_groq_queue_deadline               — exits cleanly when deadline exceeded
test_gemini_same_patterns              — mirrors Groq tests for Gemini pool
test_ai_malformed_output_fallback      — invalid JSON → AIFailure, not crash
test_ai_unavailable_manual_unblocked   — manual draft/approval works when AI is down
test_no_raw_key_in_logs_or_repr        — no secret in error repr or audit payload
```
