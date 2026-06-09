from __future__ import annotations

import logging

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger
except ModuleNotFoundError:
    BackgroundScheduler = None
    IntervalTrigger = None

from app.config import get_settings, validate_geotab_for_scheduler
from app.database.session import SessionLocal
from app.services.sync_service import SyncService

logger = logging.getLogger(__name__)


def _job(method_name: str) -> None:
    with SessionLocal() as db:
        service = SyncService(db)
        getattr(service, method_name)()


def create_scheduler() -> BackgroundScheduler:
    if BackgroundScheduler is None or IntervalTrigger is None:
        raise RuntimeError("APScheduler is not installed")
    settings = get_settings()
    scheduler = BackgroundScheduler(timezone="UTC")
    trigger = IntervalTrigger(minutes=settings.sync_interval_minutes)
    for name in ["sync_vehicles", "sync_drivers", "sync_trips", "sync_logs", "sync_faults"]:
        scheduler.add_job(
            _job,
            trigger=trigger,
            args=[name],
            id=name,
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
    validate_geotab_for_scheduler(settings)
    if BackgroundScheduler is None:
        logger.warning("scheduler_unavailable apscheduler_not_installed=true")
        return None
    scheduler = create_scheduler()
    scheduler.start()
    logger.info("scheduler_started interval_minutes=%s", settings.sync_interval_minutes)
    return scheduler
