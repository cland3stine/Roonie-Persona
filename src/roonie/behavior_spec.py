from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


CATEGORY_GREETING = "GREETING"
CATEGORY_BANTER = "BANTER"
CATEGORY_TRACK_ID = "TRACK_ID"
CATEGORY_EVENT_FOLLOW = "EVENT_FOLLOW"
CATEGORY_EVENT_SUB = "EVENT_SUB"
CATEGORY_EVENT_CHEER = "EVENT_CHEER"
CATEGORY_EVENT_RAID = "EVENT_RAID"
CATEGORY_OTHER = "OTHER"


EVENT_TYPE_TO_CATEGORY = {
    "FOLLOW": CATEGORY_EVENT_FOLLOW,
    "SUB": CATEGORY_EVENT_SUB,
    "CHEER": CATEGORY_EVENT_CHEER,
    "RAID": CATEGORY_EVENT_RAID,
}


EVENT_COOLDOWN_SECONDS = {
    CATEGORY_EVENT_FOLLOW: 45.0,
    CATEGORY_EVENT_SUB: 20.0,
    CATEGORY_EVENT_CHEER: 20.0,
    CATEGORY_EVENT_RAID: 30.0,
}
GREETING_COOLDOWN_SECONDS = 15.0


_GREETING_RE = re.compile(r"^(?:@[\w_]+\s*)?(?:hey|heya|hi|hello|yo|sup|what'?s up|whats up)\b", re.IGNORECASE)
_TRACK_ID_RE = re.compile(
    r"\b(track\s*id|what(?:'s| is)?\s+(?:this|that)\s+track|id\?|what\s+track|track\?)\b",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(r"\?")
_FOLLOWUP_RE = re.compile(
    r"\b(how|what|why|when|where|which|who|can|do|does|did|is|are)\b",
    re.IGNORECASE,
)


def _looks_like_pure_greeting(text: str) -> bool:
    m = _GREETING_RE.search(text)
    if not m:
        return False
    tail = text[m.end() :].strip(" \t\r\n,!.?-")
    if not tail:
        return True
    if _QUESTION_RE.search(tail):
        return False
    if _FOLLOWUP_RE.search(tail):
        return False
    # Keep one-word tails ("hey there") in greeting bucket.
    return len(tail.split()) <= 2


def classify_behavior_category(*, message: str, metadata: Dict[str, Any]) -> str:
    event_type = str(metadata.get("event_type", "")).strip().upper()
    if event_type in EVENT_TYPE_TO_CATEGORY:
        return EVENT_TYPE_TO_CATEGORY[event_type]

    text = str(message or "").strip()
    if not text:
        return CATEGORY_OTHER
    if _TRACK_ID_RE.search(text):
        return CATEGORY_TRACK_ID
    if _looks_like_pure_greeting(text):
        return CATEGORY_GREETING
    if _QUESTION_RE.search(text) or len(text) <= 80:
        return CATEGORY_BANTER
    return CATEGORY_OTHER


def behavior_guidance(
    *,
    category: str,
    approved_emotes: List[str],
    now_playing_available: bool,
    topic_anchor: str = "",
) -> str:
    lines: List[str] = []
    if category == CATEGORY_TRACK_ID:
        lines.append("This is a track ID question. Don't guess track names you're not sure about. Show you're curious about the track too.")
        if now_playing_available:
            lines.append("You have now-playing info available to reference.")
        else:
            lines.append("You don't have track info right now. Ask for a timestamp or clip if needed.")
    elif category in EVENT_COOLDOWN_SECONDS:
        lines.append("Quick thank-you for the event. Be warm and hyped, make them feel like it matters. Keep it brief.")
    elif category == CATEGORY_GREETING:
        lines.append("Greet them like a friend you're happy to see. Match their energy or bring it up a notch.")
    elif category == CATEGORY_BANTER:
        if topic_anchor:
            lines.append(f"Recent topic: {topic_anchor}. Pick up the thread if relevant.")
        lines.append("Chat naturally. Be warm, react to what they actually said. Light teasing is welcome if the moment is right.")
    if topic_anchor and category != CATEGORY_BANTER:
        lines.append(f"Recent topic: {topic_anchor}. Pick up the thread if relevant.")
    if approved_emotes:
        lines.append(f"Approved emotes: {', '.join(approved_emotes)}. One per message maximum, at the END only. Most messages: no emote.")
    return "\n".join(lines) if lines else ""


def cooldown_for_category(category: str) -> Tuple[Optional[str], float, Optional[str]]:
    cat = str(category or "").strip().upper()
    if cat in EVENT_COOLDOWN_SECONDS:
        return cat, float(EVENT_COOLDOWN_SECONDS[cat]), "EVENT_COOLDOWN"
    if cat == CATEGORY_GREETING:
        return cat, float(GREETING_COOLDOWN_SECONDS), "GREETING_COOLDOWN"
    return None, 0.0, None
