# API Reference

Base URL in local development:

```text
http://localhost:8000
```

The frontend reads this value from:

```text
VITE_API_URL
```

## Health

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Backend health check. |

## Settings And Provider Health

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/settings` | Read safe settings, configured flags, and key fingerprints. |
| `POST` | `/api/settings` | Save settings and encrypted credentials. |
| `POST` | `/api/settings/verify-email` | Verify the selected email provider transport. |
| `POST` | `/api/settings/verify-smtp` | Verify Gmail SMTP credentials. |
| `GET` | `/api/provider-health` | Read provider health rows. |

## Canary

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/canary/send` | Send a canary email and mark sender readiness when successful. |

## Imports

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/import/preview` | Validate imported leads without committing them. |
| `POST` | `/api/import/commit` | Commit accepted preview rows into contacts. |

## Contacts

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/contacts` | List contacts. |
| `POST` | `/api/contacts` | Create a contact. |
| `PATCH` | `/api/contacts/{id}` | Update contact fields/status. |
| `DELETE` | `/api/contacts/{id}` | Soft-delete a contact. |
| `GET` | `/api/contacts/recently-deleted` | List recently deleted contacts. |
| `POST` | `/api/contacts/{id}/restore` | Restore a soft-deleted contact. |

## Drafts And Templates

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/drafts` | List drafts. |
| `POST` | `/api/drafts` | Create a manual draft. |
| `POST` | `/api/drafts/generate` | Generate a single AI/manual draft. |
| `PATCH` | `/api/drafts/{id}` | Edit a draft. |
| `POST` | `/api/drafts/{id}/approve` | Approve a draft and queue it. |
| `POST` | `/api/drafts/generate-bulk` | Start bulk draft generation. |
| `GET` | `/api/drafts/bulk-status/{job_id}` | Read bulk generation status. |
| `POST` | `/api/drafts/approve-bulk` | Approve selected generated drafts. |
| `POST` | `/api/drafts/{id}/subject-variants` | Generate subject variants. |
| `GET` | `/api/templates` | List reusable templates. |
| `POST` | `/api/templates` | Create a reusable template. |

## Campaigns

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/campaigns` | List campaign plans. |
| `POST` | `/api/campaigns` | Create a campaign plan. |
| `PATCH` | `/api/campaigns/{id}` | Update a campaign plan. |
| `POST` | `/api/campaigns/{id}/activate` | Activate a campaign plan. |

## Queue And Follow-Ups

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/queue` | List queue entries. |
| `POST` | `/api/queue/process` | Process due queue entries. |
| `GET` | `/api/followups` | List follow-up rows. |
| `PATCH` | `/api/followups/{id}` | Update a follow-up row. |
| `POST` | `/api/followups/process` | Process due follow-ups. |
| `POST` | `/api/followups/{id}/approve-draft` | Approve a pending follow-up draft. |

## Suppressions

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/suppressions` | List suppressed emails. |
| `POST` | `/api/suppressions` | Add a suppression. |
| `DELETE` | `/api/suppressions/{id}` | Delete a suppression. |

## Replies

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/replies` | List replies, with optional filters. |
| `POST` | `/api/replies` | Add a manual reply record. |
| `POST` | `/api/replies/fetch` | Fetch recent replies from Gmail IMAP. |
| `POST` | `/api/replies/{id}/archive` | Archive a reply. |
| `POST` | `/api/replies/{id}/restore` | Restore an archived reply. |
| `DELETE` | `/api/replies/{id}` | Delete a reply. |

## Conversations And Auto-Reply

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/conversations` | List conversation summaries. |
| `GET` | `/api/conversations/{contact_id}` | Read a contact thread. |
| `POST` | `/api/conversations/{contact_id}/generate-reply` | Generate a context-aware reply. |
| `POST` | `/api/conversations/{contact_id}/send` | Send an engaged conversation reply after gates pass. |
| `GET` | `/api/auto-reply/pending` | List proposed auto-reply drafts. |
| `GET` | `/api/auto-reply/log` | Read auto-reply audit log. |
| `POST` | `/api/auto-reply/approve/{draft_id}` | Approve an auto-reply draft. |
| `POST` | `/api/auto-reply/reject/{draft_id}` | Reject an auto-reply draft. |

## Floating Assistant

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/agent/chat` | Send a user message to the governed assistant. |
| `POST` | `/api/agent/confirm` | Confirm a pending assistant send action. |
| `DELETE` | `/api/agent/cancel` | Cancel pending assistant action/session draft. |

Assistant sends are protected by `pending_email_actions`. A send can execute only when the action exists, belongs to the same session, has not expired, is not consumed, and still matches the draft hash.

## Audit

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/audit` | List redacted audit events. |

## Safe Local Examples

Health check:

```bash
curl http://127.0.0.1:8000/api/health
```

Read safe settings. This should return configured flags and fingerprints, not raw credentials:

```bash
curl http://127.0.0.1:8000/api/settings
```

Preview imports without committing contacts:

```bash
curl -X POST http://127.0.0.1:8000/api/import/preview \
  -H "Content-Type: application/json" \
  -d '{"format":"csv","content":"email,creator_name\ndemo@example.com,Demo User"}'
```

Send an assistant message with a disposable session token:

```bash
curl -X POST http://127.0.0.1:8000/api/agent/chat \
  -H "Content-Type: application/json" \
  -d '{"session_token":"local-demo-session","message":"who replied today?"}'
```

Avoid live send, canary, SMTP, IMAP, and provider-key examples in public issues unless all credentials and private data are redacted.
