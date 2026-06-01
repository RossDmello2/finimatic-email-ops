# Historical Reference Docs

This folder keeps original specs, implementation notes, and audit artifacts that explain how Finimatic evolved.

Private screenshot-derived repair notes were removed from the public documentation set because they contained historical inbox/contact evidence that is not needed for public evaluation.

Use the current public docs first:

- [README](../../README.md)
- [Getting Started](../GETTING_STARTED.md)
- [Architecture](../ARCHITECTURE.md)
- [API Reference](../API_REFERENCE.md)
- [Current Data Model](../DATA_MODEL.md)
- [Deployment Guide](../DEPLOYMENT.md)
- [Testing Guide](../TESTING.md)
- [Troubleshooting](../TROUBLESHOOTING.md)

Some files in this folder intentionally describe earlier implementation states. When a reference doc conflicts with live source, prefer:

1. `backend/app/db/models.py` for data model truth.
2. `backend/app/main.py` and router files for API truth.
3. `frontend/src/` for UI behavior truth.
4. `backend/requirements.txt` and `frontend/package.json` for dependency truth.

Do not use files in this folder as proof that a feature is production-ready unless current source and verification checks confirm it.
