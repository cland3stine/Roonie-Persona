"""TrackrBridge — polls TRACKR's HTTP API for current/previous track data
and pushes state into DashboardStorage.

Architecture: daemon thread following the EventSubBridge/AudioInputBridge
pattern.  Detects track changes by comparing the ``current`` line against
the last known value.  On track change, enriches via Discogs API (label,
year, genres, styles) and stores enrichment alongside track state.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_track_line(raw: str) -> Dict[str, str]:
    """Parse 'Artist - Title' into components.

    Returns dict with ``raw``, ``artist``, ``title``.  If the line doesn't
    contain a dash separator, ``artist`` is empty and ``title`` is the
    full line.
    """
    line = str(raw or "").strip()
    if not line:
        return {"raw": "", "artist": "", "title": ""}
    # Normalize dashes (em-dash, en-dash → hyphen-minus)
    normalized = line.replace("\u2014", "-").replace("\u2013", "-")
    parts = normalized.split(" - ", 1)
    if len(parts) == 2:
        return {
            "raw": line,
            "artist": parts[0].strip(),
            "title": parts[1].strip(),
        }
    return {"raw": line, "artist": "", "title": line}


class TrackrBridge:
    """Threaded bridge: TRACKR HTTP API → DashboardStorage state.

    Parameters
    ----------
    storage : DashboardStorage
        Dashboard storage instance (reads config, stores runtime state).
    logger : callable, optional
        Logging function matching the control-room ``_append_log`` signature.
    """

    def __init__(
        self,
        *,
        storage: Any,
        logger: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._storage = storage
        self._ext_logger = logger
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._enricher: Any = None
        self._current_enrichment: Dict[str, Any] = {}
        self._previous_enrichment: Dict[str, Any] = {}
        self._init_enricher()

    # ── Discogs enrichment ─────────────────────────────────────

    def _init_enricher(self) -> None:
        try:
            from pathlib import Path
            from roonie.config import load_config
            from roonie.network import NetworkClient
            from roonie.network.transports_urllib import UrllibJsonTransport
            from metadata.discogs import DiscogsEnricher

            base_dir = Path(__file__).resolve().parent.parent.parent.parent
            cfg = load_config(base_dir)
            if cfg.network_enabled and cfg.discogs_token:
                transport = UrllibJsonTransport(user_agent="ROONIE-AI/0.1")
                net = NetworkClient(cfg=cfg, transport=transport)
                self._enricher = DiscogsEnricher(net=net, token=cfg.discogs_token)
        except Exception:
            self._enricher = None

    def _enrich_track(self, artist: str, title: str) -> Dict[str, Any]:
        if not self._enricher or not artist or not title:
            return {}
        try:
            meta = self._enricher.enrich_track(
                artist=artist, title=title, fixture_name=None,
            )
            if meta is None:
                return {}
            result: Dict[str, Any] = {}
            if meta.year:
                result["year"] = meta.year
            if meta.label:
                result["label"] = meta.label
            if meta.genres:
                result["genres"] = list(meta.genres)
            if meta.styles:
                result["styles"] = list(meta.styles)
            if meta.catno:
                result["catno"] = meta.catno
            return result
        except Exception as exc:
            self._log(f"[TrackrBridge] discogs enrich error: {exc}")
            return {}

    # ── public API ───────────────────────────────────────────────

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="roonie-trackr-bridge", daemon=True,
        )
        self._thread.start()
        self._log("[TrackrBridge] started")

    def stop(self) -> None:
        self._stop.set()
        self._log("[TrackrBridge] stop requested")

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── logging ─────────────────────────────────────────────────

    def _log(self, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            return
        line = f"{_utc_now_iso()} {text}"
        if callable(self._ext_logger):
            try:
                self._ext_logger(line)
                return
            except Exception:
                pass
        print(line)

    # ── core loop ───────────────────────────────────────────────

    def _run(self) -> None:
        last_current = ""
        last_previous = ""
        consecutive_errors = 0

        while not self._stop.is_set():
            config = {}
            if hasattr(self._storage, "get_trackr_config"):
                config = self._storage.get_trackr_config()

            enabled = bool(config.get("enabled", False))
            if not enabled:
                self._push_state(connected=False, enabled=False)
                self._stop.wait(5.0)
                continue

            api_url = str(config.get("api_url", "http://127.0.0.1:8755")).rstrip("/")
            poll_interval = float(config.get("poll_interval_seconds", 3.0))
            poll_interval = max(1.0, min(poll_interval, 30.0))

            try:
                data = self._fetch_trackr(api_url)
                consecutive_errors = 0
            except Exception as exc:
                consecutive_errors += 1
                if consecutive_errors <= 3 or consecutive_errors % 20 == 0:
                    self._log(f"[TrackrBridge] fetch error ({consecutive_errors}): {exc}")
                self._push_state(
                    connected=False,
                    enabled=True,
                    error=str(exc),
                    last_current=last_current,
                    last_previous=last_previous,
                )
                self._stop.wait(poll_interval)
                continue

            current_raw = str(data.get("current", "")).strip()
            previous_raw = str(data.get("previous", "")).strip()
            is_running = bool(data.get("is_running", False))
            device_count = int(data.get("device_count", 0))

            # Detect track change
            if current_raw and current_raw != last_current:
                current_parsed = _parse_track_line(current_raw)
                artist = current_parsed.get("artist", "")
                title = current_parsed.get("title", "")
                self._log(
                    f"[TrackrBridge] track change: {artist} - {title}"
                )
                # Shift current enrichment to previous before enriching new track
                self._previous_enrichment = dict(self._current_enrichment)
                self._current_enrichment = self._enrich_track(artist, title)
                if self._current_enrichment:
                    self._log(
                        f"[TrackrBridge] enriched: {self._current_enrichment}"
                    )
                last_current = current_raw

            if previous_raw != last_previous:
                last_previous = previous_raw

            self._push_state(
                connected=True,
                enabled=True,
                is_running=is_running,
                device_count=device_count,
                last_current=current_raw,
                last_previous=previous_raw,
            )

            self._stop.wait(poll_interval)

        self._push_state(connected=False, enabled=False)
        self._log("[TrackrBridge] stopped")

    # ── HTTP fetch ──────────────────────────────────────────────

    @staticmethod
    def _fetch_trackr(api_url: str) -> Dict[str, Any]:
        """GET /trackr from TRACKR API. Returns parsed JSON."""
        url = f"{api_url}/trackr"
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "ROONIE-AI/0.1"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)

    # ── state push ──────────────────────────────────────────────

    def _push_state(
        self,
        *,
        connected: bool = False,
        enabled: bool = False,
        is_running: bool = False,
        device_count: int = 0,
        last_current: str = "",
        last_previous: str = "",
        error: str = "",
    ) -> None:
        if not hasattr(self._storage, "set_trackr_state"):
            return
        current_parsed = _parse_track_line(last_current) if last_current else {}
        previous_parsed = _parse_track_line(last_previous) if last_previous else {}
        state: Dict[str, Any] = {
            "connected": connected,
            "enabled": enabled,
            "trackr_running": is_running,
            "device_count": device_count,
            "current": current_parsed,
            "previous": previous_parsed,
            "error": error or None,
            "updated_at": _utc_now_iso(),
        }
        if self._current_enrichment:
            state["current_enrichment"] = dict(self._current_enrichment)
        if self._previous_enrichment:
            state["previous_enrichment"] = dict(self._previous_enrichment)
        self._storage.set_trackr_state(state)
