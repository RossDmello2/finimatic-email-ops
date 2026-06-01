# Security Policy

Finimatic handles email credentials, AI provider keys, contact data, and outbound email actions. Treat all changes as security-sensitive.

## Supported Versions

This repository currently tracks the main development line only.

| Version | Supported |
| --- | --- |
| `main` | Yes |

## Reporting A Vulnerability

If you find a vulnerability:

1. Do not post credentials, database files, private inbox content, or exploit details in a public issue.
2. Use GitHub private vulnerability reporting if it is enabled for this repository.
3. If private reporting is not enabled, open a minimal public issue that says a private security report is needed, without exploit details or secrets.
4. Include the affected area, reproduction steps, impact, and whether any secret or outbound send was exposed.

Owner-required repository setting: enable GitHub private vulnerability reporting or add a private security contact before inviting broad production use.

## Secret Handling Rules

- Never commit `.env`, `KEYS.md`, SQLite databases, logs, screenshots containing secrets, or provider keys.
- Gmail app passwords, Groq keys, and Gemini keys belong in the Settings UI, not in frontend env vars.
- `FERNET_KEY` belongs in backend runtime configuration.
- Do not expose decrypted settings in API responses, logs, prompts, browser storage, screenshots, or audit payloads.

## Deployment Security

Before public deployment:

- Add authentication or private network access.
- Restrict CORS to the deployed frontend domain.
- Use a persistent database.
- Keep `FERNET_KEY` stable and secret.
- Test dry-run and canary behavior before live sending.
- Review audit logs after every live-send test.

## Assistant Security

The assistant must preserve these guarantees:

- Model output is only a proposal.
- Backend tools execute reads and writes.
- Evidence sent to models is bounded and redacted.
- `email_send_draft` requires a valid pending action.
- Pending actions expire, cannot be reused, and are tied to session and draft hash.
- Send attempts write audit events.
