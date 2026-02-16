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
GREETING_COOLDOWN_SECONDS = 25.0


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
    base = [
        "Behavior policy:",
        "- Keep reply short and warm (1-2 sentences).",
        "- Prefer clean, clear language; keep slang occasional.",
        "- Do not repeat usernames excessively.",
        "- Do not add unsolicited commentary.",
        "- Maintain continuity with recent chat context.",
    ]
    if approved_emotes:
        base.append(
            f"- Use at most one approved emote if natural: {', '.join(approved_emotes)}."
        )
    else:
        base.append("- Do not add emotes unless explicitly approved.")

    if category == CATEGORY_TRACK_ID:
        base.append("- Track-ID mode: do not invent track names.")
        if now_playing_available:
            base.append("- Use now-playing metadata if available.")
        else:
            base.append("- If now-playing data is unavailable, ask for timestamp/clip.")
    elif category in EVENT_COOLDOWN_SECONDS:
        base.append("- Event acknowledgement mode: thank briefly and move on.")
    elif category == CATEGORY_GREETING:
        base.append("- Greeting mode: friendly, restrained, brief.")
    elif category == CATEGORY_BANTER:
        base.append("- Banter mode: answer naturally and briefly.")
        base.append("- If the viewer references earlier chat, continue that thread.")
        base.append("- Do not invent artist or track names; ask a short clarifying question if unsure.")
    if topic_anchor:
        base.append(f"- Active topic anchor: {topic_anchor}. Use this before introducing new names.")
    return "\n".join(base)


def cooldown_for_category(category: str) -> Tuple[Optional[str], float, Optional[str]]:
    cat = str(category or "").strip().upper()
    if cat in EVENT_COOLDOWN_SECONDS:
        return cat, float(EVENT_COOLDOWN_SECONDS[cat]), "EVENT_COOLDOWN"
    if cat == CATEGORY_GREETING:
        return cat, float(GREETING_COOLDOWN_SECONDS), "GREETING_COOLDOWN"
    return None, 0.0, None
