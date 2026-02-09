from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class PresenceDecision:
    allowed: bool
    lane: str
    reason: str


def decide_presence(cfg: Dict[str, Any]) -> PresenceDecision:
    """
    Phase 10G: Presence policy (balanced visibility).

    Inputs (fixture-driven):
      - budget: {max_per_hour, used}
      - cooldown_seconds: int
      - lane: "ambient" | "named" | ...
      - named_budget: {max_per_hour, used}
      - event: {type: ...}

    Output:
      - PresenceDecision(allowed, lane, reason)

    No side effects. No time dependency. Pure decision function.
    """
    lane = str(cfg.get("lane", "ambient")).strip().lower()

    budget = cfg.get("budget", {}) or {}
    max_per_hour = int(budget.get("max_per_hour", 0))
    used = int(budget.get("used", 0))
    if max_per_hour >= 0 and used >= max_per_hour and max_per_hour != 0:
        return PresenceDecision(False, lane, "Budget exhausted")

    cooldown_seconds = int(cfg.get("cooldown_seconds", 0))
    if cooldown_seconds > 0:
        return PresenceDecision(False, lane, "Cooldown active")

    if lane == "named":
        nb = cfg.get("named_budget", {}) or {}
        nb_max = int(nb.get("max_per_hour", 0))
        nb_used = int(nb.get("used", 0))
        if nb_max >= 0 and nb_used >= nb_max and nb_max != 0:
            return PresenceDecision(False, lane, "Named commentary budget exhausted")

    return PresenceDecision(True, lane, "Allowed")
