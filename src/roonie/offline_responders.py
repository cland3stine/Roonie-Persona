from __future__ import annotations

import json
import os
import re
import sqlite3
import unicodedata
from pathlib import Path
from difflib import SequenceMatcher
from typing import Optional

from .types import DecisionRecord, Event


_RESPONSES = {
    "responder:neutral_ack": "Got it.",
    "responder:clarify": "Quick check—are you asking me, and what exactly do you mean?",
    "responder:refusal": "keeping that one to myself.",
    "responder:sensitive_ack": "I hear you. take care of yourself tonight.",
    "responder:policy_safe_info": "Camera: (configured gear).",
}


def _is_greeting_message(message: str) -> bool:
    text = (message or "").strip().lower()
    if not text:
        return False
    return bool(
        re.search(
            r"^(?:@[\w_]+\s*)?(?:hey|heya|hi|hello|yo|sup|what'?s up|whats up)\b",
            text,
        )
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _studio_profile_path() -> Path:
    configured = (os.getenv("ROONIE_STUDIO_PROFILE_PATH") or "").strip()
    if configured:
        return Path(configured)
    return _repo_root() / "data" / "studio_profile.json"


def _library_index_path() -> Path:
    configured = (os.getenv("ROONIE_LIBRARY_INDEX_PATH") or "").strip()
    if configured:
        return Path(configured)
    return _repo_root() / "data" / "library" / "library_index.json"


def _memory_db_path() -> Path:
    configured = (os.getenv("ROONIE_MEMORY_DB_PATH") or "").strip()
    if configured:
        return Path(configured)
    return _repo_root() / "data" / "memory.sqlite"


def _load_active_cultural_notes(limit: int = 5) -> list[str]:
    lim = max(1, min(int(limit), 20))
    path = _memory_db_path()
    if not path.exists():
        return []
    try:
        with sqlite3.connect(str(path)) as conn:
            rows = conn.execute(
                """
                SELECT note
                FROM cultural_notes
                WHERE is_active = 1
                ORDER BY updated_at DESC, created_at DESC
                LIMIT ?
                """,
                (lim,),
            ).fetchall()
    except sqlite3.Error:
        return []
    out: list[str] = []
    for row in rows:
        note = str(row[0] if row else "").strip()
        if note:
            out.append(note)
    return out


def _load_active_viewer_notes(viewer_handle: str, limit: int = 5) -> list[str]:
    viewer = str(viewer_handle or "").strip().lstrip("@").lower()
    if not viewer:
        return []
    lim = max(1, min(int(limit), 20))
    path = _memory_db_path()
    if not path.exists():
        return []
    try:
        with sqlite3.connect(str(path)) as conn:
            rows = conn.execute(
                """
                SELECT note
                FROM viewer_notes
                WHERE viewer_handle = ? AND is_active = 1
                ORDER BY updated_at DESC, created_at DESC
                LIMIT ?
                """,
                (viewer, lim),
            ).fetchall()
    except sqlite3.Error:
        return []
    out: list[str] = []
    for row in rows:
        note = str(row[0] if row else "").strip()
        if note:
            out.append(note)
    return out


def _default_profile() -> dict:
    return {
        "location": {"display": "Washington DC area"},
        "social_links": [],
        "gear": ["Camera: (configured gear)"],
        "faq": [],
        "approved_emotes": [],
    }


def _load_studio_profile() -> dict:
    path = _studio_profile_path()
    if not path.exists():
        return _default_profile()
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return _default_profile()
    if not isinstance(raw, dict):
        return _default_profile()
    profile = _default_profile()
    for key in ("location", "social_links", "gear", "faq", "approved_emotes"):
        if key in raw:
            profile[key] = raw[key]
    return profile


def _ensure_sentence(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    if cleaned.endswith((".", "!", "?")):
        return cleaned
    return cleaned + "."


def _normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower().strip()
    text = re.sub(r"[^\w\s]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _search_key(artist: str, title: str) -> str:
    return _normalize_text(f"{artist} - {title}".strip())


def _load_library_tracks() -> list[dict]:
    path = _library_index_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return []
    if not isinstance(raw, dict):
        return []
    tracks = raw.get("tracks", [])
    if not isinstance(tracks, list):
        return []
    out = []
    for item in tracks:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "artist": str(item.get("artist", "")).strip(),
                "title": str(item.get("title", "")).strip(),
                "search_key": str(item.get("search_key", "")).strip(),
            }
        )
    return out


def _library_query_text(message: str) -> str:
    text = str(message or "").strip()
    lowered = text.lower()
    patterns = [
        r"do you have\s+(.+)",
        r"have\s+(.+)\s+in (?:your|the)\s+library",
        r"is\s+(.+)\s+in (?:your|the)\s+library",
        r"got\s+(.+)\?",
    ]
    for pat in patterns:
        m = re.search(pat, lowered)
        if m:
            candidate = m.group(1)
            candidate = re.sub(r"\bin\s+(?:your|the)\s+library\b.*$", "", candidate).strip()
            candidate = re.sub(r"^[\s@:]*roonie(?:thecat)?[\s,:-]*", "", candidate).strip()
            return _normalize_text(candidate)
    cleaned = re.sub(r"\bin\s+(?:your|the)\s+library\b.*$", "", lowered).strip()
    cleaned = re.sub(r"^[\s@:]*roonie(?:thecat)?[\s,:-]*", "", cleaned).strip()
    return _normalize_text(cleaned)


def _library_search_confidence(message: str, tracks: Optional[list[dict]] = None) -> str:
    query = _library_query_text(message)
    if not query:
        return "NONE"
    hay = tracks if tracks is not None else _load_library_tracks()
    exact = False
    close = False
    for row in hay:
        key = str(row.get("search_key", "")).strip()
        if not key:
            key = _search_key(str(row.get("artist", "")), str(row.get("title", "")))
        if not key:
            continue
        if key == query:
            exact = True
            break
        ratio = SequenceMatcher(None, query, key).ratio()
        if query in key or key in query:
            ratio = max(ratio, 0.9)
        if ratio >= 0.82:
            close = True
    if exact:
        return "EXACT"
    if close:
        return "CLOSE"
    return "NONE"


def library_availability_response(message: str) -> tuple[str, str]:
    confidence = _library_search_confidence(message)
    if confidence == "EXACT":
        return confidence, "Yes — I have that in the library."
    if confidence == "CLOSE":
        return confidence, "I might have it (close match)."
    return confidence, "Not seeing that in the library."


def classify_safe_info_category(message: str, profile: Optional[dict] = None) -> str:
    text = (message or "").strip().lower()
    if not text:
        return "legacy_safe_info"
    if any(token in text for token in ("what track is this", "track is this", "id this track", "track id", "song is this")):
        return "utility_library"
    if (
        ("library" in text and any(token in text for token in ("have", "got", "in there", "in your")))
        or ("do you have" in text and any(token in text for token in ("track", "song", "mix")))
        or ("got this" in text and "track" in text)
    ):
        return "utility_library"
    if any(token in text for token in ("where are you based", "where are you from", "where do you live", "location", "based in")):
        return "utility_location"
    if any(token in text for token in ("social", "twitch", "tiktok", "instagram", "youtube", "discord", "linktree")):
        return "utility_social"
    if "emote" in text:
        return "utility_emotes"
    if any(token in text for token in ("camera", "gear", "setup", "controller", "mixer", "interface", "software", "daw", "deck")):
        return "utility_gear"

    profile_obj = profile or {}
    faq = profile_obj.get("faq", [])
    if isinstance(faq, list):
        for item in faq:
            if not isinstance(item, dict):
                continue
            q = str(item.get("q", "")).strip().lower()
            if q and q in text:
                return "utility_faq"
    return "legacy_safe_info"


def _flatten_gear(profile: dict) -> list[dict]:
    gear = profile.get("gear", [])
    out = []
    if isinstance(gear, list):
        for item in gear:
            if isinstance(item, dict):
                name = str(item.get("name", "")).strip()
                value = str(item.get("value", "")).strip()
                if name and value:
                    out.append({"section": "flat", "name": name, "value": value})
                continue
            text = str(item or "").strip()
            if not text:
                continue
            if ":" in text:
                name, value = [part.strip() for part in text.split(":", 1)]
            else:
                name, value = text, "(fill later)"
            if name and value:
                out.append({"section": "flat", "name": name, "value": value})
        return out
    if not isinstance(gear, dict):
        return out
    for section in ("dj", "audio", "video", "software"):
        rows = gear.get(section, [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name", "")).strip()
            value = str(row.get("value", "")).strip()
            if name and value:
                out.append({"section": section, "name": name, "value": value})
    return out


def _pick_gear_entry(message: str, profile: dict) -> Optional[dict]:
    text = (message or "").lower()
    gear = _flatten_gear(profile)
    if not gear:
        return None
    for token in ("camera", "controller", "mixer", "interface", "software", "daw", "deck"):
        if token not in text:
            continue
        for row in gear:
            if token in row["name"].lower():
                return row
        if token in {"camera", "deck"}:
            for row in gear:
                if row["section"] in {"video", "dj"}:
                    return row
    return gear[0]


def _respond_with_gear(message: str, profile: dict) -> str:
    row = _pick_gear_entry(message, profile)
    if not row:
        return _RESPONSES["responder:policy_safe_info"]
    value = row["value"]
    if value == "(fill later)" and "camera" in row["name"].lower():
        value = "(configured gear)"
    return _ensure_sentence(f"{row['name']}: {value}")


def _respond_with_location(profile: dict) -> str:
    location = profile.get("location", {})
    if not isinstance(location, dict):
        return "Based in Washington DC area."
    display = str(location.get("display", "")).strip() or "Washington DC area"
    return _ensure_sentence(f"Based in {display}")


def _respond_with_social(profile: dict) -> str:
    links = profile.get("social_links", [])
    if not isinstance(links, list) or not links:
        return "Socials: (fill later)."
    parts = []
    for item in links[:5]:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", "")).strip()
        url = str(item.get("url", "")).strip()
        if label and url:
            parts.append(f"{label}: {url}")
    if not parts:
        return "Socials: (fill later)."
    return _ensure_sentence("Socials: " + " | ".join(parts[:3]))


def _respond_with_faq(message: str, profile: dict) -> Optional[str]:
    text = (message or "").strip().lower()
    faq = profile.get("faq", [])
    if not isinstance(faq, list):
        return None
    for item in faq:
        if not isinstance(item, dict):
            continue
        q = str(item.get("q", "")).strip().lower()
        a = str(item.get("a", "")).strip()
        if q and a and q in text:
            return _ensure_sentence(a)
    return None


def _respond_with_emotes(profile: dict) -> str:
    emotes = profile.get("approved_emotes", [])
    if not isinstance(emotes, list) or not emotes:
        return "Approved emotes: none."
    names = [str(item).strip() for item in emotes if str(item).strip()]
    if not names:
        return "Approved emotes: none."
    return _ensure_sentence("Approved emotes: " + ", ".join(names[:8]))


def respond(route: str, event: Event, decision: Optional[DecisionRecord]) -> Optional[str]:
    if route == "responder:neutral_ack" and _is_greeting_message(event.message):
        return "Hey there! Good to see you."
    if route == "responder:policy_safe_info":
        profile = _load_studio_profile()
        category = classify_safe_info_category(event.message, profile)
        if category == "utility_library":
            _, text = library_availability_response(event.message)
            return text
        if category == "utility_location":
            return _respond_with_location(profile)
        if category == "utility_social":
            return _respond_with_social(profile)
        if category == "utility_faq":
            faq_text = _respond_with_faq(event.message, profile)
            if faq_text:
                return faq_text
        if category == "utility_emotes":
            return _respond_with_emotes(profile)
        if category == "legacy_safe_info":
            return _RESPONSES["responder:policy_safe_info"]
        return _respond_with_gear(event.message, profile)
    return _RESPONSES.get(route)

