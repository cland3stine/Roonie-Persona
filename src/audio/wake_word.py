"""Wake-word detection on Whisper transcription output.

Matches "Roonie" and common Whisper mis-hearings (runi, roomie, runie, etc.)
using regex patterns. Returns the trigger phrase and the remaining text that
should be sent to the LLM as the user's actual message.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class WakeWordResult:
    """Result of wake-word detection on a transcription string."""

    detected: bool
    trigger_phrase: str
    remaining_text: str
    confidence: float


# Patterns ordered by specificity. Each tuple: (compiled regex, confidence).
_PATTERNS: list[tuple[re.Pattern[str], float]] = [
    (re.compile(r"\b(?:hey\s+)?roonie\b", re.IGNORECASE), 1.0),
    (re.compile(r"\b(?:hey\s+)?runi\b", re.IGNORECASE), 0.85),
    (re.compile(r"\b(?:hey\s+)?runie\b", re.IGNORECASE), 0.80),
    (re.compile(r"\b(?:hey\s+)?rooney\b", re.IGNORECASE), 0.75),
    (re.compile(r"\b(?:hey\s+)?roomie\b", re.IGNORECASE), 0.70),
]

_NOT_DETECTED = WakeWordResult(detected=False, trigger_phrase="", remaining_text="", confidence=0.0)


class WakeWordDetector:
    """Detect the wake word in a transcription string."""

    def __init__(self, *, patterns: list[tuple[re.Pattern[str], float]] | None = None) -> None:
        self._patterns = patterns if patterns is not None else _PATTERNS

    def detect(self, text: str) -> WakeWordResult:
        """Check *text* for a wake-word match.

        Returns the first match found (highest-confidence patterns are checked
        first). ``remaining_text`` is everything after the matched trigger,
        stripped of leading whitespace and punctuation.
        """
        if not text or not text.strip():
            return _NOT_DETECTED

        for pattern, confidence in self._patterns:
            match = pattern.search(text)
            if match:
                trigger = match.group(0)
                after = text[match.end():]
                # Strip leading whitespace / punctuation after the trigger.
                remaining = after.lstrip(" ,;:-").strip()
                return WakeWordResult(
                    detected=True,
                    trigger_phrase=trigger,
                    remaining_text=remaining,
                    confidence=confidence,
                )

        return _NOT_DETECTED
