from __future__ import annotations

from typing import Optional

from .types import DecisionRecord, Event


_RESPONSES = {
    "responder:neutral_ack": "Got it.",
    "responder:clarify": "Quick check—are you asking me, and what exactly do you mean?",
    "responder:refusal": "Can’t help with that.",
    "responder:policy_safe_info": "Camera: (configured gear).",
}


def respond(route: str, event: Event, decision: Optional[DecisionRecord]) -> Optional[str]:
    return _RESPONSES.get(route)
