from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class StatusResponse:
    kill_switch_on: bool
    armed: bool
    mode: str
    twitch_connected: bool
    last_heartbeat_at: Optional[str]
    active_provider: str
    version: str
    policy_loaded_at: Optional[str]
    policy_version: Optional[str]
    context_last_active: bool
    context_last_turns_used: int
    silenced: bool = False
    silence_until: Optional[str] = None
    read_only_mode: bool = True
    can_post: bool = False
    blocked_by: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EventResponse:
    ts: Optional[str]
    user_handle: str
    message_text: str
    direct_address: bool
    decision_type: str
    final_text: Optional[str]
    decision: Optional[str]
    suppression_reason: Optional[str]
    suppression_detail: Optional[str]
    context_active: bool
    context_turns_used: int

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        # Legacy alias for frozen UI compatibility.
        payload["decision"] = payload.get("final_text")
        return payload


@dataclass
class OperatorLogResponse:
    ts: str
    operator: str
    action: str
    payload_summary: Optional[str] = None
    result: Optional[str] = None
    actor: str = "unknown"
    username: Optional[str] = None
    role: Optional[str] = None
    auth_mode: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def serialize_many(items: List[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in items:
        if hasattr(item, "to_dict"):
            out.append(item.to_dict())
        elif isinstance(item, dict):
            out.append(item)
    return out
