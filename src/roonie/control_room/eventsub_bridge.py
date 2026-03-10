from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Callable, ClassVar, Dict, Optional

from roonie.control_room.social_announcer import SocialAnnouncer
from twitch.eventsub_ws import EventSubWSClient


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventSubBridge:
    _IGNORED_SUB_USERNAMES = {"cland3stine", "c0rcyra", "ruleofrune"}

    def __init__(
        self,
        *,
        storage: Any,
        live_bridge: Any,
        logger: Optional[Callable[[str], None]] = None,
        social_announcer: Optional[Any] = None,
    ) -> None:
        self._storage = storage
        self._live_bridge = live_bridge
        self._logger = logger
        self._social_announcer = (
            social_announcer
            if social_announcer is not None
            else SocialAnnouncer(storage=storage, logger=logger)
        )
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
            tier_raw = str(normalized.get("tier") or "").strip()
            tier_label = {"1000": "Tier 1", "2000": "Tier 2", "3000": "Tier 3"}.get(tier_raw, tier_raw)
            tier_suffix = f" ({tier_label})" if tier_label else ""
            months = int(normalized.get("months") or 0)
            if normalized.get("is_gift"):
                return f"@RoonieTheCat heads up: {display} received a gifted sub{tier_suffix}! Welcome them."
            if normalized.get("is_resub") and months > 0:
                month_label = "month" if months == 1 else "months"
                return f"@RoonieTheCat heads up: {display} just resubscribed{tier_suffix} ({months} {month_label})! Say thanks."
            return f"@RoonieTheCat heads up: {display} just subscribed{tier_suffix}! Say thanks."
        if event_type == "GIFTED_SUB":
            tier_raw = str(normalized.get("tier") or "").strip()
            tier_label = {"1000": "Tier 1", "2000": "Tier 2", "3000": "Tier 3"}.get(tier_raw, tier_raw)
            tier_suffix = f" ({tier_label})" if tier_label else ""
            gift_count = int(normalized.get("gift_count") or 0)
            gift_label = "sub" if gift_count == 1 else "subs"
            gifter = "an anonymous gifter" if normalized.get("is_anonymous") else display
            count_text = str(gift_count or 1)
            return f"@RoonieTheCat heads up: {gifter} gifted {count_text} {gift_label}{tier_suffix}."
        if event_type == "CHEER":
            amount = normalized.get("amount")
            return f"@RoonieTheCat heads up: {display} cheered {amount or 0} bits."
        if event_type == "RAID":
            count = normalized.get("raid_viewer_count")
            return f"@RoonieTheCat heads up: raid from {display} ({count or 0} viewers)."
        if event_type == "STREAM_ONLINE":
            return f"@RoonieTheCat heads up: stream just went live on {display}."
        if event_type == "STREAM_OFFLINE":
            return f"@RoonieTheCat heads up: stream went offline on {display}."
        return f"@RoonieTheCat heads up: {display} triggered {event_type}."

    @staticmethod
    def _normalize_username(value: Any) -> str:
        return str(value or "").strip().lstrip("@").lower()

    # FOLLOW stays utility-only; sub-related events remain suppressed until payload handling is ready.
    _SUPPRESSED_EVENT_TYPES: ClassVar[frozenset[str]] = frozenset({"FOLLOW", "SUB", "GIFTED_SUB"})

    def _is_ignored_self_sub(self, normalized: Dict[str, Any]) -> bool:
        event_type = str(normalized.get("event_type", "UNKNOWN")).strip().upper()
        if event_type != "SUB":
            return False
        user_login = self._normalize_username(normalized.get("user_login"))
        display_name = self._normalize_username(normalized.get("display_name"))
        actor = user_login or display_name
        return bool(actor and actor in self._IGNORED_SUB_USERNAMES)

    def _suppression_reason(self, normalized: Dict[str, Any]) -> Optional[str]:
        if self._is_ignored_self_sub(normalized):
            return "IGNORED_SELF_SUB"
        event_type = str(normalized.get("event_type", "UNKNOWN")).strip().upper()
        if event_type in self._SUPPRESSED_EVENT_TYPES:
            return f"SUPPRESSED_EVENT_TYPE:{event_type}"
        return None

    def _on_event(self, normalized: Dict[str, Any]) -> None:
        event_type = str(normalized.get("event_type", "UNKNOWN")).strip().upper()
        event_id = str(normalized.get("twitch_event_id", "")).strip()
        suppression_reason = self._suppression_reason(normalized)
        result: Dict[str, Any]
        if event_type == "STREAM_ONLINE":
            try:
                social_result = self._social_announcer.announce_stream_online(normalized)
                result = {
                    "emitted": bool(social_result.get("sent", False)),
                    "reason": str(social_result.get("reason", "SOCIAL_NO_SEND")).strip() or "SOCIAL_NO_SEND",
                    "session_id": None,
                }
            except Exception as exc:
                result = {"emitted": False, "reason": f"SOCIAL_ANNOUNCE_ERROR:{exc}", "session_id": None}
                self._log(f"[EventSubBridge] social announce failed event_id={event_id} error={exc}")
        elif event_type == "STREAM_OFFLINE":
            result = {"emitted": False, "reason": "STREAM_OFFLINE_NOOP", "session_id": None}
        elif suppression_reason is not None:
            result = {"emitted": False, "reason": suppression_reason, "session_id": None}
        else:
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

