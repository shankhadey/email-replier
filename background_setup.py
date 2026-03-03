"""
Background setup: runs once after a new user authorises.
  1. _generate_voice_profile — analyses sent emails, builds AI voice traits
  2. _analyze_contacts — classifies top recipients for drafting context

Both steps are isolated: failure in one does not block the other.
"""

import asyncio
import json
import logging
import os
import re
import time
from collections import Counter
from typing import Optional

import anthropic

import auth
import database as db
from gmail_client import fetch_sent_emails


def _bare_email(addr: str) -> str:
    """Extract bare email address from 'Name <email>' or plain email."""
    m = re.search(r"<([^>]+)>", addr)
    return (m.group(1) if m else addr).strip().lower()

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
    traits = [line.lstrip("- ").strip() for line in traits_text.splitlines() if line.strip()]
    params["voice_profile"]["traits"] = traits
    db.save_user_params(user_id, params)
    db.log_event(user_id, "setup_voice", "Writing style analysed and saved")


# ── Contact analysis ───────────────────────────────────────────────────────────

def _analyze_contacts(user_id: str) -> None:
    """
    Fetch up to 500 sent email headers, tally top 20 recipients,
    classify each with Claude, extract per-contact topics from both
    sent and received email subjects, compute priority scores,
    and save everything to user_contacts.
    """
    gmail_service = auth.get_gmail_service(user_id)

    # headers_only=True is fast — just To + snippet
    emails = fetch_sent_emails(gmail_service, max_results=500, days=365, headers_only=True)
    if not emails:
        logger.info(f"[{user_id}] No sent emails — skipping contact analysis")
        return

    # Tally recipients by bare email, collect display names
    to_counts: Counter = Counter()
    to_names: dict = {}  # bare_email -> display name
    for e in emails:
        to_field = e.get("to", "")
        for raw_addr in to_field.split(","):
            raw_addr = raw_addr.strip()
            if not raw_addr:
                continue
            bare = _bare_email(raw_addr)
            if bare:
                to_counts[bare] += 1
                if "<" in raw_addr and bare not in to_names:
                    name_part = raw_addr.split("<")[0].strip().strip('"')
                    if name_part:
                        to_names[bare] = name_part

    top20 = [addr for addr, _ in to_counts.most_common(20)]
    if not top20:
        return

    # Build contact list for classification (include display names when available)
    contact_lines_parts = []
    for i, addr in enumerate(top20):
        display = f"{to_names[addr]} <{addr}>" if addr in to_names else addr
        contact_lines_parts.append(f"{i+1}. {display} (sent {to_counts[addr]}x)")
    contact_lines = "\n".join(contact_lines_parts)

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    prompt = f"""Classify these email contacts based on their email addresses, names, and frequency.
For each, determine:
- relationship_type: "recruiter" | "colleague" | "manager" | "vendor" | "personal" | "unknown"
- formality_level: "formal" | "semi-formal" | "casual"

Return ONLY a JSON array (no markdown), one object per contact, using the bare email address:
[
  {{"email": "addr@example.com", "name": "Display Name or null", "relationship_type": "...", "formality_level": "..."}}
]

Contacts:
{contact_lines}"""

    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("```").strip()

    try:
        contacts = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning(f"[{user_id}] Contact JSON parse error: {e}")
        return

    # ── Topic extraction ────────────────────────────────────────────────────
    # Build a set of bare emails for fast lookup
    top20_set = set(top20)

    # Step A: collect subjects from sent emails already in memory
    contact_subjects: dict = {addr: [] for addr in top20}
    for e in emails:
        subj = e.get("subject", "").strip()
        if not subj:
            continue
        to_field = e.get("to", "")
        for raw_addr in to_field.split(","):
            bare = _bare_email(raw_addr.strip())
            if bare in top20_set:
                contact_subjects[bare].append(subj)

    # Step B: fetch subjects from received emails (what top contacts sent you)
    after_epoch = int(time.time()) - 365 * 86400
    from_query = " OR ".join(f"from:{addr}" for addr in top20)
    try:
        inbox_result = gmail_service.users().messages().list(
            userId="me",
            q=f"({from_query}) after:{after_epoch}",
            maxResults=300,
        ).execute()
        inbox_msgs = inbox_result.get("messages", [])
        for msg in inbox_msgs:
            detail = gmail_service.users().messages().get(
                userId="me", id=msg["id"], format="metadata",
                metadataHeaders=["From", "Subject"]
            ).execute()
            headers = {h["name"]: h["value"]
                       for h in detail.get("payload", {}).get("headers", [])}
            raw_from = headers.get("From", "")
            subj = headers.get("Subject", "").strip()
            if not subj:
                continue
            bare = _bare_email(raw_from)
            if bare in top20_set:
                contact_subjects[bare].append(subj)
        logger.info(f"[{user_id}] Fetched {len(inbox_msgs)} inbox messages for topic extraction")
    except Exception as e:
        logger.warning(f"[{user_id}] Inbox fetch for topics failed (non-fatal): {e}")

    # Step C: deduplicate + cap at 12 subjects per contact
    for addr in contact_subjects:
        seen = set()
        deduped = []
        for s in contact_subjects[addr]:
            clean = re.sub(r"^(Re:|Fwd?:)\s*", "", s, flags=re.IGNORECASE).strip().lower()
            if clean and clean not in seen:
                seen.add(clean)
                deduped.append(s)
        contact_subjects[addr] = deduped[:12]

    # Batched haiku call to extract topics
    topics_map: dict = {}
    topic_lines = []
    for addr in top20:
        subjs = contact_subjects[addr]
        subj_text = "; ".join(subjs) if subjs else "(no subjects found)"
        topic_lines.append(f"{addr}: {subj_text}")

    try:
        topics_prompt = (
            "Extract 3-5 short topic keywords/phrases describing the key work or relationship "
            "topics for each person, based on their email subjects (both sent and received). "
            "Return ONLY a JSON object mapping email→array of topic strings, no markdown.\n\n"
            + "\n".join(topic_lines)
        )
        topics_resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            messages=[{"role": "user", "content": topics_prompt}],
        )
        topics_raw = topics_resp.content[0].text.strip()
        topics_raw = re.sub(r"```(?:json)?", "", topics_raw).strip().rstrip("```").strip()
        topics_map = json.loads(topics_raw)
    except Exception as e:
        logger.warning(f"[{user_id}] Topic extraction failed (non-fatal): {e}")

    # ── Priority score computation ──────────────────────────────────────────
    relationship_weights = {
        "personal": 0.90, "manager": 0.85, "colleague": 0.70,
        "recruiter": 0.50, "vendor": 0.40, "unknown": 0.20,
    }
    max_count = max(to_counts.values()) if to_counts else 1

    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    saved = 0
    for c in contacts:
        if not isinstance(c, dict) or not c.get("email"):
            continue
        bare = c["email"].lower()
        count = to_counts.get(bare, 0)
        rel = c.get("relationship_type", "unknown")
        rel_weight = relationship_weights.get(rel, 0.20)
        interaction_score = count / max_count
        priority_score = round(0.45 * interaction_score + 0.55 * rel_weight, 3)

        contact_topics = topics_map.get(bare, topics_map.get(c["email"], []))
        topics_json = json.dumps(contact_topics) if contact_topics else None

        db.upsert_contact(
            user_id=user_id,
            email=bare,
            name=c.get("name") or to_names.get(bare),
            relationship_type=rel,
            formality_level=c.get("formality_level"),
            interaction_count=count,
            last_contact_at=now_iso,
            topics=topics_json,
            priority_score=priority_score,
        )
        saved += 1

    logger.info(f"[{user_id}] {saved} contacts saved with topics and priority scores")
    db.log_event(user_id, "setup_contacts", f"{saved} contacts analysed and saved")
