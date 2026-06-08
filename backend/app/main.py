from __future__ import annotations

import os
import asyncio
import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.agent.router import router as agent_router
from app.audit.service import emit_event
from app.audit.router import router as audit_router
from app.campaigns.router import router as campaigns_router
from app.contacts.router import router as contacts_router
from app.conversations.auto_reply_router import router as auto_reply_router
from app.conversations.router import router as conversations_router
from app.db.session import configure_database, init_db, SessionLocal
from app.deliverability.router import router as deliverability_router
from app.drafts.router import router as drafts_router
from app.enrichment.router import router as enrichment_router
from app.followups.router import router as followups_router
from app.imports.router import router as imports_router
from app.integrations.router import router as integrations_router
from app.provider_health.router import router as provider_health_router
from app.replies.router import router as replies_router
from app.replies.imap_fetcher import run_imap_fetch_with_lock
from app.send.canary_router import router as canary_router
from app.send.auto_process import auto_process_enabled, auto_process_interval_seconds
from app.send.router import router as queue_router
from app.send.governance_router import router as governance_router
from app.send.queue_worker import process_pending_queue, release_recoverable_queue_entries
from app.send.smtp_adapter import default_transport
from app.settings.router import router as settings_router
from app.settings.service import get_int, seed_settings
from app.security.authorization import (
    authentication_enabled,
    configured_authorization_checker,
    require_admin_access,
    require_operational_access,
    security_status,
)
from app.security.browser_session import (
    configured_interactive_oidc_settings,
    router as authentication_router,
)
from app.suppressions.router import router as suppressions_router
from app.followups.service import process_due_followups
from app.templates.router import router as templates_router
from app.verification.router import router as verification_router
from app.workflows.router import router as workflows_router

logger = logging.getLogger(__name__)


async def _periodic_queue_worker() -> None:
    while True:
        interval = 5
        try:
            with SessionLocal() as db:
                interval = auto_process_interval_seconds(db, "queue")
                if auto_process_enabled(db):
                    await process_pending_queue(db)
        except Exception:
            logger.exception("queue worker iteration failed")
        await asyncio.sleep(interval)


async def _periodic_followup_worker() -> None:
    while True:
        interval = 60
        try:
            with SessionLocal() as db:
                interval = auto_process_interval_seconds(db, "followups")
                if auto_process_enabled(db):
                    process_due_followups(db)
        except Exception:
            logger.exception("follow-up worker iteration failed")
        await asyncio.sleep(interval)


def _scheduled_imap_reply_fetch() -> None:
    with SessionLocal() as db:
        run_imap_fetch_with_lock(db)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_database(os.getenv("DATABASE_URL"))
    init_db()
    with SessionLocal() as db:
        seed_settings(db)
        from app.integrations.service import ensure_connections, ensure_mappings
        from app.workflows.service import ensure_default_workbook

        ensure_default_workbook(db)
        connections = ensure_connections(db)
        ensure_mappings(db, connections)
        recovered = release_recoverable_queue_entries(db)
        if recovered:
            emit_event(
                db,
                "queue.recoverable_entries_released",
                entity_type="send_queue",
                entity_id="startup",
                payload={"count": recovered},
            )
        db.commit()
    app.state.transport = default_transport()
    if hasattr(app.state.transport, "sent"):
        app.state.transport.sent.clear()
    tasks: list[asyncio.Task] = []
    scheduler: AsyncIOScheduler | None = None
    if os.getenv("FINIMATIC_DISABLE_SCHEDULER") != "1":
        tasks = [
            asyncio.create_task(_periodic_queue_worker()),
            asyncio.create_task(_periodic_followup_worker()),
        ]
        with SessionLocal() as db:
            imap_interval = max(1, get_int(db, "imap_fetch_interval_minutes"))
        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            _scheduled_imap_reply_fetch,
            "interval",
            minutes=imap_interval,
            id="imap_reply_fetch",
            replace_existing=True,
        )
        scheduler.start()
    yield
    for task in tasks:
        task.cancel()
    if scheduler:
        scheduler.shutdown(wait=False)


def create_app() -> FastAPI:
    app = FastAPI(title="Finimatic", version="0.1.0", lifespan=lifespan)
    auth_enabled = authentication_enabled()
    oidc_checker = configured_authorization_checker()
    app.state.oidc_authorization_checker = oidc_checker
    app.state.authorization_checker = oidc_checker
    app.state.interactive_oidc_settings = configured_interactive_oidc_settings() if auth_enabled else None
    origins = [origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if origin.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/security/status")
    def get_security_status(request: Request):
        return security_status(request)

    if auth_enabled:
        app.include_router(authentication_router)
    protected = [Depends(require_operational_access)]
    admin_only = [Depends(require_admin_access)]
    app.include_router(settings_router, dependencies=admin_only)
    app.include_router(provider_health_router, dependencies=protected)
    app.include_router(deliverability_router, dependencies=protected)
    app.include_router(canary_router, dependencies=admin_only)
    app.include_router(imports_router, dependencies=protected)
    app.include_router(contacts_router, dependencies=protected)
    app.include_router(enrichment_router, dependencies=protected)
    app.include_router(verification_router, dependencies=protected)
    app.include_router(conversations_router, dependencies=protected)
    app.include_router(auto_reply_router, dependencies=protected)
    app.include_router(drafts_router, dependencies=protected)
    app.include_router(templates_router, dependencies=protected)
    app.include_router(campaigns_router, dependencies=protected)
    app.include_router(workflows_router, dependencies=protected)
    app.include_router(queue_router, dependencies=protected)
    app.include_router(governance_router, dependencies=protected)
    app.include_router(followups_router, dependencies=protected)
    app.include_router(suppressions_router, dependencies=protected)
    app.include_router(replies_router, dependencies=protected)
    app.include_router(audit_router, dependencies=protected)
    app.include_router(integrations_router, dependencies=protected)
    app.include_router(agent_router, prefix="/api/agent", dependencies=protected)
    return app


app = create_app()
