"""
Background setup: runs once after a new user authorises.
  1. _generate_voice_profile — analyses sent emails, builds AI voice traits
  2. _analyze_contacts — classifies top recipients for drafting context

Both steps are isolated: failure in one does not block the other.
"""

import asyncio
import logging
import os
import time
from collections import Counter
from typing import Optional

import anthropic

import auth
import database as db
from gmail_client import fetch_sent_emails

logger = logging.getLogger(__name__)


async def run_setup(user_id: str) -> None:
    """Async entry point — offloads to a thread to avoid blocking the event loop."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _run_setup_sync, user_id)


def _run_setup_sync(user_id: str) -> None:
    logger.info(f"[{user_id}] Background setup starting...")
    db.log_event(user_id, "setup_start", "Profile setup started")

    try:
        _generate_voice_profile(user_id)
    except Exception as e:
        logger.warning(f"[{user_id}] Voice profile failed: {e}")
        db.log_event(user_id, "setup_warning", f"Voice profile failed: {e}")

    try:
        _analyze_contacts(user_id)
    except Exception as e:
        logger.warning(f"[{user_id}] Contact analysis failed: {e}")
        db.log_event(user_id, "setup_warning", f"Contact analysis failed: {e}")

    db.set_setup_status(user_id, "complete")
    db.log_event(user_id, "setup_complete", "Profile setup finished")
    logger.info(f"[{user_id}] Background setup complete.")


# ── Voice profile ──────────────────────────────────────────────────────────────

def _generate_voice_profile(user_id: str) -> None:
    """
    Analyse up to 100 sent emails to infer writing style traits.
    Saves result into user's params under voice_profile.traits.
    """
    gmail_service = auth.get_gmail_service(user_id)

    emails = fetch_sent_emails(gmail_service, max_results=100, days=30, headers_only=False)
    if len(emails) < 20:
        # Expand to 90 days if too few samples
        emails = fetch_sent_emails(gmail_service, max_results=100, days=90, headers_only=False)
    if not emails:
        logger.info(f"[{user_id}] No sent emails found — skipping voice profile")
        return

    # Build a compact sample: subject + first 500 chars of body
    samples = []
    for e in emails[:100]:
        subject = e.get("subject", "")
        body = (e.get("body") or "")[:500]
        samples.append(f"Subject: {subject}\n{body.strip()}")

    sample_text = "\n\n---\n\n".join(samples[:50])  # cap at 50 for prompt size

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    prompt = f"""Analyse these {len(samples)} email excerpts written by the same person.
Identify 5-8 concise bullet-point traits that describe their email writing style and voice.
Focus on: tone, length preference, formality, sign-off style, punctuation habits, common phrases.
Output ONLY the bullet points, one per line, starting with "- ". No intro, no outro.

Emails:
{sample_text}"""

    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )
    traits_text = response.content[0].text.strip()
    logger.info(f"[{user_id}] Voice traits generated: {repr(traits_text[:120])}")

    # Merge into existing params
    params = db.load_user_params(user_id)
    if "voice_profile" not in params:
        params["voice_profile"] = {}
    params["voice_profile"]["traits"] = traits_text
    db.save_user_params(user_id, params)
    db.log_event(user_id, "setup_voice", "Writing style analysed and saved")


# ── Contact analysis ───────────────────────────────────────────────────────────

def _analyze_contacts(user_id: str) -> None:
    """
    Fetch up to 500 sent email headers, tally top 20 recipients,
    then classify each with Claude and save to user_contacts.
    """
    gmail_service = auth.get_gmail_service(user_id)

    # headers_only=True is fast — just To + snippet
    emails = fetch_sent_emails(gmail_service, max_results=500, days=365, headers_only=True)
    if not emails:
        logger.info(f"[{user_id}] No sent emails — skipping contact analysis")
        return

    # Tally recipients
    to_counts: Counter = Counter()
    for e in emails:
        to_field = e.get("to", "")
        # Handle comma-separated recipients
        for addr in to_field.split(","):
            addr = addr.strip()
            if addr:
                to_counts[addr] += 1

    top20 = [addr for addr, _ in to_counts.most_common(20)]
    if not top20:
        return

    # Build a compact contact list for classification
    contact_lines = "\n".join(f"{i+1}. {addr} (sent {to_counts[addr]}x)" for i, addr in enumerate(top20))

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    prompt = f"""Classify these email contacts based on their email addresses and frequency.
For each, determine:
- relationship_type: "recruiter" | "colleague" | "manager" | "vendor" | "personal" | "unknown"
- formality_level: "formal" | "semi-formal" | "casual"

Return ONLY a JSON array (no markdown), one object per contact:
[
  {{"email": "addr@example.com", "name": null, "relationship_type": "...", "formality_level": "..."}}
]

Contacts:
{contact_lines}"""

    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )

    import json, re
    raw = response.content[0].text.strip()
    # Strip markdown fences if present
    raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("```").strip()

    try:
        contacts = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning(f"[{user_id}] Contact JSON parse error: {e}")
        return

    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    saved = 0
    for c in contacts:
        if not isinstance(c, dict) or not c.get("email"):
            continue
        db.upsert_contact(
            user_id=user_id,
            email=c["email"],
            name=c.get("name"),
            relationship_type=c.get("relationship_type"),
            formality_level=c.get("formality_level"),
            interaction_count=to_counts.get(c["email"], 0),
            last_contact_at=now_iso,
        )
        saved += 1

    logger.info(f"[{user_id}] {saved} contacts saved")
    db.log_event(user_id, "setup_contacts", f"{saved} contacts analysed and saved")
