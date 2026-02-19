from __future__ import annotations

import re
from typing import Optional, Tuple

from roonie.types import SafetyClassification

# Strip common prompt-injection wrappers before policy checks.
_INJECTION_PREFIX_PATTERNS = (
    re.compile(r"^\s*\[(?:system|assistant|user|inst)[^\]]*\]\s*", re.IGNORECASE),
    re.compile(r"^\s*</?(?:system|assistant|user|inst|s)\b[^>]*>\s*", re.IGNORECASE),
)

_REFUSE_PATTERNS = (
    # Direct personal/contact info
    re.compile(r"\b(?:your|my|his|her|their)\s+address\b", re.IGNORECASE),
    re.compile(r"\b(?:home|house|street|mailing)\s+address\b", re.IGNORECASE),
    re.compile(r"\b(?:phone|cell|mobile|telephone)\s+(?:number|#)\b", re.IGNORECASE),
    re.compile(r"\b(?:real|full|legal)\s+name\b", re.IGNORECASE),
    re.compile(r"\b(?:email|e-mail)\s+address\b", re.IGNORECASE),
    # Explicit doxxing/network asks
    re.compile(r"\b(?:doxx?|doxing|doxxing)\b", re.IGNORECASE),
    re.compile(r"\b(?:ip|ip\s+address|ipv4|ipv6)\b", re.IGNORECASE),
)

_SENSITIVE_PATTERNS = (
    re.compile(r"\bdepress(?:ed|ion)?\b", re.IGNORECASE),
    re.compile(r"\bsuicid(?:e|al)\b", re.IGNORECASE),
    re.compile(r"\bself[-\s]?harm\b", re.IGNORECASE),
    re.compile(r"\bkill\s+myself\b", re.IGNORECASE),
    re.compile(r"\b(?:want|wanna)\s+to\s+die\b", re.IGNORECASE),
    re.compile(r"\bend\s+my\s+life\b", re.IGNORECASE),
)


def normalize_for_policy(message: str) -> str:
    text = str(message or "").strip()
    if not text:
        return ""
    while text:
        changed = False
        for pattern in _INJECTION_PREFIX_PATTERNS:
            updated = pattern.sub("", text)
            if updated != text:
                text = updated.strip()
                changed = True
        if not changed:
            break
    return re.sub(r"\s+", " ", text).strip()


def classify_message_safety(message: str) -> Tuple[SafetyClassification, Optional[str]]:
    normalized = normalize_for_policy(message)
    if not normalized:
        return "allowed", None
    if any(pattern.search(normalized) for pattern in _REFUSE_PATTERNS):
        return "refuse", "REF_PRIVATE_INFO_DOXXING"
    if any(pattern.search(normalized) for pattern in _SENSITIVE_PATTERNS):
        return "sensitive_no_followup", None
    return "allowed", None
