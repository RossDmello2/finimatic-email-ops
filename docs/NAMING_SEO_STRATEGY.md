# Naming And SEO Strategy

Generated: 2026-06-01

Scope: GitHub-facing identity, README positioning, search terms, naming options, topics, and visual presentation for Finimatic. This document is documentation-only and does not approve a GitHub repository rename.

## Current Identity Audit

| Item | Current State | Evidence | Assessment |
| --- | --- | --- | --- |
| GitHub repository | `RossDmello2/email-automation` | `git remote -v`; `gh repo view RossDmello2/email-automation` | Searchable but generic. It says the category, not the strongest differentiator. |
| README title | `Finimatic Email Ops` | `README.md:1` | Better than brand-only because it keeps the name while adding a category cue. |
| README subtitle | Local-first, human-approved cold email operations with Gmail, AI-assisted drafting, policy gates, reply tracking, follow-ups, and audit logs. | `README.md:3` | Clear in the first few seconds and avoids fake production claims. |
| GitHub description | Open-source FastAPI + React dashboard for self-hosted cold email operations: import leads, draft with Groq/Gemini, approve sends, track replies/follow-ups, and audit actions. | `gh repo view RossDmello2/email-automation --json description` | Clear, searchable, and honest. It now includes category, stack, deployment model, and workflow verbs. |
| Topics | 20 topics covering email, stack, AI, Gmail, and governance | `gh repo view RossDmello2/email-automation --json repositoryTopics` | Good baseline. Should shift from generic `email-marketing` to `email-outreach`, `self-hosted`, and `local-first`. |
| First paragraph | Open-source self-hosted email operations dashboard built with FastAPI, React, Vite, SQLAlchemy, Gmail SMTP/IMAP, Groq, and Gemini. | `README.md:9` | Stronger than framework-only copy because it says category, use case, and stack. |
| Visuals | Conceptual hero plus three sanitized product screenshots | `README.md:19-25`; `docs/assets/brand/hero.png`; `docs/assets/screenshots/*.png` | Good trust signal. Real screenshots remain separate from generated artwork. |

## Source-Backed Project Identity

Finimatic Email Ops is a local-first email operations dashboard for governed cold outreach. The backend is FastAPI and mounts routes for settings, health, canary, imports, contacts, conversations, auto-reply, drafts, templates, campaigns, queue, follow-ups, suppressions, replies, audit, and the assistant under `/api/agent` (`backend/app/main.py:97-127`).

The frontend is a React/Vite dashboard with primary surfaces for setup, provider health, import, contacts, drafts, templates, campaigns, queue, follow-ups, replies/stops, conversations, auto-reply, suppressions, audit logs, errors, settings, and the floating assistant (`frontend/src/App.tsx:55-71`, `frontend/src/App.tsx:232-251`).

The data model is operational rather than a simple demo. It includes settings, imports, contacts, drafts, templates, send queue, send attempts, follow-up sequences, suppressions, replies, conversation messages, audit events, provider health, campaign plans, agent sessions, and pending email actions (`backend/app/db/models.py:18-271`).

The strongest differentiator is side-effect governance:

- Drafts are unapproved by default (`backend/app/db/models.py:76-90`).
- Queue sends pass policy checks before dispatch (`backend/app/send/policy.py:69-103`).
- Assistant sends are bound to pending actions with session, expiry, consumed-state, and draft-hash checks (`backend/app/agent/pending.py:13-112`).
- Settings encrypt Gmail, Groq, and Gemini values before storage (`backend/app/settings/service.py:145-155`).
- Settings reads expose counts and fingerprints instead of raw provider keys (`backend/app/settings/service.py:192-240`).

The strongest public use cases are:

- study a full-stack FastAPI + React email operations app
- self-host a private cold-email operations dashboard
- learn how to place policy gates around outbound email side effects
- study human-in-the-loop AI drafting and confirmation-bound sending
- run fake/dry-run/canary flows before using live Gmail

Boundaries that must remain explicit:

