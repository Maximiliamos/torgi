from __future__ import annotations

import logging
from datetime import datetime, timezone

from celery import Celery

from bankrotai.core import get_region_sync_slug, get_settings
from bankrotai.db import session_scope, upsert_region_sync_state, init_db
from bankrotai.scrapers import GorodTorgiClient, ingest_recent_tbankrot, sync_public_real_estate
from bankrotai.logic import cleanup_closed_lots

logger = logging.getLogger(__name__)

celery_app = Celery("bankrotai")
settings = get_settings()
celery_app.conf.broker_url = settings.redis_url
celery_app.conf.result_backend = settings.redis_url

@celery_app.task(name="bankrotai.tasks.sync_public_region_task")
def sync_public_region_task(city_slug: str = "yaroslavl", force: bool = False, search: str | None = None) -> int:
    try:
        sync_slug = get_region_sync_slug(city_slug)
        init_db()
        with session_scope() as session:
            upsert_region_sync_state(session, city_slug, status="running", started_at=datetime.now(timezone.utc).replace(tzinfo=None))
            logger.info("Syncing %s via source slug %s", city_slug, sync_slug)
            imported_gt = sync_public_real_estate(session, sync_slug, search=search)
            imported_tb = ingest_recent_tbankrot(session, sync_slug)
            cleanup_closed_lots(session)
            total = len(imported_gt) + len(imported_tb)
            upsert_region_sync_state(session, city_slug, status="ready", lots_discovered=total, finished_at=datetime.now(timezone.utc).replace(tzinfo=None))
            return total
    except Exception as e:
        logger.error("Sync failed for %s: %s", city_slug, e)
        with session_scope() as session:
            upsert_region_sync_state(session, city_slug, status="failed", error_message=str(e))
        raise

def schedule_region_sync(city_slug: str, force: bool = False, search: str | None = None) -> str:
    from bankrotai.db import get_region_sync_state, _region_sync_is_stuck
    
    init_db()
    with session_scope() as session:
        state = get_region_sync_state(session, city_slug)
        if state and state.status in {"queued", "running"} and not force:
            if not _region_sync_is_stuck(state):
                logger.info("Sync for %s is already %s, skipping duplicate schedule", city_slug, state.status)
                return "skipped-already-running"

    logger.info("Scheduling region sync for %s (force=%s, search=%s)", city_slug, force, search)
    
    # Run in thread if Redis is down
    if not broker_is_available():
        import threading
        with session_scope() as session:
            upsert_region_sync_state(
                session,
                city_slug,
                status="queued",
                requested_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        t = threading.Thread(target=sync_public_region_task, args=(city_slug, force, search))
        t.daemon = True
        t.start()
        return "started-in-thread"
    
    sync_public_region_task.delay(city_slug, force=force, search=search)
    return "started-in-celery"


def broker_is_available() -> bool:
    try:
        from redis import Redis
        r = Redis.from_url(celery_app.conf.broker_url, socket_connect_timeout=2)
        return r.ping()
    except Exception:
        return False
