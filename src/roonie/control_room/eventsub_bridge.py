from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from twitch.eventsub_ws import EventSubWSClient


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventSubBridge:
    def __init__(
        self,
        *,
        storage: Any,
        live_bridge: Any,
        logger: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._storage = storage
        self._live_bridge = live_bridge
        self._logger = logger
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._client: Optional[EventSubWSClient] = None
        self._last_cred_error: Optional[str] = None

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

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="roonie-eventsub-bridge", daemon=True)
        self._thread.start()
        self._log("[EventSubBridge] started")

    def stop(self) -> None:
        self._stop.set()
        if self._client is not None:
            self._client.stop()
        self._log("[EventSubBridge] stop requested")

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is None:
            return
        self._thread.join(timeout=timeout)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _on_state(self, state: Dict[str, Any]) -> None:
        if hasattr(self._storage, "set_eventsub_runtime_state"):
            self._storage.set_eventsub_runtime_state(
                connected=bool(state.get("eventsub_connected", False)),
                session_id=(str(state.get("eventsub_session_id", "")).strip() or None),
                last_message_ts=(str(state.get("last_eventsub_message_ts", "")).strip() or None),
                reconnect_count=int(state.get("reconnect_count", 0) or 0),
                last_error=(str(state.get("eventsub_last_error", "")).strip() or None),
            )

    @staticmethod
    def _eventsub_text(normalized: Dict[str, Any]) -> str:
        event_type = str(normalized.get("event_type", "UNKNOWN")).strip().upper()
        display = str(normalized.get("display_name") or normalized.get("user_login") or "someone").strip()
        if event_type == "FOLLOW":
            return f"@RoonieTheCat heads up: {display} just followed."
        if event_type == "SUB":
            tier = str(normalized.get("tier") or "").strip()
            return f"@RoonieTheCat heads up: {display} subscribed{(' tier ' + tier) if tier else ''}."
        if event_type == "CHEER":
            amount = normalized.get("amount")
            return f"@RoonieTheCat heads up: {display} cheered {amount or 0} bits."
        if event_type == "RAID":
            count = normalized.get("raid_viewer_count")
            return f"@RoonieTheCat heads up: raid from {display} ({count or 0} viewers)."
        return f"@RoonieTheCat heads up: {display} triggered {event_type}."

    def _on_event(self, normalized: Dict[str, Any]) -> None:
        event_type = str(normalized.get("event_type", "UNKNOWN")).strip().upper()
        event_id = str(normalized.get("twitch_event_id", "")).strip()
        result: Dict[str, Any] = {}
        try:
            result = self._live_bridge.ingest_eventsub_event(normalized, text=self._eventsub_text(normalized))
        except Exception as exc:
            result = {"emitted": False, "reason": f"INGEST_ERROR:{exc}", "session_id": None}
            self._log(f"[EventSubBridge] ingest failed event_id={event_id} error={exc}")

        if hasattr(self._storage, "record_eventsub_notification"):
            result_session_raw = result.get("session_id")
            result_session_id = result_session_raw.strip() if isinstance(result_session_raw, str) else None
            self._storage.record_eventsub_notification(
                twitch_event_id=event_id,
                event_type=event_type,
                session_id=result_session_id,
                emitted=bool(result.get("emitted", False)),
                suppression_reason=(str(result.get("reason", "")).strip() or None),
            )

        self._log(
            f"[EventSubBridge] processed event_id={event_id or 'unknown'} type={event_type} "
            f"emitted={bool(result.get('emitted', False))} reason={str(result.get('reason', 'UNKNOWN'))}"
        )

    def _run(self) -> None:
        while not self._stop.is_set():
            creds = self._storage.get_eventsub_runtime_credentials()
            if not bool(creds.get("ok", False)):
                reason = str(creds.get("error") or "DISCONNECTED").strip() or "DISCONNECTED"
                detail = str(creds.get("detail") or "").strip()
                stamp = f"{reason}:{detail}"
                if stamp != self._last_cred_error:
                    self._last_cred_error = stamp
                    self._log(f"[EventSubBridge] waiting for EventSub credentials ({reason}) {detail}")
                self._on_state(
                    {
                        "eventsub_connected": False,
                        "eventsub_session_id": None,
                        "last_eventsub_message_ts": None,
                        "eventsub_last_error": reason,
                    }
                )
                self._stop.wait(2.0)
                continue

            self._last_cred_error = None
            self._client = EventSubWSClient(
                oauth_token=str(creds.get("oauth_token", "")).strip(),
                client_id=str(creds.get("client_id", "")).strip(),
                broadcaster_user_id=str(creds.get("broadcaster_user_id", "")).strip(),
                on_event=self._on_event,
                on_state=self._on_state,
                logger=self._logger,
            )
            self._log("[EventSubBridge] connecting EventSub websocket")
            self._client.run_forever()
            self._client = None
            if self._stop.is_set():
                break
            self._stop.wait(1.0)

        self._on_state(
            {
                "eventsub_connected": False,
                "eventsub_session_id": None,
            }
        )
        self._log("[EventSubBridge] stopped")
