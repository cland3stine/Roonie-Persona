from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.roonie.types import DecisionRecord, Env, Event
from src.providers.router import route_generate
from src.providers.registry import ProviderRegistry

@dataclass
class LiveDirector:
    registry: ProviderRegistry
    routing_cfg: Dict[str, Any]

    def evaluate(self, event: Event, env: Env) -> DecisionRecord:
        # For Phase 11B: read-only decisions. No posting.
        # We only generate candidate text (can be None). Presence/activation can be added later.
        prompt = event.message
        context: Dict[str, Any] = {"case_id": event.metadata.get("case_id"), "event_id": event.event_id}
        out = route_generate(
            registry=self.registry,
            routing_cfg=self.routing_cfg,
            prompt=prompt,
            context=context,
        )
        # Action is "observe" because we are not posting in Phase 11B
        return DecisionRecord(
            case_id=str(event.metadata.get("case_id", "live")),
            event_id=event.event_id,
            action="observe",
            route=f"primary:{self.registry.get_default().name}",
            response_text=out,
            trace={"mode": "live_readonly"},
        )
