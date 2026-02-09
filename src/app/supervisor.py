from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class SupervisorConfig:
    max_crashes: int = 3


def run_supervised(*, run_once: Callable[[], None], cfg: Optional[SupervisorConfig] = None) -> None:
    """
    Phase 10J: minimal crash-safety supervisor.
    Deterministic and side-effect-free by design (caller supplies run_once).
    """
    if cfg is None:
        cfg = SupervisorConfig()

    crashes = 0
    while True:
        try:
            run_once()
            return
        except Exception:
            crashes += 1
            if crashes >= cfg.max_crashes:
                raise
