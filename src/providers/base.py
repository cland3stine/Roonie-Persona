from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class Provider:
    """
    Phase 10E: Provider abstraction.

    Note:
      - This is an interface boundary, not a real model integration.
      - generate() must be deterministic in tests and may return None ("silent").
    """
    name: str
    enabled: bool

    def generate(self, *, prompt: str, context: Dict[str, Any]) -> Optional[str]:
        # Default behavior: silent (no output).
        return None
