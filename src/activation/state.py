from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class ActivationDecision:
    allowed: bool
    lane: str
    reason: str


def decide_activation(system: Dict[str, Any], presence_decision: Dict[str, Any]) -> ActivationDecision:
    """
    Phase 10H: Activation layer (operator UX plumbing).

    Inputs:
      system:
        - armed: bool
        - kill_switch: bool
        - presence_mode: "normal" | "silent" | ...
      presence_decision:
        - allowed: bool
        - lane: str

    Priority (hard overrides first):
      1) kill_switch -> deny
      2) presence_mode == "silent" -> deny
      3) not armed -> deny
      4) presence allowed? if no -> deny
      5) else -> allow
    """
    lane = str(presence_decision.get("lane", "ambient")).strip().lower()

    if bool(system.get("kill_switch", False)):
        return ActivationDecision(False, lane, "Kill switch is ON")

    mode = str(system.get("presence_mode", "normal")).strip().lower()
    if mode == "silent":
        return ActivationDecision(False, lane, "Presence mode is silent")

    if not bool(system.get("armed", False)):
        return ActivationDecision(False, lane, "System is disarmed")

    if not bool(presence_decision.get("allowed", False)):
        return ActivationDecision(False, lane, "Presence denied speaking")

    return ActivationDecision(True, lane, "Armed and presence allowed")
