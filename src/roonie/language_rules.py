from __future__ import annotations

import re


DIRECT_VERBS = (
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


_GREETING_RE = re.compile(r"^(?:@[\w_]+\s*)?(?:hey|heya|hi|hello|yo|sup|what'?s up|whats up)\b", re.IGNORECASE)
_FOLLOWUP_RE = re.compile(
    r"\b(how|what|why|when|where|which|who|can|do|does|did|is|are)\b",
    re.IGNORECASE,
)


def _normalize_for_verb_tokens(text: str) -> list[str]:
    cleaned = re.sub(r"[^a-z0-9_]+", " ", str(text or "").lower()).strip()
    if not cleaned:
        return []
    return cleaned.split()


def starts_with_direct_verb(message: str) -> bool:
    tokens = _normalize_for_verb_tokens(message)
    if not tokens:
        return False
    return tokens[0] in DIRECT_VERBS


def contains_direct_verb_word(message: str) -> bool:
    tokens = _normalize_for_verb_tokens(message)
    if not tokens:
        return False
    return any(token in DIRECT_VERBS for token in tokens)


def is_pure_greeting_message(message: str) -> bool:
    text = str(message or "").strip()
    if not text:
        return False
    match = _GREETING_RE.search(text)
    if not match:
        return False
    tail = text[match.end() :].strip(" \t\r\n,!.?-")
    if not tail:
        return True
    if "?" in tail:
        return False
    if _FOLLOWUP_RE.search(tail):
        return False
    # Keep one-word tails ("hey there") in greeting bucket.
    return len(tail.split()) <= 2


def is_live_greeting_message(*, message: str, mode: str, platform: str) -> bool:
    mode_lower = str(mode or "").strip().lower()
    platform_lower = str(platform or "").strip().lower()
    if mode_lower != "live" and platform_lower != "twitch":
        return False
    return is_pure_greeting_message(message)
