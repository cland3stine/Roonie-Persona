from __future__ import annotations

import json
import random
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional


_EVENTSUB_WS_URL = "wss://eventsub.wss.twitch.tv/ws"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_eventsub_notification(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(message, dict):
        return None
    metadata = message.get("metadata", {})
    payload = message.get("payload", {})
    if not isinstance(metadata, dict) or not isinstance(payload, dict):
        return None
    if str(metadata.get("message_type", "")).strip().lower() != "notification":
        return None
    subscription = payload.get("subscription", {})
    event = payload.get("event", {})
    if not isinstance(subscription, dict) or not isinstance(event, dict):
        return None

    raw_type = str(subscription.get("type", "")).strip().lower()
    message_id = str(metadata.get("message_id", "")).strip()
    timestamp = (
        str(event.get("followed_at", "")).strip()
        or str(event.get("started_at", "")).strip()
        or str(metadata.get("message_timestamp", "")).strip()
        or _utc_now_iso()
    )

    base: Dict[str, Any] = {
        "event_type": "UNKNOWN",
        "raw_type": raw_type,
        "twitch_event_id": message_id,
        "user_login": str(event.get("user_login", "")).strip() or None,
        "display_name": str(event.get("user_name", "")).strip() or None,
        "amount": None,
        "tier": None,
        "months": None,
        "raid_viewer_count": None,
        "timestamp": timestamp,
    }

    if raw_type == "channel.follow":
        base["event_type"] = "FOLLOW"
    elif raw_type == "channel.subscribe":
        base["event_type"] = "SUB"
        base["tier"] = str(event.get("tier", "")).strip() or None
        try:
            months = int(event.get("cumulative_months", 0) or 0)
        except (TypeError, ValueError):
            months = 0
        base["months"] = months
    elif raw_type == "channel.cheer":
        base["event_type"] = "CHEER"
        try:
            base["amount"] = int(event.get("bits", 0) or 0)
        except (TypeError, ValueError):
            base["amount"] = 0
    elif raw_type == "channel.raid":
        base["event_type"] = "RAID"
        base["user_login"] = str(
            event.get("from_broadcaster_user_login", event.get("user_login", ""))
        ).strip() or None
        base["display_name"] = str(
            event.get("from_broadcaster_user_name", event.get("user_name", ""))
        ).strip() or None
        try:
            base["raid_viewer_count"] = int(event.get("viewers", 0) or 0)
        except (TypeError, ValueError):
            base["raid_viewer_count"] = 0
    else:
        return None
    return base


class EventSubWSClient:
    def __init__(
        self,
        *,
        oauth_token: str,
        client_id: str,
        broadcaster_user_id: str,
        on_event: Callable[[Dict[str, Any]], None],
        on_state: Optional[Callable[[Dict[str, Any]], None]] = None,
        logger: Optional[Callable[[str], None]] = None,
        ws_url: str = _EVENTSUB_WS_URL,
        ws_factory: Optional[Callable[[str], Any]] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        time_fn: Callable[[], float] = time.monotonic,
        random_fn: Callable[[], float] = random.random,
        dedupe_ttl_seconds: float = 120.0,
        backoff_initial_seconds: float = 1.0,
        backoff_max_seconds: float = 30.0,
    ) -> None:
        self._oauth_token = str(oauth_token or "").strip()
        self._client_id = str(client_id or "").strip()
        self._broadcaster_user_id = str(broadcaster_user_id or "").strip()
        self._on_event = on_event
        self._on_state = on_state
        self._logger = logger
        self._ws_url = str(ws_url or _EVENTSUB_WS_URL).strip() or _EVENTSUB_WS_URL
        self._ws_factory = ws_factory or self._default_ws_factory
        self._sleep_fn = sleep_fn
        self._time_fn = time_fn
        self._random_fn = random_fn
        self._dedupe_ttl_seconds = max(5.0, float(dedupe_ttl_seconds))
        self._backoff_initial_seconds = max(0.1, float(backoff_initial_seconds))
        self._backoff_max_seconds = max(self._backoff_initial_seconds, float(backoff_max_seconds))

        self._stop = threading.Event()
        self._seen_event_ids: Dict[str, float] = {}
        self._state: Dict[str, Any] = {
            "eventsub_connected": False,
            "eventsub_session_id": None,
            "last_eventsub_message_ts": None,
            "reconnect_count": 0,
            "eventsub_last_error": None,
        }
        self._current_ws: Optional[Any] = None
        self._current_ws_lock = threading.Lock()

    @staticmethod
    def _default_ws_factory(url: str) -> Any:
        try:
            import websocket  # type: ignore
        except Exception as exc:  # pragma: no cover - env dependent
            raise RuntimeError(
                "EventSub WebSocket client unavailable: install websocket-client to enable EventSub."
            ) from exc
        return websocket.create_connection(url, timeout=20)

    def _log(self, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            return
        line = f"{_utc_now_iso()} {text}"
        if callable(self._logger):
            try:
                self._logger(line)
                return
            except Exception:
                pass
        print(line)

    def stop(self) -> None:
        self._stop.set()
        with self._current_ws_lock:
            ws = self._current_ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def _emit_state(self) -> None:
        if callable(self._on_state):
            try:
                self._on_state(dict(self._state))
            except Exception:
                pass

    def _update_state(self, **updates: Any) -> None:
        self._state.update(updates)
        self._emit_state()

    def _compute_backoff_seconds(self, attempt: int) -> float:
        base = min(self._backoff_max_seconds, self._backoff_initial_seconds * (2.0 ** max(0, attempt)))
        jitter = self._random_fn() * 0.25 * base
        return max(0.05, base + jitter)

    def _prune_seen_ids(self) -> None:
        now = self._time_fn()
        stale = [key for key, ts in self._seen_event_ids.items() if (now - ts) > self._dedupe_ttl_seconds]
        for key in stale:
            self._seen_event_ids.pop(key, None)

    def _is_duplicate(self, twitch_event_id: str) -> bool:
        key = str(twitch_event_id or "").strip()
        if not key:
            return False
        self._prune_seen_ids()
        now = self._time_fn()
        if key in self._seen_event_ids:
            return True
        self._seen_event_ids[key] = now
        return False

    def _post_subscription(self, *, session_id: str, sub_type: str, version: str, condition: Dict[str, Any]) -> None:
        token = self._oauth_token
        if token.startswith("oauth:"):
            token = token.split(":", 1)[1]
        body = json.dumps(
            {
                "type": sub_type,
                "version": version,
                "condition": condition,
                "transport": {"method": "websocket", "session_id": session_id},
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            "https://api.twitch.tv/helix/eventsub/subscriptions",
            data=body,
            method="POST",
            headers={
                "Client-ID": self._client_id,
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=10.0) as response:
            _ = response.read()

    def _ensure_subscriptions(self, session_id: str) -> None:
        specs = [
            ("channel.follow", "2", {"broadcaster_user_id": self._broadcaster_user_id, "moderator_user_id": self._broadcaster_user_id}),
            ("channel.subscribe", "1", {"broadcaster_user_id": self._broadcaster_user_id}),
            ("channel.cheer", "1", {"broadcaster_user_id": self._broadcaster_user_id}),
            ("channel.raid", "1", {"to_broadcaster_user_id": self._broadcaster_user_id}),
        ]
        for sub_type, version, condition in specs:
            try:
                self._post_subscription(
                    session_id=session_id,
                    sub_type=sub_type,
                    version=version,
                    condition=condition,
                )
            except urllib.error.HTTPError as exc:
                self._log(f"[EventSubWS] subscription {sub_type} failed: HTTP {exc.code}")
            except Exception as exc:
                self._log(f"[EventSubWS] subscription {sub_type} failed: {exc}")

    def handle_raw_message(self, raw: str) -> Optional[str]:
        message = json.loads(str(raw or ""))
        metadata = message.get("metadata", {})
        payload = message.get("payload", {})
        if not isinstance(metadata, dict):
            metadata = {}
        if not isinstance(payload, dict):
            payload = {}
        message_type = str(metadata.get("message_type", "")).strip().lower()
        self._update_state(last_eventsub_message_ts=_utc_now_iso(), eventsub_last_error=None)

        if message_type == "session_welcome":
            session = payload.get("session", {})
            session_id = str(session.get("id", "")).strip() if isinstance(session, dict) else ""
            if session_id:
                self._update_state(eventsub_connected=True, eventsub_session_id=session_id)
                self._ensure_subscriptions(session_id)
            return None

        if message_type == "session_keepalive":
            self._update_state(eventsub_connected=True)
            return None

        if message_type == "session_reconnect":
            session = payload.get("session", {})
            reconnect_url = str(session.get("reconnect_url", "")).strip() if isinstance(session, dict) else ""
            self._update_state(eventsub_connected=False)
            return reconnect_url or None

        if message_type == "notification":
            normalized = normalize_eventsub_notification(message)
            if not normalized:
                return None
            twitch_event_id = str(normalized.get("twitch_event_id", "")).strip()
            if twitch_event_id and self._is_duplicate(twitch_event_id):
                return None
            self._on_event(normalized)
            return None

        return None

    def run_forever(self) -> None:
        url = self._ws_url
        attempt = 0
        while not self._stop.is_set():
            ws = None
            try:
                ws = self._ws_factory(url)
                with self._current_ws_lock:
                    self._current_ws = ws
                self._update_state(eventsub_connected=True)
                attempt = 0

                while not self._stop.is_set():
                    raw = ws.recv()
                    if raw is None:
                        raise OSError("EventSub socket closed")
                    reconnect_url = self.handle_raw_message(raw)
                    if reconnect_url:
                        url = reconnect_url
                        raise ConnectionResetError("EventSub requested reconnect")
            except Exception as exc:
                if self._stop.is_set():
                    break
                self._state["reconnect_count"] = int(self._state.get("reconnect_count", 0)) + 1
                self._update_state(eventsub_connected=False, eventsub_last_error=str(exc))
                delay = self._compute_backoff_seconds(attempt)
                attempt += 1
                self._sleep_fn(delay)
            finally:
                with self._current_ws_lock:
                    self._current_ws = None
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass
