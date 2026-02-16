from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable, Optional

from metadata.discogs import DiscogsEnricher
from nowplaying.oneshot import run_nowplaying_oneshot


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _read_for_hash(path: Path) -> str:
    """
    Read nowplaying.txt content for hashing.

    We do NOT use mtime. If the file is momentarily incomplete (e.g., partial write),
    we retry once. This keeps the daemon stable in live usage while remaining deterministic.
    """
    try:
        txt = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""

    lines = [ln for ln in txt.splitlines() if ln.strip() != ""]
    if len(lines) < 2:
        # retry once
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    return txt


def run_nowplaying_daemon(
    *,
    overlay_dir: Path,
    enricher: DiscogsEnricher,
    discogs_fixture_name: Optional[str] = None,
    poll_interval_seconds: float = 0.25,
    max_ticks: Optional[int] = None,
    sleep_fn: Optional[Callable[[float], None]] = None,
) -> None:
    """
    Phase 10C: poll overlay_dir/nowplaying.txt and update chat files on content change.

    Testability:
      - max_ticks bounds the loop deterministically
      - sleep_fn is injectable (tests pass a no-op)
    """
    if sleep_fn is None:
        import time
        sleep_fn = time.sleep

    src = overlay_dir / "nowplaying.txt"
    last_hash: Optional[str] = None
    ticks = 0

    while True:
        txt = _read_for_hash(src)
        h = _hash_text(txt)

        if h != last_hash:
            run_nowplaying_oneshot(
                overlay_dir=overlay_dir,
                enricher=enricher,
                discogs_fixture_name=discogs_fixture_name,
            )
            last_hash = h

        ticks += 1
        if max_ticks is not None and ticks >= max_ticks:
            return

        sleep_fn(poll_interval_seconds)
