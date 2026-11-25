"""Background jobs. Run inside the API (RUN_SCHEDULER=true) or standalone: `python -m app.worker`."""
import logging
import time

from apscheduler.schedulers.background import BackgroundScheduler

from .database import SessionLocal
from .services import extend_all_schedules, mark_missed, send_due_reminders

log = logging.getLogger("medtime.worker")


def tick():
    with SessionLocal() as db:
        r, m = send_due_reminders(db), mark_missed(db)
        if r or m:
            log.info("reminders=%s missed=%s", r, m)


def extend():
    with SessionLocal() as db:
        log.info("generated %s future doses", extend_all_schedules(db))


def start_scheduler() -> BackgroundScheduler:
    s = BackgroundScheduler(timezone="UTC")
    s.add_job(tick, "interval", minutes=1, id="tick", max_instances=1, coalesce=True)
    s.add_job(extend, "cron", hour=0, minute=30, id="extend", max_instances=1)
    s.start()
    return s


if __name__ == "__main__":
    from .database import Base, engine
    from . import models  # noqa: F401
    logging.basicConfig(level=logging.INFO)
    Base.metadata.create_all(engine)
    start_scheduler()
    while True:
        time.sleep(3600)
