"""
Scheduler: polls Gmail every N minutes between configured hours.
"""

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config import load_config
from gmail_client import fetch_unread_emails
from processor import process_email

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler = None
_last_run: datetime = None
_last_results: list = []


def _poll():
    global _last_run, _last_results
    config = load_config()
    now = datetime.now()
    hour = now.hour

    if not (config["poll_start_hour"] <= hour < config["poll_end_hour"]):
        logger.info(f"Outside poll window ({hour}:00). Skipping.")
        return

    logger.info("Polling Gmail...")
    emails = fetch_unread_emails(max_results=20)
    results = []
    for email in emails:
        result = process_email(email)
        results.append(result)
        logger.info(f"  -> {result['action']}: {result.get('subject', '')}")

    _last_run = now
    _last_results = results
    logger.info(f"Poll complete. Processed {len(results)} emails.")


def start_scheduler():
    global _scheduler
    config = load_config()

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        _poll,
        trigger=IntervalTrigger(minutes=config["poll_interval_minutes"]),
        id="gmail_poll",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(f"Scheduler started. Poll every {config['poll_interval_minutes']}min "
                f"between {config['poll_start_hour']}:00-{config['poll_end_hour']}:00")


def stop_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown()
        logger.info("Scheduler stopped.")


def reschedule(interval_minutes: int):
    """Update the poll interval without restarting."""
    global _scheduler
    if _scheduler:
        _scheduler.reschedule_job(
            "gmail_poll",
            trigger=IntervalTrigger(minutes=interval_minutes),
        )


def run_now():
    """Trigger an immediate poll (for manual refresh)."""
    _poll()
    return _last_results


def get_status() -> dict:
    config = load_config()
    return {
        "running": _scheduler is not None and _scheduler.running,
        "poll_interval_minutes": config["poll_interval_minutes"],
        "poll_start_hour": config["poll_start_hour"],
        "poll_end_hour": config["poll_end_hour"],
        "last_run": _last_run.isoformat() if _last_run else None,
        "last_results_count": len(_last_results),
    }
