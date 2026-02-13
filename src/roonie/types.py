from __future__ import annotations

from dataclasses import dataclass, field, fields
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
    context_active: bool = False
    context_turns_used: int = 0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DecisionRecord":
        allowed = {f.name for f in fields(cls)}
        filtered = {key: value for key, value in data.items() if key in allowed}
        return cls(**filtered)

    def to_dict(self, *, exclude_defaults: bool = False) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "case_id": self.case_id,
            "event_id": self.event_id,
            "action": self.action,
            "route": self.route,
            "response_text": self.response_text,
            "trace": self.trace,
            "context_active": self.context_active,
            "context_turns_used": self.context_turns_used,
        }
        if exclude_defaults:
            if payload["context_active"] is False:
                payload.pop("context_active", None)
            if payload["context_turns_used"] == 0:
                payload.pop("context_turns_used", None)
        return payload
