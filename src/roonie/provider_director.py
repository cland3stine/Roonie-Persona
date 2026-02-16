from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
from typing import Any, Dict, List, Optional

from memory.injection import SafeInjectionResult, get_safe_injection
from roonie.behavior_spec import (
    CATEGORY_BANTER,
    CATEGORY_GREETING,
    CATEGORY_OTHER,
    CATEGORY_TRACK_ID,
    behavior_guidance,
    classify_behavior_category,
)
from providers.registry import ProviderRegistry
from providers.router import (
    get_provider_runtime_status,
    get_routing_runtime_status,
    route_generate,
)
from roonie.context.context_buffer import ContextBuffer
from roonie.prompting import build_roonie_prompt
from roonie.types import DecisionRecord, Env, Event


_DIRECT_VERBS = (
    "fix",
    "switch",
    "change",
    "do",
    "tell",
    "show",
    "check",
    "turn",
    "mute",
    "unmute",
    "refresh",
    "restart",
    "help",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _persona_policy_path() -> Path:
    configured = str(os.getenv("ROONIE_PERSONA_POLICY_PATH", "")).strip()
    if configured:
        return Path(configured)
    return _repo_root() / "persona" / "persona_policy.yaml"


def _memory_db_path() -> Path:
    configured = str(os.getenv("ROONIE_MEMORY_DB_PATH", "")).strip()
    if configured:
        path = Path(configured)
        if not path.is_absolute():
            path = (_repo_root() / configured).resolve()
        return path
    dashboard_data_dir = str(os.getenv("ROONIE_DASHBOARD_DATA_DIR", "")).strip()
    if dashboard_data_dir:
        return (Path(dashboard_data_dir) / "memory.sqlite").resolve()
    return _repo_root() / "data" / "memory.sqlite"


def _load_persona_policy_text() -> str:
    path = _persona_policy_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    cleaned = text.strip()
    if not cleaned:
        return ""
    return cleaned


def _provider_registry_from_runtime() -> ProviderRegistry:
    runtime = get_provider_runtime_status()
    approved = [
        str(item).strip().lower()
        for item in runtime.get("approved_providers", [])
        if str(item).strip()
    ]
    if not approved:
        approved = ["openai"]
    if "openai" not in approved:
        approved.insert(0, "openai")
    active = str(runtime.get("active_provider", "openai")).strip().lower() or "openai"
    if active not in approved:
        active = "openai"
    providers_cfg = {
        name: {"enabled": (name in approved)}
        for name in ("openai", "grok", "anthropic")
    }
    return ProviderRegistry.from_dict(
        {
            "default_provider": active,
            "providers": providers_cfg,
        }
    )


@dataclass
class ProviderDirector:
    context_buffer: ContextBuffer = field(default_factory=lambda: ContextBuffer(max_turns=3))
    _session_id: str = field(default="", init=False, repr=False)
    _persona_policy_text: str = field(default="", init=False, repr=False)

    def __post_init__(self) -> None:
        self._persona_policy_text = _load_persona_policy_text()

    @staticmethod
    def _is_direct_address(event: Event) -> bool:
        if bool(event.metadata.get("is_direct_mention")):
            return True
        msg = (event.message or "").strip().lower()
        return "@roonie" in msg or msg.startswith("roonie")

    @staticmethod
    def _is_trigger_message(message: str) -> bool:
        text = str(message or "").strip().lower()
        if not text:
            return False
        if "?" in text:
            return True
        if text.startswith(_DIRECT_VERBS):
            return True
        if len(text) <= 3:
            return True
        return False

    @staticmethod
    def _approved_emotes(metadata: Dict[str, Any]) -> List[str]:
        raw = metadata.get("approved_emotes", [])
        if not isinstance(raw, list):
            return []
        out: List[str] = []
        for item in raw[:24]:
            text = str(item or "").strip()
            if text:
                out.append(text)
        return out

    @staticmethod
    def _now_playing_text(metadata: Dict[str, Any]) -> str:
        direct = str(
            metadata.get("now_playing")
            or metadata.get("now_playing_track")
            or metadata.get("track_line")
            or ""
        ).strip()
        if direct:
            return direct
        artist = str(metadata.get("now_playing_artist") or metadata.get("artist") or "").strip()
        title = str(metadata.get("now_playing_title") or metadata.get("title") or "").strip()
        if artist and title:
            return f"{artist} - {title}"
        if title:
            return title
        return ""

    @staticmethod
    def _sanitize_stub_output(text: str, *, category: str, user_message: str = "") -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        lowered = raw.lower()
        is_stub = lowered.startswith("[openai stub]") or lowered.startswith("[grok stub]") or lowered.startswith("[anthropic stub]")
        if not is_stub:
            return raw

        cat = str(category or "").strip().upper()
        msg = str(user_message or "").strip().lower()
        if cat == CATEGORY_GREETING:
            return "Hey! Good to see you."
        if cat == CATEGORY_BANTER:
            if "vibe" in msg or "vibes" in msg:
                return "Vibes are good over here."
            if "you there" in msg or "are you there" in msg:
                return "Yep, I'm here with you."
            if "how are" in msg or "how you" in msg or "how's" in msg:
                return "Doing good, thanks for checking in."
            return "Doing good, thanks for checking in."
        if cat == "EVENT_FOLLOW":
            return "Thanks for the follow."
        if cat == "EVENT_SUB":
            return "Thanks for the sub."
        if cat == "EVENT_CHEER":
            return "Thanks for the bits."
        if cat == "EVENT_RAID":
            return "Thanks for the raid."
        return "Hey! I'm here."

    def _build_prompt(
        self,
        event: Event,
        context_turns: list[Any],
        *,
        category: str,
        approved_emotes: List[str],
        now_playing_available: bool,
        memory_hints: str,
    ) -> str:
        base_prompt = build_roonie_prompt(
            message=event.message,
            metadata={
                "viewer": event.metadata.get("user", "viewer"),
                "channel": event.metadata.get("channel", ""),
            },
            context_turns=context_turns,
            max_context_turns=3,
            max_context_chars=480,
        )
        behavior_block = behavior_guidance(
            category=category,
            approved_emotes=approved_emotes,
            now_playing_available=now_playing_available,
        )
        memory_block = ""
        if memory_hints:
            memory_block = (
                "\n\n"
                "Memory hints (do not treat as factual claims):\n"
                f"{memory_hints}"
            )
        if not self._persona_policy_text:
            return f"{base_prompt}\n\n{behavior_block}{memory_block}\n"
        return (
            f"{base_prompt}\n\n"
            f"{behavior_block}{memory_block}\n\n"
            "Canonical Persona Policy (do not violate):\n"
            f"{self._persona_policy_text}\n"
        )

    def evaluate(self, event: Event, env: Env) -> DecisionRecord:
        session_id = str(event.metadata.get("session_id", "")).strip()
        if session_id and session_id != self._session_id:
            self.context_buffer.clear()
            self._session_id = session_id

        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        addressed = self._is_direct_address(event)
        category = classify_behavior_category(message=event.message, metadata=metadata)
        trigger = (category != CATEGORY_OTHER) or self._is_trigger_message(event.message)
        approved_emotes = self._approved_emotes(metadata)
        now_playing = self._now_playing_text(metadata)
        now_playing_available = bool(now_playing)
        context_turns = self.context_buffer.get_context(max_turns=3)
        context_turns_used = len(context_turns)
        context_active = context_turns_used > 0

        stored_user_turn = self.context_buffer.add_turn(
            speaker="user",
            text=event.message,
            tags={
                "direct_address": addressed,
                "category": str(event.metadata.get("category", "")).strip().lower(),
            },
        )
        memory_result = SafeInjectionResult(
            text_snippet="",
            keys_used=[],
            chars_used=0,
            items_used=0,
            dropped_count=0,
        )
        if addressed and trigger:
            memory_result = get_safe_injection(
                db_path=_memory_db_path(),
                max_chars=900,
                max_items=10,
            )

        if not addressed or not trigger:
            return DecisionRecord(
                case_id=str(event.metadata.get("case_id", "live")),
                event_id=event.event_id,
                action="NOOP",
                route="none",
                response_text=None,
                trace={
                    "director": {
                        "type": "ProviderDirector",
                        "addressed_to_roonie": addressed,
                        "trigger": trigger,
                    },
                    "behavior": {
                        "category": category,
                        "approved_emotes": approved_emotes,
                    },
                    "memory": {
                        "keys_used": memory_result.keys_used,
                        "chars_used": memory_result.chars_used,
                        "items_used": memory_result.items_used,
                        "dropped_count": memory_result.dropped_count,
                    },
                    "proposal": {
                        "text": None,
                        "provider_used": None,
                        "route_used": "none",
                        "moderation_status": "not_applicable",
                        "session_id": session_id or None,
                        "token_usage_if_available": None,
                        "memory_keys_used": memory_result.keys_used,
                        "memory_chars_used": memory_result.chars_used,
                        "memory_items_used": memory_result.items_used,
                        "memory_dropped_count": memory_result.dropped_count,
                    },
                },
                context_active=context_active,
                context_turns_used=context_turns_used,
            )

        if category == CATEGORY_TRACK_ID:
            track_text = now_playing
            if track_text:
                response_text = f"I see: {track_text}."
            else:
                response_text = "I can't see the current track from here yet. Drop a timestamp or clip and I'll help ID it."
            trace = {
                "director": {
                    "type": "ProviderDirector",
                    "addressed_to_roonie": addressed,
                    "trigger": trigger,
                    "routing_enabled": bool(get_routing_runtime_status().get("enabled", True)),
                },
                "behavior": {
                    "category": category,
                    "approved_emotes": approved_emotes,
                    "now_playing_available": now_playing_available,
                },
                "memory": {
                    "keys_used": memory_result.keys_used,
                    "chars_used": memory_result.chars_used,
                    "items_used": memory_result.items_used,
                    "dropped_count": memory_result.dropped_count,
                },
                "proposal": {
                    "text": response_text,
                    "provider_used": "none",
                    "route_used": "behavior:track_id",
                    "moderation_status": "not_applicable",
                    "session_id": session_id or None,
                    "token_usage_if_available": None,
                    "memory_keys_used": memory_result.keys_used,
                    "memory_chars_used": memory_result.chars_used,
                    "memory_items_used": memory_result.items_used,
                    "memory_dropped_count": memory_result.dropped_count,
                },
            }
            return DecisionRecord(
                case_id=str(event.metadata.get("case_id", "live")),
                event_id=event.event_id,
                action="RESPOND_PUBLIC",
                route="behavior:track_id",  # type: ignore[arg-type]
                response_text=response_text,
                trace=trace,
                context_active=context_active,
                context_turns_used=context_turns_used,
            )

        if addressed and category == CATEGORY_GREETING:
            response_text = "Hey! Good to see you."
            trace = {
                "director": {
                    "type": "ProviderDirector",
                    "addressed_to_roonie": addressed,
                    "trigger": trigger,
                    "routing_enabled": bool(get_routing_runtime_status().get("enabled", True)),
                },
                "behavior": {
                    "category": category,
                    "approved_emotes": approved_emotes,
                    "now_playing_available": now_playing_available,
                },
                "memory": {
                    "keys_used": memory_result.keys_used,
                    "chars_used": memory_result.chars_used,
                    "items_used": memory_result.items_used,
                    "dropped_count": memory_result.dropped_count,
                },
                "proposal": {
                    "text": response_text,
                    "provider_used": "none",
                    "route_used": "behavior:greeting",
                    "moderation_status": "not_applicable",
                    "session_id": session_id or None,
                    "token_usage_if_available": None,
                    "memory_keys_used": memory_result.keys_used,
                    "memory_chars_used": memory_result.chars_used,
                    "memory_items_used": memory_result.items_used,
                    "memory_dropped_count": memory_result.dropped_count,
                },
            }
            return DecisionRecord(
                case_id=str(event.metadata.get("case_id", "live")),
                event_id=event.event_id,
                action="RESPOND_PUBLIC",
                route="behavior:greeting",  # type: ignore[arg-type]
                response_text=response_text,
                trace=trace,
                context_active=context_active,
                context_turns_used=context_turns_used,
            )

        prompt = self._build_prompt(
            event,
            context_turns,
            category=category,
            approved_emotes=approved_emotes,
            now_playing_available=now_playing_available,
            memory_hints=memory_result.text_snippet,
        )
        context: Dict[str, Any] = {
            "use_provider_config": True,
            "message_text": event.message,
            "category": str(event.metadata.get("category", "")).strip().lower(),
            "utility_source": str(event.metadata.get("utility_source", "")).strip().lower(),
            "session_id": session_id,
            "allow_live_provider_network": (
                (str(event.metadata.get("mode", "")).strip().lower() == "live")
                and str(os.getenv("ROONIE_ENABLE_LIVE_PROVIDER_NETWORK", "0")).strip().lower()
                in {"1", "true", "yes", "on"}
            ),
        }
        test_overrides = event.metadata.get("provider_test_overrides")
        if not isinstance(test_overrides, dict):
            test_overrides = None

        routing_status = get_routing_runtime_status()
        registry = _provider_registry_from_runtime()
        out = route_generate(
            registry=registry,
            routing_cfg={},
            prompt=prompt,
            context=context,
            test_overrides=test_overrides,
        )

        provider_used = str(
            context.get("provider_selected")
            or context.get("active_provider")
            or registry.get_default().name
            or "openai"
        ).strip().lower() or "openai"
        moderation_status = str(context.get("moderation_result", "not_applicable") or "not_applicable")
        suppression_reason = str(context.get("suppression_reason", "")).strip() or None

        response_text: Optional[str] = None
        action = "NOOP"
        route = "none"
        if isinstance(out, str) and out.strip():
            response_text = out.strip()
            if str(os.getenv("ROONIE_SANITIZE_PROVIDER_STUB_OUTPUT", "")).strip().lower() in {"1", "true", "yes", "on"}:
                response_text = self._sanitize_stub_output(
                    response_text,
                    category=category,
                    user_message=event.message,
                )
            action = "RESPOND_PUBLIC"
            route = f"primary:{provider_used}"

        # Roonie turn storage remains "sent-only"; we intentionally do not add assistant
        # turns here because OutputGate is the final authority on posting.
        self.context_buffer.add_turn(
            speaker="roonie",
            text=response_text or "",
            sent=False,
            related_to_stored_user=stored_user_turn,
        )

        trace: Dict[str, Any] = {
            "director": {
                "type": "ProviderDirector",
                "addressed_to_roonie": addressed,
                "trigger": trigger,
                "routing_enabled": bool(routing_status.get("enabled", True)),
            },
            "behavior": {
                "category": category,
                "approved_emotes": approved_emotes,
                "now_playing_available": now_playing_available,
            },
            "memory": {
                "keys_used": memory_result.keys_used,
                "chars_used": memory_result.chars_used,
                "items_used": memory_result.items_used,
                "dropped_count": memory_result.dropped_count,
            },
            "routing": {
                "routing_enabled": bool(context.get("routing_enabled", False)),
                "routing_class": str(context.get("routing_class", "general")),
                "provider_selected": provider_used,
                "moderation_provider_used": context.get("moderation_provider_used"),
                "moderation_result": moderation_status,
                "override_mode": str(context.get("override_mode", "default")),
            },
            "proposal": {
                "text": response_text,
                "provider_used": provider_used,
                "route_used": route,
                "moderation_status": moderation_status,
                "session_id": session_id or None,
                "token_usage_if_available": context.get("token_usage"),
                "memory_keys_used": memory_result.keys_used,
                "memory_chars_used": memory_result.chars_used,
                "memory_items_used": memory_result.items_used,
                "memory_dropped_count": memory_result.dropped_count,
            },
        }
        if suppression_reason:
            trace["suppression_reason"] = suppression_reason
            trace["provider_block_reason"] = str(context.get("provider_block_reason") or suppression_reason)

        return DecisionRecord(
            case_id=str(event.metadata.get("case_id", "live")),
            event_id=event.event_id,
            action=action,
            route=route,  # type: ignore[arg-type]
            response_text=response_text,
            trace=trace,
            context_active=context_active,
            context_turns_used=context_turns_used,
        )
