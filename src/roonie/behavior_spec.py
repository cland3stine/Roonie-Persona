from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from roonie.language_rules import is_pure_greeting_message


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


_TRACK_ID_RE = re.compile(
    r"\b(track\s*id|what(?:'s| is)?\s+(?:this|that)\s+track|id\?|what\s+track|track\?)\b",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(r"\?")


def classify_behavior_category(*, message: str, metadata: Dict[str, Any]) -> str:
    event_type = str(metadata.get("event_type", "")).strip().upper()
    if event_type in EVENT_TYPE_TO_CATEGORY:
        return EVENT_TYPE_TO_CATEGORY[event_type]

    text = str(message or "").strip()
    if not text:
        return CATEGORY_OTHER
    if _TRACK_ID_RE.search(text):
        return CATEGORY_TRACK_ID
    if is_pure_greeting_message(text):
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
    short_ack_preferred: bool = False,
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
        if short_ack_preferred:
            lines.append("Viewer shared a status update without a question. Reply with one short acknowledgment sentence.")
            lines.append("Do not force a follow-up question unless it's clearly needed.")
        if topic_anchor:
            lines.append(f"Recent topic: {topic_anchor}. Pick up the thread if relevant.")
        lines.append("Chat naturally. Be warm, react to what they actually said. Light teasing with people you know well is welcome if the moment is right.")
        lines.append("If the recent chat shows you repeating the same joke or theme, drop it and respond fresh to what the viewer just said.")
    if topic_anchor and category != CATEGORY_BANTER:
        lines.append(f"Recent topic: {topic_anchor}. Pick up the thread if relevant.")
    if approved_emotes:
        lines.append(f"Approved emotes: {', '.join(approved_emotes)}")
    return "\n".join(lines) if lines else ""


def cooldown_for_category(category: str) -> Tuple[Optional[str], float, Optional[str]]:
    cat = str(category or "").strip().upper()
    if cat in EVENT_COOLDOWN_SECONDS:
        return cat, float(EVENT_COOLDOWN_SECONDS[cat]), "EVENT_COOLDOWN"
    if cat == CATEGORY_GREETING:
        return cat, float(GREETING_COOLDOWN_SECONDS), "GREETING_COOLDOWN"
    return None, 0.0, None
