from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

Action = Literal["NOOP", "RESPOND_PUBLIC", "MEMORY_WRITE_INTENT"]
Route = Literal[
    "none",
    "responder:neutral_ack",
    "responder:clarify",
    "responder:refusal",
    "responder:policy_safe_info",
]
TriggerType = Literal["direct_question", "direct_request", "safety_issue", "banter", "unknown"]
SafetyClassification = Literal["allowed", "refuse", "sensitive_no_followup", "unknown"]


@dataclass
class Event:
    event_id: str
    message: str
    actor: str = "viewer"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Env:
    offline: bool = True
    stream_state: str = "online"


@dataclass
class DecisionRecord:
    case_id: str
    event_id: str
    action: Action
    route: Route
    response_text: Optional[str]
    trace: Dict[str, Any]
