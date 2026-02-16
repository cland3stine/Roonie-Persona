from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

from providers.registry import ProviderRegistry
from providers.router import route_generate
from roonie.context.context_buffer import ContextBuffer
from roonie.prompting import build_roonie_prompt
from roonie.types import DecisionRecord, Env, Event


def senses_allowed(runtime_context: Dict[str, Any] | None = None) -> bool:
    _ = runtime_context
    return False


@dataclass
class LiveDirector:
    registry: ProviderRegistry
    routing_cfg: Dict[str, Any]
    context_buffer: ContextBuffer = field(default_factory=lambda: ContextBuffer(max_turns=3))
    _session_id: str = field(default="", init=False, repr=False)

    @staticmethod
    def _is_direct_address(event: Event) -> bool:
        if bool(event.metadata.get("is_direct_mention")):
            return True
        msg = (event.message or "").strip().lower()
        return "@roonie" in msg or msg.startswith("roonie")

    def evaluate(self, event: Event, env: Env) -> DecisionRecord:
        # For Phase 11B: read-only decisions. No posting.
        # We only generate candidate text (can be None). Presence/activation can be added later.
        session_id = str(event.metadata.get("session_id", "")).strip()
        if session_id and session_id != self._session_id:
            self.context_buffer.clear()
            self._session_id = session_id

        prompt_context_turns = self.context_buffer.get_context(max_turns=3)
        context_turns_used = len(prompt_context_turns)
        context_active = context_turns_used > 0

        prompt = build_roonie_prompt(
            message=event.message,
            metadata={
                "viewer": event.metadata.get("user", "viewer"),
                "channel": event.metadata.get("channel", ""),
            },
            context_turns=prompt_context_turns,
            max_context_turns=3,
            max_context_chars=480,
        )
        senses_ok = senses_allowed(
            {
                "mode": "live",
                "event_id": event.event_id,
                "session_id": session_id,
            }
        )

        context: Dict[str, Any] = {
            "case_id": event.metadata.get("case_id"),
            "event_id": event.event_id,
            "context_active": context_active,
            "context_turns_used": context_turns_used,
            "senses_allowed": senses_ok,
            "senses_ignored": (not senses_ok),
            "use_provider_config": True,
            "message_text": event.message,
            "category": str(event.metadata.get("category", "")).strip().lower(),
            "utility_source": str(event.metadata.get("utility_source", "")).strip().lower(),
        }
        out = route_generate(
            registry=self.registry,
            routing_cfg=self.routing_cfg,
            prompt=prompt,
            context=context,
        )
        active_provider = str(context.get("active_provider", self.registry.get_default().name))
        provider_block_reason = context.get("provider_block_reason")

        stored_user_turn = self.context_buffer.add_turn(
            speaker="user",
            text=event.message,
            tags={
                "direct_address": self._is_direct_address(event),
                "category": str(event.metadata.get("category", "")).strip().lower(),
            },
        )
        self.context_buffer.add_turn(
            speaker="roonie",
            text=str(out or ""),
            sent=bool(event.metadata.get("response_sent", False)),
            related_to_stored_user=stored_user_turn,
        )

        # Action is "observe" because we are not posting in Phase 11B
        return DecisionRecord(
            case_id=str(event.metadata.get("case_id", "live")),
            event_id=event.event_id,
            action="observe",
            route=f"primary:{active_provider}",
            response_text=out,
            trace={
                "mode": "live_readonly",
                "context_active": context_active,
                "context_turns_used": context_turns_used,
                "provider_block_reason": provider_block_reason,
                "senses_allowed": senses_ok,
                "senses_ignored": (not senses_ok),
                "routing": {
                    "routing_enabled": bool(context.get("routing_enabled", False)),
                    "routing_class": str(context.get("routing_class", "general")),
                    "provider_selected": str(context.get("provider_selected", active_provider)),
                    "moderation_provider_used": context.get("moderation_provider_used"),
                    "moderation_result": context.get("moderation_result"),
                    "override_mode": str(context.get("override_mode", "default")),
                },
            },
            context_active=context_active,
            context_turns_used=context_turns_used,
        )
