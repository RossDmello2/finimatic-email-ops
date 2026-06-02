from __future__ import annotations

import logging
import os

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.followups.service import process_due_followups
from app.send.queue_worker import process_pending_queue
from app.settings.service import get_bool, get_int

logger = logging.getLogger(__name__)


def auto_process_enabled(db: Session) -> bool:
    if os.getenv("FINIMATIC_DISABLE_SCHEDULER") == "1" or os.getenv("FINIMATIC_DISABLE_AUTO_PROCESS") == "1":
        return False
    return get_bool(db, "auto_process_enabled")


def auto_process_interval_seconds(db: Session, kind: str) -> int:
    if kind == "followups":
        return max(30, min(get_int(db, "auto_process_followup_interval_seconds"), 3600))
    return max(5, min(get_int(db, "auto_process_queue_interval_seconds"), 600))


async def run_auto_process_once(*, include_followups: bool = False) -> dict:
    with SessionLocal() as db:
        if not auto_process_enabled(db):
            return {"enabled": False}
        result: dict = {"enabled": True}
        if include_followups:
            result["followups"] = process_due_followups(db)
        result["queue"] = await process_pending_queue(db)
        return result


def schedule_auto_process(background_tasks: BackgroundTasks | None, *, include_followups: bool = False) -> bool:
    if background_tasks is None or os.getenv("FINIMATIC_DISABLE_SCHEDULER") == "1":
        return False
    background_tasks.add_task(run_auto_process_once, include_followups=include_followups)
    return True
