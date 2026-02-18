"""
Autonomy Engine: decides whether to send, draft, or queue for review
based on the autonomy level and email classification.

Levels:
  1 = Always reviewed by user (everything goes to review queue as draft)
  2 = Review only: low confidence, critical, unknown sender, or attachment
  3 = Fully autonomous, EXCEPT: has_attachment, unknown sender always draft

Hard rules (override all levels):
  - Email has attachment to send: always draft (attachment emails need human eyes)
  - Unknown sender: always draft (no matter the level)
"""

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RoutingDecision:
    action: str        # "send", "draft", "review", "skip"
    reason: str


def route(
    classification: dict,
    autonomy_level: int,
    has_attachments_to_send: bool,
    low_confidence_threshold: float = 0.70,
) -> RoutingDecision:
    """Determine the action to take for a classified email."""

    needs_reply = classification.get("needs_reply", False)
    sender_priority = classification.get("sender_priority", "unknown")
    confidence = classification.get("confidence", 0.0)
    is_critical = classification.get("is_critical", False)

    # --- Hard rules ---
    if not needs_reply:
        return RoutingDecision("skip", "No reply needed")

    # Unknown sender: always draft regardless of level
    if sender_priority == "unknown":
        return RoutingDecision("review", "Unknown sender - always review")

    # If we're attaching a file: always draft
    if has_attachments_to_send:
        return RoutingDecision("review", "Email has Drive attachment - always review")

    is_low_confidence = confidence < low_confidence_threshold

    # --- Level 1: always review ---
    if autonomy_level == 1:
        return RoutingDecision("review", "Autonomy L1: all emails reviewed")

    # --- Level 2: review if any risk flag ---
    if autonomy_level == 2:
        if is_low_confidence:
            return RoutingDecision("review", f"Low confidence ({confidence:.0%}) - review required")
        if is_critical:
            return RoutingDecision("review", "Critical email - review required")
        return RoutingDecision("send", "High confidence, known sender, not critical")

    # --- Level 3: fully autonomous (except hard rules already caught above) ---
    if autonomy_level == 3:
        return RoutingDecision("send", "Autonomy L3: sending autonomously")

    # Fallback
    return RoutingDecision("review", "Unknown autonomy level - defaulting to review")
