from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Any, Callable, Deque, Dict, List, Literal, Optional

Speaker = Literal["user", "roonie"]

_INTERROGATIVE_RE = re.compile(r"^(what|why|how|where|when|can|do|does|is|are)\b", re.IGNORECASE)
_LEADING_MENTION_RE = re.compile(r"^@\w+\s+")
_UTILITY_CATEGORIES = {
    "utility_track_id",
    "utility_gear",
    "utility_library",
    "courtesy",
    "operator_queue",
}


@dataclass(frozen=True)
class ContextTurn:
    ts: str
    speaker: Speaker
    text: str
    tags: Dict[str, Any] = field(default_factory=dict)


class ContextBuffer:
    """
    Deterministic in-memory context ring buffer.
    Bounded, process-local, and explicitly non-persistent.
    """

    def __init__(self, *, max_turns: int = 12, now_fn: Optional[Callable[[], datetime]] = None) -> None:
        self._max_turns = max(1, int(max_turns))
        self._turns: Deque[ContextTurn] = deque(maxlen=self._max_turns)
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def _now_iso(self) -> str:
        return self._now_fn().isoformat()

    @staticmethod
    def _is_user_relevant(*, text: str, direct_address: bool, category: str) -> bool:
        if direct_address:
            return True
        if "?" in text:
            return True
        probe = _LEADING_MENTION_RE.sub("", text.strip())
        if _INTERROGATIVE_RE.match(probe):
            return True
        if category in _UTILITY_CATEGORIES:
            return True
        return False

    def add_turn(
        self,
        *,
        speaker: Speaker,
        text: str,
        tags: Optional[Dict[str, Any]] = None,
        ts: Optional[str] = None,
        sent: bool = False,
        related_to_stored_user: bool = False,
    ) -> bool:
        """
        Adds a turn only when deterministic relevance gates pass.
        Returns True if stored, False if discarded.
        """
        speaker_norm = str(speaker).strip().lower()
        if speaker_norm not in {"user", "roonie"}:
            raise ValueError("speaker must be 'user' or 'roonie'")

        text_norm = str(text or "").strip()
        if not text_norm:
            return False

        incoming = dict(tags or {})
        direct_address = bool(incoming.get("direct_address", False))
        category = str(incoming.get("category", "")).strip().lower()

        if speaker_norm == "user":
            if not self._is_user_relevant(text=text_norm, direct_address=direct_address, category=category):
                return False
        else:
            if not sent:
                return False
            if not related_to_stored_user:
                return False
            if not any(t.speaker == "user" for t in self._turns):
                return False

        stored_tags: Dict[str, Any] = {}
        if "direct_address" in incoming:
            stored_tags["direct_address"] = bool(incoming["direct_address"])
        if category:
            stored_tags["category"] = category

        turn = ContextTurn(
            ts=ts or self._now_iso(),
            speaker=speaker_norm,  # type: ignore[arg-type]
            text=text_norm,
            tags=stored_tags,
        )
        self._turns.append(turn)
        return True

    def get_context(self, max_turns: int = 3) -> List[ContextTurn]:
        count = max(0, min(int(max_turns), self._max_turns))
        if count == 0:
            return []
        # Newest first for deterministic prompt packing.
        return list(reversed(list(self._turns)[-count:]))

    def clear(self) -> None:
        self._turns.clear()
