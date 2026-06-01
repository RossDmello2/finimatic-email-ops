# Changelog

All notable project changes should be documented here.

## [Unreleased]

- Refreshed the README for open-source discovery, beginner setup, screenshots, architecture, safety boundaries, and status gaps.
- Added a current data model guide, troubleshooting guide, reference-doc index, and generated conceptual README artwork.
- Moved historical specs, audits, and repair notes into `docs/reference/` to reduce root clutter.
- Expanded issue and pull request templates with transport, database, side-effect, screenshot, and secret-scan prompts.
- Broadened `.gitignore` coverage for local keys, certs, build output, and deployment state.

## [0.1.0] - 2026-05-27

- Initial public release of the Finimatic email automation project.
- FastAPI backend with contacts, imports, settings, drafts, templates, queue, follow-ups, replies, conversations, audit events, provider health, campaigns, and floating assistant APIs.
- React/Vite dashboard with operator workflows for cold-email operations.
- Governed assistant module with confirmation-bound email actions.
- Backend test suite and frontend production build support.
