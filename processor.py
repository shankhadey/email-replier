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

from classifier import classify_email
from drafter import draft_reply
from autonomy_engine import route
from database import is_processed, mark_processed, add_to_review_queue
from gmail_client import send_reply, create_reply_draft, mark_as_read
from gcal_client import get_free_slots
from gdrive_client import search_and_attach, get_attachment_names
from config import load_config

logger = logging.getLogger(__name__)


def process_email(email: dict) -> dict:
    """
    Full pipeline for a single email.
    Returns a result dict with action taken.
    """
    config = load_config()
    message_id = email["id"]

    if is_processed(message_id):
        return {"message_id": message_id, "action": "skipped", "reason": "already processed"}

    logger.info(f"Processing: [{email['subject']}] from {email['sender']}")

    # Step 1: Classify
    classification = classify_email(
        sender=email["sender"],
        subject=email["subject"],
        body=email["body"],
        has_attachments=email["has_attachments"],
    )
    logger.info(f"  Classification: {classification}")

    # Step 2: Skip if no reply needed
    if not classification.get("needs_reply"):
        mark_processed(message_id, email["thread_id"])
        return {"message_id": message_id, "action": "skipped", "reason": "no reply needed"}

    # Step 3: Gather context
    calendar_slots: Optional[str] = None
    attachment_names: list[str] = []
    attachments: list[dict] = []

    if classification.get("needs_calendar"):
        calendar_slots = get_free_slots(tz_name=config.get("user_timezone", "America/Chicago"))
        logger.info(f"  Calendar slots: {repr(calendar_slots)}")

    if classification.get("needs_gdrive") and classification.get("gdrive_query"):
        query = classification["gdrive_query"]
        attachments = search_and_attach(query)
        attachment_names = [a["filename"] for a in attachments]
        logger.info(f"  Drive attachments: {attachment_names}")

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
    )

    if not draft_body:
        mark_processed(message_id, email["thread_id"])
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
            thread_id=email["thread_id"],
            to=sender_email,
            subject=reply_subject,
            body=draft_body,
            attachments=attachments if attachments else None,
        )
        if not success:
            action_taken = "review"
            decision = type(decision)(action="review", reason="Send failed, queued for review")

    if action_taken in ("review",):
        # Add to review queue (this also covers cases where send failed)
        add_to_review_queue(
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

    mark_processed(message_id, email["thread_id"])
    mark_as_read(message_id)

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
