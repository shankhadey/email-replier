"""
Scheduler: per-user Gmail polling.
Each authenticated user gets their own APScheduler job keyed by user_id.
"""

import logging
import time
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

import auth
import database as db
from gmail_client import fetch_unread_emails
from processor import process_email

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler = None

# Per-user state
_last_run: dict[str, str] = {}      # user_id -> ISO timestamp string
_last_results: dict[str, list] = {} # user_id -> results list


def init_scheduler():
    global _scheduler
    if _scheduler is None or not _scheduler.running:
        _scheduler = BackgroundScheduler()
        _scheduler.start()
        logger.info("Scheduler started.")


def shutdown_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler shut down.")


def add_user_job(user_id: str) -> None:
    """Add or replace the polling job for a user."""
    global _scheduler
    if _scheduler is None or not _scheduler.running:
        init_scheduler()

    config = db.load_user_config(user_id)
    interval = config.get("poll_interval_minutes", 30)
    _scheduler.add_job(
        func=_run_for_user,
        trigger=IntervalTrigger(minutes=interval),
        id=user_id,
        args=[user_id],
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    logger.info(f"[{user_id}] Scheduler job added/updated: every {interval}min")


def remove_user_job(user_id: str) -> None:
    """Remove a user's polling job (e.g. on token revocation)."""
    global _scheduler
    if _scheduler and _scheduler.get_job(user_id):
        _scheduler.remove_job(user_id)
        logger.info(f"[{user_id}] Scheduler job removed.")


def _run_for_user(user_id: str) -> None:
    """Core poll logic for a single user."""
    # Verify token is still valid before proceeding
    try:
        auth.get_credentials(user_id)
    except ValueError:
        db.log_event(user_id, "error", "No valid token — skipping poll")
        return

    config = db.load_user_config(user_id)
    hour = datetime.now().hour
    if not (config["poll_start_hour"] <= hour <= config["poll_end_hour"]):
        logger.info(f"[{user_id}] Outside poll window ({hour}:00). Skipping.")
        return

    user = db.get_user(user_id)
    lookback = config.get("lookback_hours", 72)
    if lookback > 0:
        epoch_filter = int(time.time()) - lookback * 3600
    else:
        # 0 = use service_start_epoch as lower bound (don't go before first login)
        epoch_filter = user.get("service_start_epoch") if user else None

    logger.info(f"[{user_id}] Polling Gmail (lookback={lookback}h)...")
    db.log_event(user_id, "poll_start", f"Scanning Gmail (past {lookback}h)...")

    gmail_service = auth.get_gmail_service(user_id)
    emails = fetch_unread_emails(gmail_service, max_results=50, after_epoch=epoch_filter)

    results = []
    for email in emails:
        try:
            result = process_email(email, user_id=user_id)
            results.append(result)
            logger.info(f"  [{user_id}] -> {result['action']}: {result.get('subject', '')}")
        except Exception as e:
            logger.error(f"  [{user_id}] Error processing {email.get('id')}: {e}")

    _last_run[user_id] = datetime.now(timezone.utc).isoformat()
    _last_results[user_id] = results

    counts = {}
    for r in results:
        counts[r["action"]] = counts.get(r["action"], 0) + 1
    summary = (
        f"Scanned {len(emails)} email(s) — "
        f"{counts.get('review', 0)} queued, "
        f"{counts.get('sent', 0)} sent, "
        f"{counts.get('skipped', 0)} skipped"
    )
    db.log_event(user_id, "poll_end", summary)
    logger.info(f"[{user_id}] Poll complete. {summary}")


def run_now(user_id: str) -> list:
    """Trigger an immediate poll for a user, bypassing the time window."""
    _run_for_user_force(user_id)
    return _last_results.get(user_id, [])


def _run_for_user_force(user_id: str) -> None:
    """Same as _run_for_user but skips the hour-window check."""
    try:
        auth.get_credentials(user_id)
    except ValueError:
        db.log_event(user_id, "error", "No valid token — cannot run now")
        return

    config = db.load_user_config(user_id)
    user = db.get_user(user_id)
    lookback = config.get("lookback_hours", 72)
    if lookback > 0:
        epoch_filter = int(time.time()) - lookback * 3600
    else:
        epoch_filter = user.get("service_start_epoch") if user else None

    db.log_event(user_id, "poll_start", f"Manual scan (past {lookback}h)...")
    gmail_service = auth.get_gmail_service(user_id)
    emails = fetch_unread_emails(gmail_service, max_results=50, after_epoch=epoch_filter)

    results = []
    for email in emails:
        try:
            result = process_email(email, user_id=user_id)
            results.append(result)
        except Exception as e:
            logger.error(f"  [{user_id}] Error processing {email.get('id')}: {e}")

    _last_run[user_id] = datetime.now(timezone.utc).isoformat()
    _last_results[user_id] = results

    counts = {}
    for r in results:
        counts[r["action"]] = counts.get(r["action"], 0) + 1
    summary = (
        f"Scanned {len(emails)} email(s) — "
        f"{counts.get('review', 0)} queued, "
        f"{counts.get('sent', 0)} sent, "
        f"{counts.get('skipped', 0)} skipped"
    )
    db.log_event(user_id, "poll_end", summary)


def get_user_status(user_id: str) -> dict:
    global _scheduler
    config = db.load_user_config(user_id)
    job_running = (
        _scheduler is not None
        and _scheduler.running
        and _scheduler.get_job(user_id) is not None
    )
    return {
        "running": job_running,
        "poll_interval_minutes": config["poll_interval_minutes"],
        "poll_start_hour": config["poll_start_hour"],
        "poll_end_hour": config["poll_end_hour"],
        "last_run": _last_run.get(user_id),
        "last_results_count": len(_last_results.get(user_id, [])),
    }
