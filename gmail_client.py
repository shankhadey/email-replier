"""
Gmail operations: fetch unread emails, create drafts, send replies.
"""

import base64
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from typing import Optional

from auth import get_gmail_service

logger = logging.getLogger(__name__)

# Labels to skip - automated/promotional senders
SKIP_LABELS = {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_UPDATES", "CATEGORY_FORUMS"}


def fetch_unread_emails(max_results: int = 20) -> list[dict]:
    """Fetch unread emails from inbox, excluding automated categories."""
    service = get_gmail_service()
    try:
        result = service.users().messages().list(
            userId="me",
            q="is:unread in:inbox -category:promotions -category:social -category:updates",
            maxResults=max_results,
        ).execute()

        messages = result.get("messages", [])
        emails = []
        for msg in messages:
            detail = service.users().messages().get(
                userId="me", id=msg["id"], format="full"
            ).execute()

            label_ids = set(detail.get("labelIds", []))
            if label_ids & SKIP_LABELS:
                continue

            parsed = _parse_message(detail)
            if parsed:
                emails.append(parsed)

        return emails
    except Exception as e:
        logger.error(f"Error fetching emails: {e}")
        return []


def _parse_message(detail: dict) -> Optional[dict]:
    headers = {h["name"]: h["value"] for h in detail["payload"].get("headers", [])}
    sender = headers.get("From", "")
    subject = headers.get("Subject", "(no subject)")
    message_id = headers.get("Message-ID", "")
    in_reply_to = headers.get("In-Reply-To", "")
    date = headers.get("Date", "")

    body = _extract_body(detail["payload"])
    if not body.strip():
        return None

    return {
        "id": detail["id"],
        "thread_id": detail["threadId"],
        "sender": sender,
        "subject": subject,
        "message_id_header": message_id,
        "in_reply_to": in_reply_to,
        "date": date,
        "snippet": detail.get("snippet", ""),
        "body": body[:4000],  # cap for LLM context
        "has_attachments": _has_attachments(detail["payload"]),
    }


def _extract_body(payload: dict) -> str:
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    if payload.get("mimeType") == "text/html":
        data = payload.get("body", {}).get("data", "")
        if data:
            # strip basic HTML tags
            import re
            text = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            return re.sub(r"<[^>]+>", " ", text)

    for part in payload.get("parts", []):
        result = _extract_body(part)
        if result:
            return result

    return ""


def _has_attachments(payload: dict) -> bool:
    for part in payload.get("parts", []):
        if part.get("filename") and part["filename"].strip():
            return True
        if _has_attachments(part):
            return True
    return False


def create_reply_draft(
    thread_id: str,
    to: str,
    subject: str,
    body: str,
    attachments: Optional[list[dict]] = None,
) -> Optional[str]:
    """Create a draft reply. Returns draft ID."""
    service = get_gmail_service()
    msg = _build_message(to, subject, body, attachments)
    msg["threadId"] = thread_id

    try:
        draft = service.users().drafts().create(
            userId="me", body={"message": msg}
        ).execute()
        logger.info(f"Draft created: {draft['id']}")
        return draft["id"]
    except Exception as e:
        logger.error(f"Error creating draft: {e}")
        return None


def send_reply(
    thread_id: str,
    to: str,
    subject: str,
    body: str,
    attachments: Optional[list[dict]] = None,
) -> bool:
    """Send a reply directly. Returns success bool."""
    service = get_gmail_service()
    msg = _build_message(to, subject, body, attachments)
    msg["threadId"] = thread_id

    try:
        service.users().messages().send(userId="me", body=msg).execute()
        logger.info(f"Email sent to {to}")
        return True
    except Exception as e:
        logger.error(f"Error sending email: {e}")
        return False


def _build_message(
    to: str,
    subject: str,
    body: str,
    attachments: Optional[list[dict]] = None,
) -> dict:
    """Build a base64-encoded email message dict."""
    if attachments:
        mime = MIMEMultipart()
        mime.attach(MIMEText(body, "plain"))
        for att in attachments:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(att["data"])
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                f'attachment; filename="{att["filename"]}"',
            )
            mime.attach(part)
        mime["to"] = to
        mime["subject"] = subject
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
    else:
        mime = MIMEText(body, "plain")
        mime["to"] = to
        mime["subject"] = subject
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()

    return {"raw": raw}


def mark_as_read(message_id: str):
    """Remove UNREAD label from a message."""
    service = get_gmail_service()
    try:
        service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()
    except Exception as e:
        logger.error(f"Error marking as read: {e}")
