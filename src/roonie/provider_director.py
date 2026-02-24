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
from roonie.language_rules import starts_with_direct_verb
from providers.registry import ProviderRegistry
from providers.router import (
    classify_request,
    get_provider_runtime_status,
    get_routing_runtime_status,
    route_generate,
)
from roonie.context.context_buffer import ContextBuffer
from roonie.prompting import build_roonie_prompt
from roonie.safety_policy import classify_message_safety
from roonie.types import DecisionRecord, Env, Event


_SHORT_ACK_MAX_CHARS = 220

_TOPIC_ANCHOR_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9]*(?:\s+[A-Za-z0-9]+){0,2}\s+\d{1,3})\b")
_TOPIC_ANCHOR_PHRASE_RE = re.compile(r"\b([A-Z][A-Za-z0-9]*(?:\s+[A-Za-z0-9][A-Za-z0-9']*){1,5})\b")
_TOPIC_ANCHOR_TTL_TURNS = 8
_MUSIC_FACT_RE = re.compile(r"\b(label|release|released|out on|came out|drop|dropped|release date|when)\b", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[a-z0-9]{3,}", re.IGNORECASE)
_DEICTIC_FOLLOWUP_RE = re.compile(
    r"\b(it|that|this|the latest one|latest one|that one|which one|which track|remind me|what was it)\b",
    re.IGNORECASE,
)
_ANCHOR_STOPWORDS = {
    "the",
    "a",
    "an",
    "this",
    "that",
    "latest",
    "one",
    "hey",
    "hi",
    "hello",
    "thanks",
    "thank",
    "ok",
    "okay",
    "hope",
    "today",
    "tonight",
    "lately",
}


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


def _topic_overlap(message: str, anchor: str) -> bool:
    msg_norm = _normalize_text(message)
    anc_norm = _normalize_text(anchor)
    if not msg_norm or not anc_norm:
        return False
    msg_tokens = {
        t
        for t in _TOKEN_RE.findall(msg_norm)
        if t and (t not in _ANCHOR_STOPWORDS) and len(t) >= 3
    }
    anchor_tokens = [
        t
        for t in _TOKEN_RE.findall(anc_norm)
        if t and (t not in _ANCHOR_STOPWORDS) and len(t) >= 3
    ]
    if not msg_tokens or not anchor_tokens:
        return False
    uniq_anchor = set(anchor_tokens)
    hits = sum(1 for t in uniq_anchor if t in msg_tokens)
    if hits >= 2:
        return True
    # Small anchors like "Maze 28" only have one meaningful token ("maze").
    if hits >= 1 and len(uniq_anchor) <= 2:
        return True
    return False


def _load_persona_policy_text() -> str:
    path = _persona_policy_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    cleaned = text.strip()
    if not cleaned:
        return ""
    # Skip injection if file is pure YAML config (no behavioral prose).
    # A line is "config-only" if blank or looks like a YAML key/value/list/frontmatter.
    _yaml_line = re.compile(r"^(?:---|\s*[\w_]+\s*:.*|\s*-\s+.*)$")
    has_prose = any(
        line.strip() and not _yaml_line.match(line)
        for line in cleaned.splitlines()
    )
    if not has_prose:
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
    context_buffer: ContextBuffer = field(default_factory=lambda: ContextBuffer(max_turns=12))
    _session_id: str = field(default="", init=False, repr=False)
    _persona_policy_text: str = field(default="", init=False, repr=False)
    _turn_counter: int = field(default=0, init=False, repr=False)
    _topic_anchor: str = field(default="", init=False, repr=False)
    _topic_anchor_turn: int = field(default=0, init=False, repr=False)
    _topic_anchor_kind: str = field(default="", init=False, repr=False)  # "music"|"general"
    _library_cache_mtime_ns: int = field(default=0, init=False, repr=False)
    _library_cache_tracks: List[Dict[str, Any]] = field(default_factory=list, init=False, repr=False)
    _pending_assistant_turns: Dict[str, Dict[str, Any]] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self._persona_policy_text = _load_persona_policy_text()

    def _queue_pending_assistant_turn(self, *, event_id: str, text: str, related_to_stored_user: bool) -> None:
        key = str(event_id or "").strip()
        value = str(text or "").strip()
        if not key or not value:
            return
        self._pending_assistant_turns[key] = {
            "text": value,
            "related_to_stored_user": bool(related_to_stored_user),
        }
        # Keep this bounded for direct unit tests that call evaluate without output feedback.
        if len(self._pending_assistant_turns) > 128:
            oldest_key = next(iter(self._pending_assistant_turns))
            self._pending_assistant_turns.pop(oldest_key, None)

    def apply_output_feedback(
        self,
        *,
        event_id: str,
        emitted: bool,
        send_result: Optional[Dict[str, Any]] = None,
    ) -> None:
        key = str(event_id or "").strip()
        if not key:
            return
        pending = self._pending_assistant_turns.pop(key, None)
        if not isinstance(pending, dict):
            return
        text = str(pending.get("text", "")).strip()
        if not text:
            return
        was_sent = bool(emitted)
        if isinstance(send_result, dict) and "sent" in send_result:
            was_sent = was_sent and bool(send_result.get("sent", False))
        if not was_sent:
            return
        self.context_buffer.add_turn(
            speaker="roonie",
            text=text,
            sent=True,
            related_to_stored_user=bool(pending.get("related_to_stored_user", False)),
        )

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
        if starts_with_direct_verb(text):
            return True
        if len(text) <= 3:
            return True
        return False

    @staticmethod
    def _should_short_ack_direct_address(*, addressed: bool, category: str, message: str) -> bool:
        if not addressed:
            return False
        if str(category or "").strip().upper() != CATEGORY_OTHER:
            return False
        text = str(message or "").strip()
        if not text:
            return False
        if "?" in text:
            return False
        # Strip leading mention before measuring substance.
        stripped = re.sub(r"^@\w+\s*", "", text).strip()
        if not stripped:
            return False
        if len(stripped) > _SHORT_ACK_MAX_CHARS:
            return False
        # Avoid turning tiny acknowledgments into forced replies.
        if len(stripped.split()) < 5 and "," not in stripped and "." not in stripped:
            return False
        return True

    @staticmethod
    def _approved_emotes(metadata: Dict[str, Any]) -> List[str]:
        raw = metadata.get("approved_emotes", [])
        if not isinstance(raw, list):
            return []
        out: List[str] = []
        for item in raw[:24]:
            if isinstance(item, dict):
                if item.get("denied", False):
                    continue
                name = str(item.get("name") or "").strip()
                desc = str(item.get("desc") or "").strip()
                if name:
                    out.append(f"{name} ({desc})" if desc else name)
            else:
                text = str(item or "").strip()
                if text:
                    out.append(text)
        return out

    @staticmethod
    def _normalize_emote_spacing(text: str, approved_emotes: List[str]) -> str:
        value = str(text or "").strip()
        if not value:
            return ""
        if not approved_emotes:
            return value
        names: List[str] = []
        seen: set[str] = set()
        for item in approved_emotes[:24]:
            token = str(item or "").strip()
            if not token:
                continue
            match = re.match(r"^([A-Za-z][A-Za-z0-9_]{2,31})\b", token)
            if not match:
                continue
            name = str(match.group(1)).strip()
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            names.append(name)
        if not names:
            return value
        # Providers can glue emotes to trailing punctuation/words ("...booth.ruleof6Paws").
        # Ensure a delimiter so Twitch parses the emote token correctly.
        normalized = value
        for name in sorted(names, key=len, reverse=True):
            pattern = rf"(?<![\s@])({re.escape(name)})(?=$|[\s\.,!?:;\)\]\}}])"
            normalized = re.sub(pattern, r" \1", normalized)
        return normalized

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
    def _inner_circle_block(metadata: Dict[str, Any]) -> str:
        raw = metadata.get("inner_circle", [])
        if not isinstance(raw, list) or not raw:
            return ""
        lines = []
        for m in raw[:50]:
            if not isinstance(m, dict):
                continue
            username = str(m.get("username", "")).strip()
            if not username:
                continue
            display = str(m.get("display_name", "")).strip() or username
            role = str(m.get("role", "")).strip()
            note = str(m.get("note", "")).strip()
            parts = [f"@{username}"]
            if display and display.lower() != username.lower():
                parts[0] = f"@{username} ({display})"
            if role:
                parts.append(role)
            if note:
                parts.append(note)
            lines.append("- " + " — ".join(parts))
        if not lines:
            return ""
        return "People you know:\n" + "\n".join(lines)

    _DAY_ORDER = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }

    @staticmethod
    def _stream_schedule_block(metadata: Dict[str, Any]) -> str:
        raw = metadata.get("stream_schedule")
        if not isinstance(raw, dict):
            return ""
        slots = raw.get("slots", [])
        if not isinstance(slots, list):
            return ""
        tz = str(raw.get("timezone", "ET")).strip() or "ET"
        day_order = ProviderDirector._DAY_ORDER
        filtered = []
        for s in slots:
            if not isinstance(s, dict):
                continue
            day = str(s.get("day", "")).strip().lower()
            time_val = str(s.get("time", "")).strip()
            if day and time_val:
                filtered.append(s)
        if not filtered:
            return ""
        filtered.sort(key=lambda s: day_order.get(str(s.get("day", "")).strip().lower(), 99))
        parts = []
        for s in filtered:
            day = str(s.get("day", "")).strip().capitalize()
            time_val = str(s.get("time", "")).strip()
            note = str(s.get("note", "")).strip()
            entry = f"{day} {time_val}"
            if note:
                entry += f" ({note})"
            parts.append(entry)
        line = f"Stream schedule (all times {tz}): {', '.join(parts)}"
        override = str(raw.get("next_stream_override", "")).strip()
        if override:
            line += f"\nSchedule note: {override}"
        return line

    @staticmethod
    def _extract_topic_anchor(message: str) -> str:
        text = str(message or "").strip()
        if not text:
            return ""
        # Strip leading @mentions (viewer or roonie) to avoid anchoring on handles.
        text = re.sub(r"^@\w+\s*", "", text).strip()

        # Prefer numeric-ish anchors (e.g., "Maze 28") because they are distinctive.
        m = _TOPIC_ANCHOR_RE.search(text)
        if m:
            anchor = " ".join(str(m.group(1)).split())
            tokens = anchor.split()
            while tokens and tokens[0].lower() in _ANCHOR_STOPWORDS:
                tokens.pop(0)
            while tokens and tokens[-1].lower().strip(".,!?") in _ANCHOR_STOPWORDS:
                tokens.pop()
            normalized = " ".join(tokens).strip()
            return normalized or anchor

        # General-purpose anchor: a capitalized phrase with a few trailing tokens.
        # This is intentionally conservative and should only be *used* when the
        # current message indicates continuity (deictic follow-up or token overlap).
        m2 = _TOPIC_ANCHOR_PHRASE_RE.search(text)
        if not m2:
            return ""
        anchor2 = " ".join(str(m2.group(1)).split()).strip().strip(".,!?")
        if not anchor2:
            return ""
        tokens2 = [t.strip(".,!?") for t in anchor2.split() if t.strip(".,!?")]
        while tokens2 and tokens2[0].lower() in _ANCHOR_STOPWORDS:
            tokens2.pop(0)
        while tokens2 and tokens2[-1].lower() in _ANCHOR_STOPWORDS:
            tokens2.pop()
        cleaned = " ".join(tokens2).strip()
        if not cleaned:
            return ""
        lowered = cleaned.lower()
        if lowered in {"roonie", "rooniethecat"}:
            return ""
        return cleaned

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

        def _pick(pool: list, key: str) -> str:
            return pool[abs(hash(key)) % len(pool)]

        _GREETING = [
            "hey, welcome in",
            "good to see you",
            "hey. glad you're here",
            "evening. pull up a seat",
        ]
        _BANTER_GENERAL = [
            "honestly this set is locked in right now",
            "right? the energy in here tonight is something",
            "sitting on this booth feeling every transition",
            "I'm good. glad you're here",
        ]
        _BANTER_THERE = [
            "I'm here. always here",
            "still on the booth. still listening",
            "I'm right here. just taking it in",
        ]
        _BANTER_HOW = [
            "I'm good. glad you're here",
            "doing well. this set is helping",
            "all good up here on the booth",
        ]
        _FOLLOW = [
            "welcome in. glad you found us",
            "hey, welcome. stick around — sets go deep",
            "welcome. you picked a good night",
        ]
        _SUB = [
            "appreciate that. welcome to the crew",
            "that means a lot. glad to have you",
            "welcome in. you're part of this now",
        ]
        _CHEER = [
            "appreciate the love",
            "hey, thank you",
            "that's real. appreciate it",
        ]
        _RAID = [
            "welcome in, everyone. good timing",
            "hey raiders. you just walked into something good",
            "welcome. settle in — there's a lot of music ahead",
        ]
        _GENERIC = [
            "hey. I'm here",
            "right here on the booth",
            "I'm right here",
        ]

        if cat == CATEGORY_GREETING:
            return _pick(_GREETING, msg or cat)
        if cat == CATEGORY_BANTER:
            if "you there" in msg or "are you there" in msg:
                return _pick(_BANTER_THERE, msg)
            if "how are" in msg or "how you" in msg or "how's" in msg:
                return _pick(_BANTER_HOW, msg)
            return _pick(_BANTER_GENERAL, msg or cat)
        if cat == "EVENT_FOLLOW":
            return _pick(_FOLLOW, msg or cat)
        if cat == "EVENT_SUB":
            return _pick(_SUB, msg or cat)
        if cat == "EVENT_CHEER":
            return _pick(_CHEER, msg or cat)
        if cat == "EVENT_RAID":
            return _pick(_RAID, msg or cat)
        return _pick(_GENERIC, msg or cat)

    def _build_prompt(
        self,
        event: Event,
        context_turns: list[Any],
        *,
        category: str,
        approved_emotes: List[str],
        now_playing_available: bool,
        now_playing_text: str = "",
        inner_circle_text: str = "",
        schedule_text: str = "",
        memory_hints: str,
        topic_anchor: str,
        library_block: str,
        music_fact_question: bool,
        short_ack_preferred: bool = False,
        safety_classification: str = "allowed",
    ) -> str:
        base_prompt = build_roonie_prompt(
            message=event.message,
            metadata={
                "viewer": event.metadata.get("user", "viewer"),
                "channel": event.metadata.get("channel", ""),
            },
            context_turns=context_turns,
            max_context_turns=8,
            max_context_chars=1200,
            now_playing_text=now_playing_text,
            inner_circle_text=inner_circle_text,
            schedule_text=schedule_text,
        )
        behavior_block = behavior_guidance(
            category=category,
            approved_emotes=approved_emotes,
            now_playing_available=now_playing_available,
            topic_anchor=topic_anchor,
            short_ack_preferred=short_ack_preferred,
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
        safety_block = ""
        if safety_classification == "refuse":
            safety_block = (
                "\n\n"
                "IMPORTANT — This message may be asking for private or identifying information. "
                "Deflect casually and stay fully in character. Never reveal real addresses, "
                "phone numbers, full names, email addresses, IP addresses, or other identifying "
                "details about yourself or your people. If asked about location, say \"DC area\" "
                "and nothing more specific. Keep it brief and natural."
            )
        elif safety_classification == "sensitive_no_followup":
            safety_block = (
                "\n\n"
                "IMPORTANT — This viewer may be expressing emotional distress. "
                "Respond with brief warmth and care, staying in character. "
                "Do not ask follow-up questions about their emotional state. "
                "Do not play therapist or counselor. A short, genuine acknowledgment is enough."
            )
        if not self._persona_policy_text:
            return f"{base_prompt}\n\n{behavior_block}{grounding_block}{music_fact_block}{memory_block}{safety_block}\n"
        return (
            f"{base_prompt}\n\n"
            f"{behavior_block}{grounding_block}{music_fact_block}{memory_block}{safety_block}\n\n"
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
            self._topic_anchor_kind = ""
            self._pending_assistant_turns.clear()

        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        addressed = self._is_direct_address(event)
        category = classify_behavior_category(message=event.message, metadata=metadata)
        short_ack_preferred = self._should_short_ack_direct_address(
            addressed=addressed,
            category=category,
            message=event.message,
        )
        if short_ack_preferred:
            category = CATEGORY_BANTER
        trigger = (category != CATEGORY_OTHER) or self._is_trigger_message(event.message)
        safety_classification, refusal_reason_code = classify_message_safety(event.message)
        approved_emotes = self._approved_emotes(metadata)
        now_playing = self._now_playing_text(metadata)
        now_playing_available = bool(now_playing)
        context_turns = self.context_buffer.get_context(max_turns=8)
        context_turns_used = len(context_turns)
        context_active = context_turns_used > 0

        self._turn_counter += 1
        meta_category = str(event.metadata.get("category", "")).strip()
        utility_source = str(event.metadata.get("utility_source", "")).strip()
        routing_class_hint = classify_request(event.message, meta_category or category, utility_source)

        maybe_anchor = self._extract_topic_anchor(event.message)
        if maybe_anchor:
            self._topic_anchor = maybe_anchor
            self._topic_anchor_turn = self._turn_counter
            # Anchor kind is determined at the time it is set.
            # This is used to avoid running music-only grounding for general topics.
            anchor_musicish = (
                (routing_class_hint == "music_culture")
                or (category == CATEGORY_TRACK_ID)
                or bool(_MUSIC_FACT_RE.search(str(event.message or "")))
            )
            self._topic_anchor_kind = "music" if anchor_musicish else "general"
        topic_anchor_candidate = ""
        if self._topic_anchor:
            age = self._turn_counter - self._topic_anchor_turn
            if age <= _TOPIC_ANCHOR_TTL_TURNS:
                topic_anchor_candidate = self._topic_anchor
            else:
                self._topic_anchor = ""
                self._topic_anchor_turn = 0
                self._topic_anchor_kind = ""

        # Only treat "label/release/when" as music-fact intent if we're already in a music thread
        # (router says music_culture, track-id category, or the anchor was set from a music turn).
        music_thread = (routing_class_hint == "music_culture") or (category == CATEGORY_TRACK_ID) or (
            self._topic_anchor_kind == "music"
        )
        music_fact_question_candidate = _is_music_fact_question(event.message, topic_anchor=topic_anchor_candidate)
        musicish = (routing_class_hint == "music_culture") or (category == CATEGORY_TRACK_ID) or (
            music_thread and music_fact_question_candidate
        )
        deictic_followup = bool(topic_anchor_candidate) and _is_deictic_followup(event.message)
        overlap_followup = bool(topic_anchor_candidate) and _topic_overlap(event.message, topic_anchor_candidate)

        # Prevent "stuck topic" bleed: only use a topic anchor for music-ish messages
        # or follow-ups that clearly indicate continuity ("that one", "when?", token overlap) within TTL.
        topic_anchor = topic_anchor_candidate if (musicish or deictic_followup or overlap_followup) else ""

        library_block = ""
        library_confidence = "NONE"
        if musicish or (deictic_followup and self._topic_anchor_kind == "music"):
            grounding = self._library_grounding(message=event.message, topic_anchor=topic_anchor)
            library_block = str(grounding.get("block", "")).strip()
            library_confidence = str(grounding.get("confidence", "NONE")).strip().upper() or "NONE"

        music_fact_question = bool(music_thread and _is_music_fact_question(event.message, topic_anchor=topic_anchor))

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
                        "short_ack_preferred": short_ack_preferred,
                        "topic_anchor": topic_anchor,
                        "topic_anchor_kind": self._topic_anchor_kind or None,
                        "library_confidence": library_confidence,
                        "routing_class_hint": routing_class_hint,
                    },
                    "memory": {
                        "keys_used": memory_result.keys_used,
                        "chars_used": memory_result.chars_used,
                        "items_used": memory_result.items_used,
                        "dropped_count": memory_result.dropped_count,
                    },
                    "policy": {
                        "safety_classification": safety_classification,
                        "refusal_reason_code": refusal_reason_code,
                    },
                    "proposal": {
                        "text": None,
                        "message_text": event.message,
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

        inner_circle_text = self._inner_circle_block(metadata)
        schedule_text = self._stream_schedule_block(metadata)

        prompt = self._build_prompt(
            event,
            context_turns,
            category=category,
            approved_emotes=approved_emotes,
            now_playing_available=now_playing_available,
            now_playing_text=now_playing,
            inner_circle_text=inner_circle_text,
            schedule_text=schedule_text,
            memory_hints=memory_result.text_snippet,
            topic_anchor=topic_anchor,
            library_block=library_block,
            music_fact_question=bool(music_fact_question),
            short_ack_preferred=short_ack_preferred,
            safety_classification=safety_classification,
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
            response_text = self._normalize_emote_spacing(response_text, approved_emotes)
            action = "RESPOND_PUBLIC"
            route = f"primary:{provider_used}"

        event_id = str(event.event_id or "").strip()
        if event_id:
            self._pending_assistant_turns.pop(event_id, None)
        if response_text:
            self._queue_pending_assistant_turn(
                event_id=event_id,
                text=response_text,
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
                "short_ack_preferred": short_ack_preferred,
                "now_playing_available": now_playing_available,
                "topic_anchor": topic_anchor,
                "topic_anchor_kind": self._topic_anchor_kind or None,
                "library_confidence": library_confidence,
                "routing_class_hint": routing_class_hint,
            },
            "memory": {
                "keys_used": memory_result.keys_used,
                "chars_used": memory_result.chars_used,
                "items_used": memory_result.items_used,
                "dropped_count": memory_result.dropped_count,
            },
            "policy": {
                "safety_classification": safety_classification,
                "refusal_reason_code": refusal_reason_code,
            },
            "routing": {
                "routing_enabled": bool(context.get("routing_enabled", False)),
                "routing_class": str(context.get("routing_class", "general")),
                "provider_selected": provider_used,
                "model_selected": provider_model,
                "moderation_provider_used": context.get("moderation_provider_used"),
                "moderation_result": moderation_status,
                "override_mode": str(context.get("override_mode", "default")),
                "provider_error_attempts": context.get("provider_error_attempts"),
            },
            "proposal": {
                "text": response_text,
                "message_text": event.message,
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
            trace["provider_error_detail"] = context.get("provider_error_detail")

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
