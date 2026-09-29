"""Run the pipeline automatically on an interval or cron schedule."""
from __future__ import annotations

import logging

from .config import AppConfig
from .pipeline import run_all

log = logging.getLogger(__name__)


def start(cfg: AppConfig) -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    sc = cfg.schedule
    trigger = (CronTrigger.from_crontab(sc.cron, timezone=sc.timezone) if sc.cron
               else IntervalTrigger(minutes=sc.interval_minutes, timezone=sc.timezone))

    def job() -> None:
        try:
            run_all(cfg)
        except Exception:
            log.exception("Scheduled run crashed; will retry on the next tick")

    log.info("Scheduler started (%s). Running once now, then on schedule. Ctrl+C to stop.",
             f"cron '{sc.cron}'" if sc.cron else f"every {sc.interval_minutes} min")
    job()
    scheduler = BlockingScheduler(timezone=sc.timezone)
    scheduler.add_job(job, trigger, id="scrape", max_instances=1, coalesce=True)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped")
