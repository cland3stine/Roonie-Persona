from __future__ import annotations

from typing import Callable

# Phase 10D safety invariant: kill switch default must be ON.
DEFAULT_KILL_SWITCH_ON: bool = True


def maybe_post_nowplaying(
    *,
    gate_enabled: bool,
    gate_armed: bool,
    kill_switch: bool,
    mode: str,
    message: str,
    post_fn: Callable[[str], None],
) -> None:
    """
    Phase 10D: gated Twitch write path.

    Invariants:
      - If not enabled: never post
      - If not armed: never post
      - If kill switch ON: never post
      - If mode == "replay": never post (hard rule)
      - Otherwise (live + enabled + armed + kill switch OFF): post exactly once via post_fn
    """
    if not gate_enabled:
        return
    if not gate_armed:
        return
    if kill_switch:
        return
    if mode.strip().lower() == "replay":
        return

    post_fn(message)
