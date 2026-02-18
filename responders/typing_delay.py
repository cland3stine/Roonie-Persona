from __future__ import annotations

import os
import random


def compute_typing_delay(text: str) -> float:
    """Return seconds to wait before sending *text*, simulating paw-typing.

    Model: ~1.5 s read/think  +  len(text) / 12 chars-per-second,
    then ±25 % uniform jitter, clamped to [2.0, 12.0].
    Disabled when ROONIE_TYPING_DELAY_ENABLED == "0".
    """
    if os.getenv("ROONIE_TYPING_DELAY_ENABLED", "1").strip() == "0":
        return 0.0

    base_think = 1.5
    typing_speed = 12.0  # chars per second
    raw = base_think + len(text) / typing_speed

    jitter = random.uniform(-0.25, 0.25)
    delay = raw * (1.0 + jitter)

    return max(2.0, min(delay, 12.0))
