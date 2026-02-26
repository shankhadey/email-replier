"""
Draft email replies in Shankha's voice using Claude.
Optionally injects calendar availability and GDrive attachment names.
"""

import logging
import os
import time
from typing import Optional

import anthropic
from config import load_config
from params import load_params

logger = logging.getLogger(__name__)

def _build_drafter_prompt(params: dict) -> str:
    """Build the drafter system prompt from behavior_params.json."""
    identity = params.get("user_identity", {})
    voice    = params.get("voice_profile", {})

    name     = identity.get("name", "the user")
    traits   = "\n".join(f"- {t}" for t in voice.get("traits", []))
    avail    = voice.get("availability_format", "")
    attach   = voice.get("attachment_format", "")
    examples = "\n".join(f'- "{e}"' for e in voice.get("examples", []))

    return f"""You are drafting an email reply on behalf of {name}.

{name}'s email voice:
{traits}
- When sharing availability: {avail}
- When attaching a document: {attach}

Example replies:
{examples}

Rules:
1. Write the reply body ONLY — no subject line, no "Subject:" prefix
2. Match tone to the relationship (casual for known contacts, professional for unknowns)
3. Be concise: if it can be said in one sentence, use one sentence
4. If calendar availability is provided, include it exactly as formatted (don't reformat it)
5. If attachment context is provided, mention the attachment naturally and briefly
6. Never add platitudes, filler phrases, or unnecessary sign-off lines beyond "{name}"
7. If the email doesn't need a substantive reply, write a minimal acknowledgment"""


def draft_reply(
    sender: str,
    subject: str,
    body: str,
    classification: dict,
    calendar_slots: Optional[str] = None,
    attachment_names: Optional[list[str]] = None,
    thread_context: Optional[str] = None,
) -> str:
    """Draft a reply in Shankha's voice."""
    config = load_config()
    system_prompt = _build_drafter_prompt(load_params())
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    context_parts = []
    if calendar_slots:
        context_parts.append(f"Calendar availability to include:\n{calendar_slots}")
    if attachment_names:
        context_parts.append(f"Attaching these files from Drive: {', '.join(attachment_names)}")

    context_block = ("\n\n" + "\n\n".join(context_parts)) if context_parts else ""

    thread_block = ""
    if thread_context:
        thread_block = f"\n\nPrior conversation context (for reference only, do not repeat):\n{thread_context[:1500]}"

    user_prompt = f"""Draft a reply to the latest email in this thread:

From: {sender}
Subject: {subject}
Sender priority: {classification.get('sender_priority', 'unknown')}

Latest email (reply to THIS one):
{body[:2000]}
{context_block}{thread_block}

Write the reply body only. Reply to the latest email above, not to earlier messages in the thread.
"""

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model=config["anthropic_model"],
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return response.content[0].text.strip()
        except anthropic.APIStatusError as e:
            if e.status_code == 529 and attempt < max_retries - 1:
                wait = 10 * (attempt + 1)
                logger.warning(f"Anthropic overloaded (529), retrying in {wait}s... (attempt {attempt + 1}/{max_retries})")
                time.sleep(wait)
            else:
                logger.error(f"Draft error after {attempt + 1} attempts: {e}")
                return ""
        except Exception as e:
            logger.error(f"Draft error: {e}")
            return ""
    return ""
