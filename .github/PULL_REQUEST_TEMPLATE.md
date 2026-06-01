# Summary

Describe what changed and why.

## Validation

- [ ] Backend tests: `cd backend && python -m pytest -q`
- [ ] Frontend build: `cd frontend && npm run build`
- [ ] Documentation updated if behavior changed
- [ ] No secrets, database files, logs, or build output committed
- [ ] `git diff --check` passes
- [ ] UI changes include sanitized screenshots, if applicable

## Security Checklist

- [ ] No Gmail, Groq, Gemini, SMTP, IMAP, Fernet, or token values exposed
- [ ] Frontend still uses only `VITE_API_URL`
- [ ] Email sends remain policy-gated
- [ ] Assistant sends remain pending-action and confirmation-gated
- [ ] Audit behavior preserved for side-effecting actions
- [ ] Any database migration or schema change is explained
- [ ] Any live email, IMAP, queue, or scheduler side effect is called out
