from __future__ import annotations

import logging

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger
except ModuleNotFoundError:
    BackgroundScheduler = None
    IntervalTrigger = None

from app.config import get_settings, missing_geotab_credentials
from app.data_gathering.sync_service import SyncService
from app.database.session import SessionLocal

logger = logging.getLogger(__name__)


def _job_all() -> None:
    with SessionLocal() as db:
        SyncService(db).sync_all()


def create_scheduler() -> BackgroundScheduler:
    if BackgroundScheduler is None or IntervalTrigger is None:
        raise RuntimeError("APScheduler is not installed")
    settings = get_settings()
    scheduler = BackgroundScheduler(timezone="UTC")
    trigger = IntervalTrigger(minutes=settings.sync_interval_minutes)
    scheduler.add_job(
        _job_all,
        trigger=trigger,
        id="sync_all",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
        misfire_grace_time=300,
    )
    return scheduler


def start_scheduler() -> BackgroundScheduler | None:
    settings = get_settings()
    if not settings.scheduler_enabled:
        logger.info("scheduler_disabled")
        return None
    missing = missing_geotab_credentials(settings)
    if missing:
        logger.error(
            "scheduler_not_started reason=missing_geotab_credentials missing=%s",
            ",".join(missing),
        )
        return None
    if BackgroundScheduler is None:
        logger.warning("scheduler_unavailable apscheduler_not_installed=true")
        return None
    scheduler = create_scheduler()
    scheduler.start()
    logger.info("scheduler_started interval_minutes=%s", settings.sync_interval_minutes)
    return scheduler
