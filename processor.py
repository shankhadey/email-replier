"""
Core processor: orchestrates the full pipeline for each new email.
  1. Classify
  2. Skip if no reply needed
  3. Fetch calendar / GDrive if needed
  4. Draft reply
  5. Route (send / review / skip)
  6. Persist
"""

import logging
from typing import Optional

import auth
import database as db
from autonomy_engine import route
from classifier import classify_email
from drafter import draft_reply
from gcal_client import get_free_slots
from gdrive_client import search_and_attach
from gmail_client import create_reply_draft, mark_as_read, send_reply

logger = logging.getLogger(__name__)


def process_email(email: dict, user_id: str) -> dict:
    """
    Full pipeline for a single email belonging to user_id.
    Returns a result dict with action taken.
    """
    config = db.load_user_config(user_id)
    params = db.load_user_params(user_id)
    model = config["anthropic_model"]
    message_id = email["id"]

    if db.is_processed(user_id, message_id):
        return {"message_id": message_id, "action": "skipped", "reason": "already processed"}

    logger.info(f"[{user_id}] Processing: [{email['subject']}] from {email['sender']}")

    # Build gmail service once — reused for send_reply and mark_as_read
    gmail_service = auth.get_gmail_service(user_id)

    # Step 1: Classify
    classification = classify_email(
        sender=email["sender"],
        subject=email["subject"],
        body=email["body"],
        has_attachments=email["has_attachments"],
        params=params,
        model=model,
    )
    logger.info(f"  Classification: {classification}")

    priority = classification.get("sender_priority", "unknown")
    confidence_pct = round(classification.get("confidence", 0) * 100)
    db.log_event(
        user_id,
        "classified",
        f"'{email['subject']}' from {_sender_name(email['sender'])} "
        f"— {priority} priority, {confidence_pct}% confidence",
    )

    # Step 2: Skip if no reply needed
    if not classification.get("needs_reply"):
        db.log_event(user_id, "skipped", f"'{email['subject']}' — no reply needed")
        db.mark_processed(user_id, message_id, email["thread_id"])
        return {"message_id": message_id, "action": "skipped", "reason": "no reply needed"}

    # Step 3: Gather context
    calendar_slots: Optional[str] = None
    attachment_names: list[str] = []
    attachments: list[dict] = []

    if classification.get("needs_calendar"):
        days = int(classification.get("calendar_days_requested") or 7)
        days = max(1, min(days, 60))
        cal_service = auth.get_calendar_service(user_id)
        calendar_slots = get_free_slots(
            cal_service,
            days_ahead=days,
            tz_name=config.get("user_timezone", "America/Chicago"),
        )
        logger.info(f"  Calendar slots ({days}d): {repr(calendar_slots)}")
        db.log_event(user_id, "calendar_checked", f"Checked calendar availability ({days}d window)")

    if classification.get("needs_gdrive") and classification.get("gdrive_query"):
        query = classification["gdrive_query"]
        drive_service = auth.get_drive_service(user_id)
        attachments = search_and_attach(drive_service, query)
        attachment_names = [a["filename"] for a in attachments]
        logger.info(f"  Drive attachments: {attachment_names}")
        if attachment_names:
            db.log_event(user_id, "drive_fetched", f"Fetched '{attachment_names[0]}' from Drive")
        else:
            db.log_event(user_id, "drive_fetched", f"Drive search for '{query}' — no files found")

    # Step 4: Draft reply
    has_attachments_to_send = len(attachments) > 0
    draft_body = draft_reply(
        sender=email["sender"],
        subject=email["subject"],
        body=email["body"],
        classification=classification,
        calendar_slots=calendar_slots,
        attachment_names=attachment_names if attachment_names else None,
        thread_context=email.get("thread_context", ""),
        params=params,
        model=model,
    )

    if not draft_body:
        db.mark_processed(user_id, message_id, email["thread_id"])
        return {"message_id": message_id, "action": "error", "reason": "draft generation failed"}

    # Step 5: Route
    decision = route(
        classification=classification,
        autonomy_level=config["autonomy_level"],
        has_attachments_to_send=has_attachments_to_send,
        low_confidence_threshold=config["low_confidence_threshold"],
    )
    logger.info(f"  Routing: {decision.action} - {decision.reason}")

    # Step 6: Execute decision
    reply_subject = (
        email["subject"]
        if email["subject"].lower().startswith("re:")
        else f"Re: {email['subject']}"
    )
    sender_email = _extract_email(email["sender"])
    action_taken = decision.action

    if decision.action == "send":
        success = send_reply(
            gmail_service,
            thread_id=email["thread_id"],
            to=sender_email,
            subject=reply_subject,
            body=draft_body,
            attachments=attachments if attachments else None,
        )
        if not success:
            action_taken = "review"
            decision = type(decision)(action="review", reason="Send failed, queued for review")
        else:
            db.log_event(user_id, "sent", f"Auto-sent to {sender_email} — '{reply_subject}'")

    if action_taken in ("review",):
        db.log_event(user_id, "queued", f"Queued for review: '{email['subject']}' — {decision.reason}")
        db.add_to_review_queue(
            user_id=user_id,
            message_id=message_id,
            thread_id=email["thread_id"],
            sender=email["sender"],
            subject=email["subject"],
            snippet=email["snippet"],
            body=email["body"],
            draft_reply=draft_body,
            classification={
                **classification,
                "routing_reason": decision.reason,
                "has_attachments": has_attachments_to_send,
                "attachment_names": attachment_names,
            },
        )

    db.mark_processed(user_id, message_id, email["thread_id"])
    mark_as_read(gmail_service, message_id)

    return {
        "message_id": message_id,
        "action": action_taken,
        "reason": decision.reason,
        "sender": email["sender"],
        "subject": email["subject"],
    }


def _extract_email(sender: str) -> str:
    """Extract bare email from 'Name <email>' format."""
    import re
    match = re.search(r"<([^>]+)>", sender)
    return match.group(1) if match else sender


def _sender_name(sender: str) -> str:
    """Extract display name from 'Name <email>' format, fall back to email."""
    import re
    match = re.match(r"^([^<]+)<", sender)
    return match.group(1).strip() if match else sender.split("@")[0]
