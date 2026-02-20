"""
Classify incoming emails using Claude.
Returns structured classification dict.
"""

import json
import logging
import os
import re
import time
from typing import Optional

import anthropic
from config import load_config

logger = logging.getLogger(__name__)

CLASSIFICATION_SCHEMA = """
Return ONLY valid JSON matching this schema (no markdown, no explanation):
{
  "needs_reply": true | false,
  "sender_priority": "high" | "medium" | "low" | "unknown",
  "confidence": 0.0 - 1.0,
  "is_critical": true | false,
  "needs_calendar": true | false,
  "needs_gdrive": true | false,
  "gdrive_query": "string or null",
  "reasoning": "one sentence"
}

Definitions:
- needs_reply: Does this email warrant a reply from Shankha?
- sender_priority: "high" = executives/recruiters/close collaborators/important business contacts; "medium" = colleagues/vendors; "low" = mailing lists/newsletters/low priority; "unknown" = first-time or unrecognized sender.
- confidence: How confident are you in your classification (0 = very unsure, 1 = certain)?
- is_critical: Time-sensitive, financial, legal, job offer, urgent decision required?
- needs_calendar: Does the email ask for Shankha's availability or to schedule a meeting?
- needs_gdrive: Does the email ask for a document (resume, proposal, report, etc)?
- gdrive_query: If needs_gdrive=true, what search query to use in Drive (e.g. "resume", "Q3 proposal")? Otherwise null.
- reasoning: Brief reason for your classification.
"""

SYSTEM_PROMPT = f"""You are an email classifier for Shankha Dey, a Senior Director of Product Management specializing in AI/ML. 
Your job is to analyze incoming emails and classify them accurately.

Context about Shankha:
- Works at Salesforce Data Cloud on AI/ML initiatives
- Actively interviewing for VP-level PM roles
- Has a spouse named Priya (real estate agent)
- Receives recruiter emails, executive communications, technical collaboration requests

{CLASSIFICATION_SCHEMA}
"""


def classify_email(
    sender: str,
    subject: str,
    body: str,
    has_attachments: bool,
) -> dict:
    """Classify an email and return structured classification."""
    config = load_config()
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    user_prompt = f"""Classify this email:

From: {sender}
Subject: {subject}
Has Attachments: {has_attachments}

Body:
{body[:2000]}
"""

    max_retries = 3
    last_error = None
    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model=config["anthropic_model"],
                max_tokens=512,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )

            raw = response.content[0].text.strip()
            # Strip markdown code fences if present
            raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("```").strip()
            result = json.loads(raw)

            # Validate required keys
            required = ["needs_reply", "sender_priority", "confidence", "is_critical",
                        "needs_calendar", "needs_gdrive"]
            for key in required:
                if key not in result:
                    raise ValueError(f"Missing key: {key}")

            return result

        except anthropic.APIStatusError as e:
            if e.status_code == 529 and attempt < max_retries - 1:
                wait = 10 * (attempt + 1)
                logger.warning(f"Anthropic overloaded (529), retrying in {wait}s... (attempt {attempt + 1}/{max_retries})")
                time.sleep(wait)
                last_error = e
            else:
                last_error = e
                break
        except Exception as e:
            last_error = e
            break

    logger.error(f"Classification error: {last_error}")
    # Safe fallback: treat as needing review
    return {
        "needs_reply": True,
        "sender_priority": "unknown",
        "confidence": 0.0,
        "is_critical": False,
        "needs_calendar": False,
        "needs_gdrive": False,
        "gdrive_query": None,
        "reasoning": f"Classification failed: {str(last_error)}",
    }
