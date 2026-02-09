from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional


@dataclass
class App:
    """
    Phase 10J: packaged app shell.

    This is a deterministic, testable wrapper. Real IO loops and services are out of scope;
    callers run run_tick() repeatedly if desired.
    """
    cfg: Dict[str, Any]
    post_fn: Callable[[str], None]

    def run_tick(self) -> None:
        # Packaging safety: run_tick() must be side-effect-free unless explicitly permitted later.
        # For Phase 10J tests, we do nothing. Future phases can wire real behavior under strict gates.
        return


def build_app(cfg: Dict[str, Any], post_fn: Optional[Callable[[str], None]] = None) -> App:
    """
    Build the app with injected dependencies. Defaults are safe.
    """
    if post_fn is None:
        # Default post_fn is a no-op to ensure silence by default.
        def _noop(_msg: str) -> None:
            return
        post_fn = _noop

    return App(cfg=cfg, post_fn=post_fn)


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="roonie",
        description="ROONIE-AI v1 packaged entrypoint (Phase 10J).",
    )
    p.add_argument("--config", default=None, help="Optional path to config JSON (not used in tests).")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    # Minimal entrypoint; production config loading can be expanded later without changing core invariants.
    _ = _build_arg_parser().parse_args(argv)
    return 0
