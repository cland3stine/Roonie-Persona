from __future__ import annotations

from pathlib import Path
from typing import Optional

from metadata.discogs import DiscogsEnricher
from nowplaying.bridge import build_chat_lines_from_nowplaying_txt


def _read_nowplaying_txt(path: Path) -> str:
    # Defensive read for network shares / atomic rewrites:
    # if content looks incomplete, retry once.
    txt = path.read_text(encoding="utf-8")
    lines = [ln for ln in txt.splitlines() if ln.strip() != ""]
    if len(lines) < 2:
        # retry once
        txt2 = path.read_text(encoding="utf-8")
        return txt2
    return txt


def _atomic_write_text(dest: Path, content: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(dest)


def run_nowplaying_oneshot(
    *,
    overlay_dir: Path,
    enricher: DiscogsEnricher,
    discogs_fixture_name: Optional[str] = None,
) -> None:
    """
    Phase 10B:
    One-shot bridge:
    - Read overlay_dir/nowplaying.txt (2 lines)
    - Produce chat-ready lines (current + previous)
    - Write overlay_dir/nowplaying_chat.txt and overlay_dir/previous_chat.txt atomically
    """
    src = overlay_dir / "nowplaying.txt"
    nowplaying_txt = _read_nowplaying_txt(src)

    cur_line, prev_line = build_chat_lines_from_nowplaying_txt(
        nowplaying_txt=nowplaying_txt,
        enricher=enricher,
        discogs_fixture_name=discogs_fixture_name,
    )

    _atomic_write_text(overlay_dir / "nowplaying_chat.txt", cur_line + "\n")
    _atomic_write_text(overlay_dir / "previous_chat.txt", prev_line + "\n")
