from __future__ import annotations

from typing import Tuple, Optional

from src.metadata.discogs import DiscogsEnricher


def _parse_track_id(track_id: str) -> Optional[tuple[str, str]]:
    # Expect "Artist - Title"
    if " - " not in track_id:
        return None
    artist, title = track_id.split(" - ", 1)
    artist = artist.strip()
    title = title.strip()
    if not artist or not title:
        return None
    return artist, title


def _render_basic(track_id: str) -> str:
    return f"Now Playing: {track_id.strip()}"


def _render_enriched(track_id: str, *, year: int, label: str) -> str:
    return f"Now Playing: {track_id.strip()} (Released {year} on {label})"


def build_chat_lines_from_nowplaying_txt(
    *,
    nowplaying_txt: str,
    enricher: DiscogsEnricher,
    discogs_fixture_name: str | None = None,
) -> Tuple[str, str]:
    """
    Phase 10A (pure):
    - Input: contents of nowplaying.txt (2 lines: current, previous)
    - Output: (current_chat_line, previous_chat_line)
    - Deterministic: no wall-clock, no IO, no side effects
    """
    lines = [ln.strip() for ln in nowplaying_txt.splitlines() if ln.strip() != ""]
    if len(lines) < 2:
        # Fallback behavior: treat missing lines as empty
        cur = lines[0] if lines else ""
        prev = ""
    else:
        cur, prev = lines[0], lines[1]

    def enrich_or_basic(track_id: str) -> str:
        if not track_id:
            return "Now Playing: "
        parsed = _parse_track_id(track_id)
        if not parsed:
            return _render_basic(track_id)

        artist, title = parsed
        meta = enricher.enrich_track(artist=artist, title=title, fixture_name=discogs_fixture_name)
        if meta is None or meta.year is None or meta.label is None:
            return _render_basic(track_id)

        return _render_enriched(track_id, year=meta.year, label=meta.label)

    return enrich_or_basic(cur), enrich_or_basic(prev)
