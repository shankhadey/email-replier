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

logger = logging.getLogger(__name__)

VOICE_PROFILE = """
Shankha's email voice:
- Direct, no fluff, gets to the point immediately
- Short sentences and paragraphs
- Casual with known contacts ("Sure man", "sounds good"), professional but warm otherwise
- Never over-explains or adds unnecessary context
- Never starts with "I hope this email finds you well" or similar filler
- Signs off as just "Shankha" or nothing at all
- Uses natural, conversational language
- When sharing availability: list dates with times like "2/18: 12-6pm" (no extra text needed)
- When attaching a document: brief acknowledgment, nothing more

Example replies:
- "Hi Alex, Thanks for getting back. You can call me at 4255914898. Here are times I can make this week:\n\n2/18: 12-6pm\n2/19: 10:30-11am, 1:30-6pm\n\nShankha"
- "I can talk at 10:30am today. Will keep an eye out for your call."
- "Sure man, sounds similar to the last one I did with you"
- "Here's my resume. Down with fever so expect some delays in replies."
"""

SYSTEM_PROMPT = f"""You are drafting an email reply on behalf of Shankha Dey.

{VOICE_PROFILE}

Rules:
1. Write the reply body ONLY, no subject line, no "Subject:" prefix
2. Match the tone to the relationship (casual for known contacts, professional for unknowns)
3. Be concise: if it can be said in one sentence, use one sentence
4. If calendar availability is provided, include it exactly as formatted (don't reformat it)
5. If attachment context is provided, mention the attachment naturally and briefly
6. Never add platitudes, filler phrases, or unnecessary sign-off lines beyond "Shankha"
7. If the email doesn't need a substantive reply, write a minimal acknowledgment
"""


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
                system=SYSTEM_PROMPT,
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
