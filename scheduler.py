"""
Scheduler: polls Gmail every N minutes between configured hours.
"""

import logging
import time
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config import load_config
from gmail_client import fetch_unread_emails
from processor import process_email
from database import log_event

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler = None
_last_run: datetime = None
_last_results: list = []
_service_start_epoch: int = None  # Only process emails received after this


def _poll():
    global _last_run, _last_results
    config = load_config()
    now = datetime.now()
    hour = now.hour

    if not (config["poll_start_hour"] <= hour <= config["poll_end_hour"]):
        logger.info(f"Outside poll window ({hour}:00). Skipping.")
        return

    lookback_hours = config.get("lookback_hours", 72)
    if lookback_hours > 0:
        epoch_filter = int(time.time()) - lookback_hours * 3600
    else:
        epoch_filter = None  # 0 = no limit
    logger.info(f"Polling Gmail... (lookback={lookback_hours}h)")
    log_event("poll_start", f"Scanning Gmail (past {lookback_hours}h)...")

    emails = fetch_unread_emails(max_results=50, after_epoch=epoch_filter)
    results = []
    for email in emails:
        result = process_email(email)
        results.append(result)
        logger.info(f"  -> {result['action']}: {result.get('subject', '')}")

    _last_run = now
    _last_results = results

    counts = {"sent": 0, "review": 0, "skipped": 0, "error": 0}
    for r in results:
        counts[r["action"]] = counts.get(r["action"], 0) + 1
    summary = (
        f"Scanned {len(emails)} email(s) — "
        f"{counts.get('review', 0)} queued, "
        f"{counts.get('sent', 0)} sent, "
        f"{counts.get('skipped', 0)} skipped"
    )
    log_event("poll_end", summary)
    logger.info(f"Poll complete. Processed {len(results)} emails.")


def start_scheduler():
    global _scheduler, _service_start_epoch
    _service_start_epoch = int(time.time())
    logger.info(f"Service start epoch: {_service_start_epoch} — only processing emails after this point.")
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
    global _service_start_epoch
    if not _service_start_epoch:
        _service_start_epoch = int(time.time())
    _poll()
    return _last_results


def get_config_option(key: str):
    from config import load_config
    return load_config().get(key)


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