- Do not claim public SaaS readiness or built-in multi-user authentication. The docs warn to add authentication or private access before public exposure (`README.md:13`, `docs/DEPLOYMENT.md:14-21`, `SECURITY.md:35-40`).
- Do not claim every email path is always human-approved. Auto-reply can run in autonomous mode when enabled and gates pass (`frontend/src/App.tsx:2408-2413`, `backend/app/conversations/auto_reply_service.py:192-214`).
- Do not claim full multi-agent LLM orchestration for every assistant stage. Several assistant stages are deterministic/rule-based, with Groq used where available for channel classification (`backend/app/agent/goal_frame.py:7-62`, `backend/app/agent/channel_router.py:50-94`).
- Do not claim attachment content processing. The assistant frontend sends metadata and strips the `File` object (`frontend/src/features/floating-assistant/assistantApi.ts:37-43`, `frontend/src/features/floating-assistant/assistantStore.ts:143-150`).
- Do not claim deliverability, inbox placement, compliance, or a verified live hosted URL (`README.md:372-376`, `docs/DEPLOYMENT.md:161-168`).

## Search Intent Matrix

| Persona | Likely Search Query | Relevant Keywords | What They Need To See | Topic Candidates |
| --- | --- | --- | --- | --- |
| Beginner full-stack developer | `fastapi react email automation example` | FastAPI, React, Vite, SQLAlchemy, email dashboard | screenshots, quick start, project structure, fake mode | `fastapi`, `react`, `vite`, `sqlalchemy`, `email-automation` |
| Self-hosting user | `self hosted cold email dashboard` | self-hosted, local-first, Gmail, PostgreSQL | deployment caveats, auth warning, env vars | `self-hosted`, `local-first`, `gmail`, `postgresql` |
| Technical email operator | `cold email queue follow up tracking open source` | queue, follow-ups, replies, suppressions, audit | operational workflow and screenshots | `cold-email`, `email-outreach`, `email-ops`, `audit-logs` |
| AI builder | `AI email assistant human in the loop send confirmation` | AI drafting, confirmation, pending action, Groq, Gemini | safety model and assistant confirmation flow | `ai-email-assistant`, `human-in-the-loop`, `groq`, `gemini` |
| Security-minded engineer | `email automation secrets backend encrypted settings` | Fernet, backend-owned secrets, redaction, policy gates | security notes and no frontend secrets | `gmail`, `smtp`, `imap`, `audit-logs` |
| Recruiter or non-technical evaluator | `email automation dashboard portfolio project` | dashboard, workflow, screenshots, open source | first paragraph, preview, plain-English value | `email-automation`, `email-ops`, `react`, `python` |

## Similar Repository Pattern Scan

Method: GitHub CLI search and live web search on 2026-06-01 using `cold email automation`, `email automation dashboard`, `self hosted email marketing`, `AI email assistant`, `self hosted email outreach automation`, and related terms.

Observed examples:

