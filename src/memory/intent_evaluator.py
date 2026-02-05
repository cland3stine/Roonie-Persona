from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

_CUES: List[Tuple[str, str, int, float]] = [
    ("i love", "like", 180, 0.9),
    ("i like", "like", 180, 0.7),
    ("my favorite", "like", 180, 0.9),
    ("i hate", "dislike", 365, 0.9),
    ("i can't stand", "dislike", 365, 0.95),
    ("not a fan of", "dislike", 365, 0.8),
]

_BLOCKLIST_PATTERNS = [
    r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b",  # ssn-like
    r"\b\d{10,}\b",  # long numbers
    r"\b(?:street|st\.?|avenue|ave\.?|road|rd\.?|drive|dr\.?|lane|ln\.?)\b",
    r"\b(?:phone|cell|email|e-mail|address|addr|ip)\b",
    r"\b(?:password|passcode|pin|credit card|cvv)\b",
]

_SENSITIVE_TOPICS = [
    r"\b(?:depressed|suicidal|self harm|kill myself|anxious|anxiety|bipolar|schizophrenia)\b",
    r"\b(?:election|politics|political|president|senator|congress)\b",
    r"\b(?:religion|religious|church|mosque|synagogue|atheist|muslim|christian|jewish|hindu|buddhist)\b",
]

_STOP_TOKENS = [" but ", " though ", " however "]


def _normalize_object(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"[^\w\s]+", "", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()

def _extract_object(text: str, cue: str) -> Optional[str]:
    idx = text.find(cue)
    if idx == -1:
        return None
    tail = text[idx + len(cue) :].strip()
    if tail.startswith("is "):
        tail = tail[3:]
    elif tail.startswith("are "):
        tail = tail[4:]
    if tail.startswith("the "):
        tail = tail[4:]
    if tail.startswith("to "):
        tail = tail[3:]

    for token in _STOP_TOKENS:
        cut = tail.find(token)
        if cut != -1:
            tail = tail[:cut]
            break

    tail = re.split(r"[.!?;]\\s*", tail, maxsplit=1)[0].strip()
    tail = tail.strip(" \t\n\r\"'“”’,")
    if not tail:
        return None
    if len(tail) > 80:
        tail = tail[:80].rstrip()
    return tail


def _is_blocked(text: str) -> bool:
    lowered = text.lower()
    for pat in _BLOCKLIST_PATTERNS:
        if re.search(pat, lowered, flags=re.IGNORECASE):
            return True
    for pat in _SENSITIVE_TOPICS:
        if re.search(pat, lowered, flags=re.IGNORECASE):
            return True
    return False

def evaluate_memory_intents(event: Dict[str, Any]) -> List[Dict[str, Any]]:
    message = (event.get("message") or "").strip()
    if not message:
        return []

    metadata = event.get("metadata", {}) or {}
    user = metadata.get("user")
    if not user:
        return []

    lowered = message.lower()
    if _is_blocked(lowered):
        return []

    # Collect all cue occurrences (deterministic)
    found: List[Dict[str, Any]] = []
    for cue, pref, ttl_days, confidence in _CUES:
        idx = lowered.find(cue)
        if idx == -1:
            continue
        obj = _extract_object(lowered, cue)
        if not obj:
            continue
        if _is_blocked(obj):
            continue
        found.append(
            {
                "idx": idx,
                "cue": cue,
                "preference": pref,
                "ttl_days": ttl_days,
                "confidence": confidence,
                "object": obj,
            }
        )

    if not found:
        return []

    has_contrast = any(tok in lowered for tok in _STOP_TOKENS)
    likes = [x for x in found if x["preference"] == "like"]
    dislikes = [x for x in found if x["preference"] == "dislike"]

    reason: Optional[str] = None
    chosen = min(found, key=lambda x: x["idx"])  # default: first match

    if has_contrast and likes and dislikes:
        cut_idx = -1
        for tok in _STOP_TOKENS:
            i = lowered.find(tok)
            if i != -1 and (cut_idx == -1 or i < cut_idx):
                cut_idx = i

        if cut_idx != -1:
            left_dislikes = [x for x in dislikes if x["idx"] < cut_idx]
            right_likes = [x for x in likes if x["idx"] > cut_idx]
            left_dislike = max(left_dislikes, key=lambda x: x["idx"]) if left_dislikes else None
            right_like = min(right_likes, key=lambda x: x["idx"]) if right_likes else None

            if left_dislike and right_like:
                left_obj = left_dislike["object"]
                right_obj = right_like["object"]
                if left_obj and (
                    _normalize_object(left_obj) == _normalize_object(right_obj)
                    or _normalize_object(right_obj) in {"", "it", "this", "that"}
                ):
                    reason = "overwrite"
                    chosen = right_like
    intent_trace: Dict[str, Any] = {
        "scope": "viewer",
        "user": user,
        "preference": chosen["preference"],
        "object": chosen["object"],
        "confidence": chosen["confidence"],
        "ttl_days": chosen["ttl_days"],
        "cue": chosen["cue"],
    }
    if reason:
        intent_trace["reason"] = reason

    return [
        {
            "case_id": event.get("case_id", ""),
            "event_id": event.get("event_id", ""),
            "action": "MEMORY_WRITE_INTENT",
            "route": "none",
            "response_text": None,
            "trace": {"memory_intent": intent_trace},
        }
    ]
