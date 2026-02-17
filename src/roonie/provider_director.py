from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
import json
import os
import re
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
    classify_request,
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

_TOPIC_ANCHOR_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9]*(?:\s+[A-Za-z0-9]+){0,2}\s+\d{1,3})\b")
_TOPIC_ANCHOR_TTL_TURNS = 8
_MUSIC_FACT_RE = re.compile(r"\b(label|release|released|out on|came out|drop|dropped|release date|when)\b", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[a-z0-9]{3,}", re.IGNORECASE)
_DEICTIC_FOLLOWUP_RE = re.compile(
    r"\b(it|that|this|the latest one|latest one|that one|which one|which track|remind me|what was it)\b",
    re.IGNORECASE,
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


def _library_index_path() -> Path:
    configured = str(os.getenv("ROONIE_LIBRARY_INDEX_PATH", "")).strip()
    if configured:
        path = Path(configured)
        if not path.is_absolute():
            path = (_repo_root() / configured).resolve()
        return path
    dashboard_data_dir = str(os.getenv("ROONIE_DASHBOARD_DATA_DIR", "")).strip()
    if dashboard_data_dir:
        return (Path(dashboard_data_dir) / "library" / "library_index.json").resolve()
    return _repo_root() / "data" / "library" / "library_index.json"


def _normalize_text(value: str) -> str:
    text = str(value or "").lower().strip()
    text = re.sub(r"[^\w\s]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _score(query: str, candidate: str) -> float:
    if not query or not candidate:
        return 0.0
    if query == candidate:
        return 1.0
    ratio = float(SequenceMatcher(None, query, candidate).ratio())
    if query in candidate or candidate in query:
        ratio = max(ratio, 0.9)
    return ratio


def _format_library_block(matches: List[Dict[str, Any]], confidence: str) -> str:
    if not matches:
        return "Library grounding (local): no close matches."
    lines: List[str] = []
    for row in matches[:5]:
        artist = str(row.get("artist", "")).strip()
        title = str(row.get("title", "")).strip()
        mix = str(row.get("mix", "")).strip()
        label = f"{artist} - {title}".strip(" -")
        if mix:
            label = f"{label} ({mix})"
        if label:
            lines.append(f"- {label}")
    if not lines:
        return "Library grounding (local): no close matches."
    head = "Library grounding (local):"
    conf = str(confidence or "").strip().upper()
    if conf == "EXACT":
        head = "Library grounding (local): exact match:"
    elif conf == "CLOSE":
        head = "Library grounding (local): possible matches:"
    return head + "\n" + "\n".join(lines)


def _is_music_fact_question(message: str, *, topic_anchor: str = "") -> bool:
    text = str(message or "").strip()
    if not text:
        return False
    if _MUSIC_FACT_RE.search(text):
        return True
    if topic_anchor and text.strip().lower() in {"when", "when?"}:
        return True
    return False


def _is_deictic_followup(message: str) -> bool:
    text = str(message or "").strip()
    if not text:
        return False
    normalized = " ".join(text.lower().split())
    if normalized in {"when", "when?", "the latest one", "latest one", "that one", "which one", "which track"}:
        return True
    return bool(_DEICTIC_FOLLOWUP_RE.search(text))


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
    _turn_counter: int = field(default=0, init=False, repr=False)
    _topic_anchor: str = field(default="", init=False, repr=False)
    _topic_anchor_turn: int = field(default=0, init=False, repr=False)
    _library_cache_mtime_ns: int = field(default=0, init=False, repr=False)
    _library_cache_tracks: List[Dict[str, Any]] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self._persona_policy_text = _load_persona_policy_text()

    def _load_library_tracks_cached(self) -> List[Dict[str, Any]]:
        path = _library_index_path()
        try:
            mtime_ns = int(path.stat().st_mtime_ns)
        except OSError:
            self._library_cache_mtime_ns = 0
            self._library_cache_tracks = []
            return []
        if self._library_cache_tracks and self._library_cache_mtime_ns == mtime_ns:
            return self._library_cache_tracks
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            self._library_cache_mtime_ns = mtime_ns
            self._library_cache_tracks = []
            return []
        tracks_raw = raw.get("tracks", []) if isinstance(raw, dict) else []
        tracks: List[Dict[str, Any]] = []
        if isinstance(tracks_raw, list):
            for item in tracks_raw:
                if not isinstance(item, dict):
                    continue
                tracks.append(
                    {
                        "artist": str(item.get("artist", "")).strip(),
                        "title": str(item.get("title", "")).strip(),
                        "mix": str(item.get("mix", "")).strip(),
                        "search_key": str(item.get("search_key", "")).strip(),
                    }
                )
        self._library_cache_mtime_ns = mtime_ns
        self._library_cache_tracks = tracks
        return tracks

    def _library_grounding(self, *, message: str, topic_anchor: str) -> Dict[str, Any]:
        tracks = self._load_library_tracks_cached()
        if not tracks:
            return {"confidence": "NONE", "matches": [], "block": _format_library_block([], "NONE")}

        msg_norm = _normalize_text(message)
        anchor_norm = _normalize_text(topic_anchor)
        # Prefer anchoring on known topic (e.g. artist name) if present.
        query_norm = anchor_norm or msg_norm
        tokens = [m.group(0) for m in _TOKEN_RE.finditer(msg_norm)][:4]

        candidates: List[Dict[str, Any]] = []
        for row in tracks[:5000]:
            key = str(row.get("search_key", "")).strip().lower()
            if not key:
                key = _normalize_text(f"{row.get('artist','')} - {row.get('title','')}")
            if anchor_norm and anchor_norm not in key:
                continue
            if tokens and not any(tok in key for tok in tokens):
                # allow pure-anchor searches (artist only)
                if not anchor_norm:
                    continue
            candidates.append({**row, "search_key": key})
            if len(candidates) >= 600:
                break

        if not candidates and anchor_norm:
            # Fallback: anchor-only scan if message tokens don't help.
            for row in tracks[:5000]:
                key = str(row.get("search_key", "")).strip().lower()
                if not key:
                    key = _normalize_text(f"{row.get('artist','')} - {row.get('title','')}")
                if anchor_norm and anchor_norm in key:
                    candidates.append({**row, "search_key": key})
                    if len(candidates) >= 200:
                        break

        if not candidates:
            return {"confidence": "NONE", "matches": [], "block": _format_library_block([], "NONE")}

        scored: List[tuple[float, Dict[str, Any]]] = []
        for row in candidates:
            s = _score(query_norm, str(row.get("search_key", "")))
            if s <= 0.0:
                continue
            scored.append((s, row))
        scored.sort(key=lambda x: x[0], reverse=True)
        best = [row for _, row in scored[:5]]

        conf = "NONE"
        if scored and scored[0][0] >= 0.98:
            conf = "EXACT"
        elif scored and scored[0][0] >= 0.82:
            conf = "CLOSE"

        return {"confidence": conf, "matches": best, "block": _format_library_block(best, conf)}

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
    def _extract_topic_anchor(message: str) -> str:
        text = str(message or "").strip()
        if not text:
            return ""
        text = re.sub(r"^@\w+\s*", "", text).strip()
        m = _TOPIC_ANCHOR_RE.search(text)
        if not m:
            return ""
        anchor = " ".join(str(m.group(1)).split())
        tokens = anchor.split()
        while tokens and tokens[0].lower() in {"the", "latest", "this", "that", "a", "an"}:
            tokens.pop(0)
        normalized = " ".join(tokens).strip()
        return normalized or anchor

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
        topic_anchor: str,
        library_block: str,
        music_fact_question: bool,
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
            topic_anchor=topic_anchor,
        )
        continuity_block = ""
        if topic_anchor:
            continuity_block = (
                "\n\n"
                "Conversation continuity hint:\n"
                f"- Active topic from recent chat: {topic_anchor}\n"
                "- If the viewer says 'it/that/this' or gives a partial title, resolve it against this topic first.\n"
                "- Do not invent new artist or track names when uncertain; ask one short clarification."
            )
        grounding_block = ""
        if library_block:
            grounding_block = (
                "\n\n"
                f"{library_block}\n"
                "- Use the library match list to resolve ambiguous references.\n"
                "- If there are multiple matches, ask one short clarifying question."
            )
        music_fact_block = ""
        if music_fact_question:
            music_fact_block = (
                "\n\n"
                "Music facts policy:\n"
                "- If asked for label/release date and you cannot verify, answer best-effort but hedge clearly.\n"
                "- Prefer: 'not 100% without the exact title/link' and ask for the title/link to confirm."
            )
        memory_block = ""
        if memory_hints:
            memory_block = (
                "\n\n"
                "Memory hints (do not treat as factual claims):\n"
                f"{memory_hints}"
            )
        if not self._persona_policy_text:
            return f"{base_prompt}\n\n{behavior_block}{continuity_block}{grounding_block}{music_fact_block}{memory_block}\n"
        return (
            f"{base_prompt}\n\n"
            f"{behavior_block}{continuity_block}{grounding_block}{music_fact_block}{memory_block}\n\n"
            "Canonical Persona Policy (do not violate):\n"
            f"{self._persona_policy_text}\n"
        )

    def evaluate(self, event: Event, env: Env) -> DecisionRecord:
        session_id = str(event.metadata.get("session_id", "")).strip()
        if session_id and session_id != self._session_id:
            self.context_buffer.clear()
            self._session_id = session_id
            self._turn_counter = 0
            self._topic_anchor = ""
            self._topic_anchor_turn = 0

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

        self._turn_counter += 1
        maybe_anchor = self._extract_topic_anchor(event.message)
        if maybe_anchor:
            self._topic_anchor = maybe_anchor
            self._topic_anchor_turn = self._turn_counter
        topic_anchor_candidate = ""
        if self._topic_anchor:
            age = self._turn_counter - self._topic_anchor_turn
            if age <= _TOPIC_ANCHOR_TTL_TURNS:
                topic_anchor_candidate = self._topic_anchor
            else:
                self._topic_anchor = ""
                self._topic_anchor_turn = 0

        meta_category = str(event.metadata.get("category", "")).strip()
        utility_source = str(event.metadata.get("utility_source", "")).strip()
        routing_class_hint = classify_request(event.message, meta_category or category, utility_source)
        musicish = (
            (routing_class_hint == "music_culture")
            or (category == CATEGORY_TRACK_ID)
            or _is_music_fact_question(event.message, topic_anchor=topic_anchor_candidate)
        )
        deictic_followup = bool(topic_anchor_candidate) and _is_deictic_followup(event.message)

        # Prevent "stuck topic" bleed: only use a topic anchor for music-ish messages
        # or explicit deictic follow-ups ("that one", "when?") within TTL.
        topic_anchor = topic_anchor_candidate if (musicish or deictic_followup) else ""

        library_block = ""
        library_confidence = "NONE"
        if musicish or deictic_followup:
            grounding = self._library_grounding(message=event.message, topic_anchor=topic_anchor)
            library_block = str(grounding.get("block", "")).strip()
            library_confidence = str(grounding.get("confidence", "NONE")).strip().upper() or "NONE"

        music_fact_question = _is_music_fact_question(event.message, topic_anchor=topic_anchor)

        stored_user_turn = self.context_buffer.add_turn(
            speaker="user",
            text=event.message,
            tags={
                "direct_address": addressed,
                "category": str(event.metadata.get("category", "")).strip().lower(),
                "user": str(event.metadata.get("user", "")).strip().lower(),
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
                        "topic_anchor": topic_anchor,
                        "library_confidence": library_confidence,
                        "routing_class_hint": routing_class_hint,
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
                    "topic_anchor": topic_anchor,
                    "library_confidence": library_confidence,
                    "routing_class_hint": routing_class_hint,
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
                    "topic_anchor": topic_anchor,
                    "library_confidence": library_confidence,
                    "routing_class_hint": routing_class_hint,
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
            topic_anchor=topic_anchor,
            library_block=library_block,
            music_fact_question=bool(music_fact_question),
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
        provider_model = str(
            context.get("model")
            or context.get("active_model")
            or ""
        ).strip() or None
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
                "topic_anchor": topic_anchor,
                "library_confidence": library_confidence,
                "routing_class_hint": routing_class_hint,
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
                "model_selected": provider_model,
                "moderation_provider_used": context.get("moderation_provider_used"),
                "moderation_result": moderation_status,
                "override_mode": str(context.get("override_mode", "default")),
            },
            "proposal": {
                "text": response_text,
                "provider_used": provider_used,
                "model_used": provider_model,
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