| Query | Examples Found | Pattern |
| --- | --- | --- |
| `cold email automation` | [PaulleDemon/Email-automation](https://github.com/PaulleDemon/Email-automation), [deep-div/Cold-Email-Automation](https://github.com/deep-div/Cold-Email-Automation), [SURESHBEEKHANI/Cold-Email-Automations](https://github.com/SURESHBEEKHANI/Cold-Email-Automations) | Many names are literal and generic. They are searchable but not memorable. |
| `email automation dashboard` | [manoj-kumar006/Email-automation-dashboard](https://github.com/manoj-kumar006/Email-automation-dashboard), [Nimesh-Tharaka/Smart-Email-Automation-Dashboard](https://github.com/Nimesh-Tharaka/Smart-Email-Automation-Dashboard) | Dashboard terms help search intent, but descriptions often lack differentiators. |
| `self hosted email marketing` | [mettle/sendportal](https://github.com/mettle/sendportal), [vitorfs/colossus](https://github.com/vitorfs/colossus) | Higher-signal projects often use a short brand plus a direct description. |
| `AI email assistant` | [Drlordbasil/groq-gmail-assistant](https://github.com/Drlordbasil/groq-gmail-assistant), [NotaBeen/notabeen-ai-email-assistant](https://github.com/NotaBeen/notabeen-ai-email-assistant) | AI-focused names attract searchers but can over-focus on chat instead of the full workflow. |
| `self-hosted email agent` | [cloudflare/agentic-inbox](https://github.com/cloudflare/agentic-inbox), [agenticmail/agenticmail](https://github.com/agenticmail/agenticmail) | Agent-oriented projects benefit from very direct descriptions of the trust boundary and deployment model. |
| `open-source email platform` | [useplunk/plunk](https://github.com/useplunk/plunk), [199ocero/mailifyflow](https://github.com/199ocero/mailifyflow) | Larger email platforms use broad platform language; Finimatic should stay narrower and emphasize governed outreach ops. |

Useful naming patterns:

- Short brand + category descriptor: `Finimatic Email Ops`
- Use-case-first subtitle: `Self-hosted cold email operations with human approval gates`
- Stack in description, not necessarily in the name
- Safety differentiator in description: `policy-gated`, `audit logs`, `human-approved`

Overused or weak patterns:

- `ColdEmailAutomation`, `Email-Automation-Dashboard`, `Smart Email Automation`
- Names that hide email/outreach entirely
- Names that imply inbox management when the app is primarily outbound operations
- Names that imply autonomous sending or production deliverability

## Candidate Names

Scores: 1 = weak, 10 = strong. Total is directional, not a trademark or availability check.

| Candidate | Clarity | Memorability | Searchability | Honesty | Domain Fit | Beginner Appeal | Professional Credibility | Uniqueness | Notes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Finimatic Email Ops | 9 | 8 | 9 | 10 | 10 | 9 | 9 | 8 | Best balance. Keeps brand and adds category. |
| Finimatic Outreach Ops | 8 | 8 | 8 | 9 | 9 | 8 | 9 | 8 | Good if outbound outreach is emphasized over general email. |
| Finimatic MailOps | 8 | 8 | 7 | 9 | 9 | 7 | 9 | 8 | Professional, but `MailOps` is less beginner-friendly. |
| Governed Email Ops | 9 | 6 | 8 | 10 | 9 | 7 | 9 | 5 | Accurate but generic and less ownable. |
| HumanLoop Email Ops | 8 | 8 | 7 | 9 | 8 | 8 | 8 | 7 | Strong safety signal, but sounds narrower than the full app. |
| PolicyGate Mail | 7 | 8 | 6 | 8 | 8 | 6 | 8 | 8 | Memorable, but policy-gate language may feel abstract. |
| CanaryMail Ops | 7 | 8 | 6 | 8 | 7 | 6 | 8 | 7 | Canary is a real workflow, but not the whole product. |
| DraftQueue | 7 | 8 | 6 | 7 | 7 | 8 | 7 | 7 | Clean, but omits replies, audit, and AI. |
| ReplySafe Ops | 7 | 7 | 6 | 7 | 7 | 7 | 8 | 7 | Sounds like reply safety rather than outbound ops. |
| Outreach Guard | 8 | 7 | 8 | 8 | 8 | 8 | 8 | 6 | Good safety framing, but could imply compliance features not verified. |
| Self-Hosted Cold Email Ops | 10 | 4 | 10 | 9 | 9 | 8 | 7 | 3 | Very searchable but poor as a brand. |
| Cold Email Control Room | 9 | 7 | 8 | 8 | 9 | 8 | 8 | 6 | Visually strong, but a bit long and dramatic. |
| Mailflow Governor | 7 | 7 | 5 | 8 | 7 | 6 | 8 | 7 | Interesting but not obvious enough for search. |
| ConfirmSend | 7 | 8 | 6 | 7 | 7 | 8 | 7 | 8 | Good assistant-send concept, too narrow for the full dashboard. |
| AuditMail Ops | 7 | 7 | 6 | 8 | 7 | 6 | 8 | 7 | Audit is real, but it should not dominate the identity. |
| Send Queue Studio | 8 | 6 | 7 | 8 | 8 | 8 | 7 | 5 | Understandable but misses replies/AI and sounds generic. |
| Gmail Outreach Console | 9 | 5 | 9 | 8 | 8 | 8 | 7 | 4 | Searchable, but too tied to Gmail and less brandable. |
| AI Outreach Desk | 7 | 7 | 8 | 6 | 7 | 8 | 6 | 5 | Attractive but risks overclaiming AI centrality. |

Rejected directions:

- `Finimatic AI`: too broad and hides the email operations workflow.
- `Autonomous Email Agent`: misleading because side effects are intended to be governed and not fully autonomous.
- `Inbox Copilot`: misleading because the app is not primarily an inbox manager.
- `Production Cold Email Platform`: overclaims readiness, authentication, compliance, and deliverability.
- `Enterprise Email Automation`: overclaims enterprise readiness.

## Top 3 Recommended Names

### 1. Finimatic Email Ops

Best display name: `Finimatic Email Ops`

Best repo slug: `finimatic-email-ops`

Best tagline: `Local-first, human-approved cold email operations with Gmail, AI-assisted drafting, policy gates, reply tracking, follow-ups, and audit logs.`

Best GitHub description:

`Open-source FastAPI + React dashboard for self-hosted cold email operations: import leads, draft with Groq/Gemini, review and approve sends, track replies/follow-ups, and audit every action.`

Recommended topics:

`cold-email`, `email-automation`, `email-outreach`, `email-ops`, `self-hosted`, `local-first`, `human-in-the-loop`, `ai-email-assistant`, `gmail`, `smtp`, `imap`, `fastapi`, `python`, `react`, `typescript`, `vite`, `sqlalchemy`, `postgresql`, `groq`, `gemini`

Risk/tradeoff: the name is longer than `Finimatic`, but it is much clearer in GitHub search and README previews.

### 2. Finimatic Outreach Ops

Best repo slug: `finimatic-outreach-ops`

Tagline: `Governed outreach operations for drafting, approving, sending, tracking replies, and auditing every email action.`

Risk/tradeoff: stronger outreach positioning, but less direct for users searching `email automation`.

### 3. Governed Email Ops

Best repo slug: `governed-email-ops`

Tagline: `A self-hosted email operations dashboard built around review, policy gates, and audit trails.`

Risk/tradeoff: very clear, but less ownable and less memorable than Finimatic.

## Final Recommendation

Keep the current GitHub repository slug `email-automation` until the owner explicitly approves a rename. The current slug is generic but not harmful, and renaming would affect clone URLs, GitHub Pages paths, badges, CI references, deployment docs, and external links.

Recommended display identity now:

- README title: `Finimatic Email Ops`
- Short tagline: `Local-first, human-approved cold email operations with Gmail, AI-assisted drafting, policy gates, reply tracking, follow-ups, and audit logs.`
- Public category: `self-hosted cold email operations dashboard`

Recommended repo rename only if explicitly approved:

```powershell
gh repo rename finimatic-email-ops --repo RossDmello2/email-automation
```

Before running a rename, update any GitHub Pages paths, badges, deployment docs, local remotes, and external deployment settings that reference `email-automation`.

## Visual Strategy

Real screenshots should stay near the top because they prove the dashboard exists. Generated art should stay clearly labeled as conceptual. Current visual assets:

- `docs/assets/brand/hero.png` - conceptual README hero artwork
- `docs/assets/brand/social-preview.png` - prepared GitHub social preview asset
- `docs/assets/screenshots/drafts-dashboard.png` - real sanitized drafts dashboard screenshot
- `docs/assets/screenshots/replies-stops-dashboard.png` - real sanitized replies/stops screenshot
- `docs/assets/screenshots/campaign-assistant.png` - real sanitized campaign builder and assistant screenshot

Recommended GitHub social preview upload:

1. Open GitHub repository Settings.
2. Go to Social preview.
3. Upload `docs/assets/brand/social-preview.png`.
4. Do not use generated artwork as proof of product behavior; keep the README screenshots as the product evidence.

## Claim Hygiene Checklist

| Claim Type | Use? | Recommended Wording |
| --- | --- | --- |
| Local-first | Yes | `local-first` because SQLite default, local setup, backend-owned settings, and self-hosting docs support it. |
| Self-hosted | Yes | `self-hosted` with caveat that auth/private access is needed before public exposure. |
| Human-approved | Yes, qualified | Use for drafts, queue, and assistant sends; do not say every email path always requires human approval because auto-reply autonomous mode exists. |
| AI-powered | Avoid as headline | Use `AI-assisted drafting` or `AI-assisted assistant` instead. |
| Production-ready | No | Use `READY WITH GAPS` and document missing auth/live-provider verification. |
| Enterprise-grade | No | Not source-backed. |
| Deliverability/compliance | No | Explicitly state not guaranteed. |
| Secure | Avoid broad claim | Use concrete claims: encrypted stored settings, backend-owned secrets, redacted audit payloads, pending-action confirmation. |
