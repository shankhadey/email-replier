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
from params import load_params

logger = logging.getLogger(__name__)

CLASSIFICATION_SCHEMA = """
Return ONLY valid JSON matching this schema (no markdown, no explanation):
{
  "needs_reply": true | false,
  "sender_priority": "high" | "medium" | "low" | "unknown",
  "confidence": 0.0 - 1.0,
  "is_critical": true | false,
  "needs_calendar": true | false,
  "calendar_days_requested": integer or null,
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
- calendar_days_requested: If needs_calendar=true, how many days ahead is the email asking about? ("tomorrow"→2, "this week"→7, "next two weeks"→14, "this month"→30). Default 7 if unspecified. Otherwise null.
- needs_gdrive: Does the email ask for a document (resume, proposal, report, etc)?
- gdrive_query: If needs_gdrive=true, what search query to use in Drive (e.g. "resume", "Q3 proposal")? Otherwise null.
- reasoning: Brief reason for your classification.
"""

def _build_classifier_prompt(params: dict) -> str:
    """Build the classifier system prompt from behavior_params.json."""
    identity = params.get("user_identity", {})
    rules    = params.get("classification_rules", {})

    name    = identity.get("name", "the user")
    role    = identity.get("role", "")
    company = identity.get("company", "")
    focus   = identity.get("focus", "")
    context = "\n".join(f"- {c}" for c in identity.get("context", []))

    priority = "\n".join(
        f'  "{k}": {v}' for k, v in rules.get("sender_priority", {}).items()
    )
    critical = "\n".join(f"- {c}" for c in rules.get("is_critical_criteria", []))
    cal_trig = "\n".join(f"- {t}" for t in rules.get("needs_calendar_triggers", []))
    drv_trig = "\n".join(f"- {t}" for t in rules.get("needs_gdrive_triggers", []))
    skip     = "\n".join(f"- {t}" for t in rules.get("skip_triggers", []))

    return f"""You are an email classifier for {name}, {role} at {company} ({focus}).
Your job is to analyze incoming emails and classify them accurately.

Context about {name}:
{context}

sender_priority definitions:
{priority}

is_critical = true when any of:
{critical}

needs_calendar = true when:
{cal_trig}

needs_gdrive = true when:
{drv_trig}

needs_reply = false (skip without replying) when:
{skip}

{CLASSIFICATION_SCHEMA}"""


def classify_email(
    sender: str,
    subject: str,
    body: str,
    has_attachments: bool,
    params: dict = None,
    model: str = None,
) -> dict:
    """Classify an email and return structured classification."""
    if params is None:
        params = load_params()
    if model is None:
        model = load_config()["anthropic_model"]
    system_prompt = _build_classifier_prompt(params)
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
                model=model,
                max_tokens=512,
                system=system_prompt,
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
