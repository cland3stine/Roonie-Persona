from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.roonie.network import NetworkClient


@dataclass(frozen=True)
class DiscogsTrackMeta:
    release_id: int
    title: str
    year: Optional[int]
    label: Optional[str]
    catno: Optional[str]
    genres: List[str]
    styles: List[str]


class DiscogsEnricher:
    """
    Phase 9A: Discogs metadata enrichment (fixture-backed via injected transport).
    Deterministic selection:
    - exact case-insensitive match on "Artist - Title" against result.title
    - no fuzzy matching, no inference
    """

    def __init__(self, net: NetworkClient):
        self.net = net

    @staticmethod
    def _desired_title(artist: str, title: str) -> str:
        return f"{artist.strip()} - {title.strip()}"

    @staticmethod
    def _norm(s: str) -> str:
        return " ".join(s.strip().lower().split())

    def enrich_track(self, *, artist: str, title: str, fixture_name: str) -> Optional[DiscogsTrackMeta]:
        # In Phase 9A we do not build real URLs; we just call through the network boundary.
        # URL is informational only in fake transport mode.
        url = "https://api.discogs.local/database/search"
        body = self.net.get_json(url, fixture_name=fixture_name)

        results = []
        if isinstance(body, dict):
            results = body.get("results") or []

        if not isinstance(results, list):
            return None

        desired = self._norm(self._desired_title(artist, title))

        for r in results:
            if not isinstance(r, dict):
                continue
            rt = r.get("title")
            if isinstance(rt, str) and self._norm(rt) == desired:
                return DiscogsTrackMeta(
                    release_id=int(r.get("id")),
                    title=rt,
                    year=int(r["year"]) if isinstance(r.get("year"), int) else None,
                    label=(r.get("label") or [None])[0] if isinstance(r.get("label"), list) else None,
                    catno=r.get("catno") if isinstance(r.get("catno"), str) else None,
                    genres=[x for x in (r.get("genre") or []) if isinstance(x, str)],
                    styles=[x for x in (r.get("style") or []) if isinstance(x, str)],
                )

        return None
