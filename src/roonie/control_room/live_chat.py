from __future__ import annotations

import os
import threading
import time
import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from live_shim.record_run import run_payload
from roonie.offline_director import OfflineDirector
from roonie.provider_director import ProviderDirector
from roonie.types import Env
from twitch.read_path import TwitchMsg, iter_twitch_messages


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LiveChatBridge:
    def __init__(
        self,
        *,
        storage: Any,
        account: str = "bot",
        logger: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._storage = storage
        self._account = str(account or "bot").strip().lower() or "bot"
        self._logger = logger
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._retry_thread: Optional[threading.Thread] = None
        self._event_counter = 0
        self._last_credential_error: Optional[str] = None
        self._pending_lock = threading.Lock()
        self._pending_ready = threading.Event()
        self._pending_retries: list[Dict[str, Any]] = []
        self._runtime_lock = threading.Lock()
        self._runtime_director_name: str = ""
        self._runtime_director: Any = None
        self._runtime_env = Env(offline=False)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._retry_thread = threading.Thread(target=self._retry_loop, name="roonie-live-chat-retry", daemon=True)
        self._retry_thread.start()
        self._thread = threading.Thread(target=self._run, name="roonie-live-chat-bridge", daemon=True)
        self._thread.start()
        self._log(f"[LiveChatBridge] started (account={self._account})")

    def stop(self) -> None:
        self._stop.set()
        self._pending_ready.set()
        self._log("[LiveChatBridge] stop requested")

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is None:
            if self._retry_thread is not None:
                self._retry_thread.join(timeout=timeout)
            return
        self._thread.join(timeout=timeout)
        if self._retry_thread is not None:
            self._retry_thread.join(timeout=timeout)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

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

    @staticmethod
    def _is_direct_mention(msg: TwitchMsg, bot_nick: str) -> bool:
        text = str(msg.message or "").strip().lower()
        nick = str(bot_nick or "").strip().lower()
        if not text:
            return False
        if "@roonie" in text:
            return True
        if nick and (f"@{nick}" in text or text.startswith(nick)):
            return True
        return False

    def _sync_output_env(self, creds: Dict[str, Any]) -> None:
        channel = str(creds.get("channel", "")).strip().lstrip("#").lower()
        nick = str(creds.get("nick", "")).strip()
        oauth_token = str(creds.get("oauth_token", "")).strip()
        if channel:
            os.environ["TWITCH_CHANNEL"] = channel
        if nick:
            os.environ["TWITCH_BOT_NICK"] = nick
            if "TWITCH_NICK" not in os.environ or not str(os.getenv("TWITCH_NICK", "")).strip():
                os.environ["TWITCH_NICK"] = nick
        if oauth_token:
            os.environ["TWITCH_OAUTH_TOKEN"] = oauth_token
            if "TWITCH_OAUTH" not in os.environ or not str(os.getenv("TWITCH_OAUTH", "")).strip():
                os.environ["TWITCH_OAUTH"] = oauth_token
        # Live chat should prefer real providers when API keys are configured.
        os.environ.setdefault("ROONIE_ENABLE_LIVE_PROVIDER_NETWORK", "1")
        # Live bridge safety: avoid posting provider stub prompt dumps.
        os.environ.setdefault("ROONIE_SANITIZE_PROVIDER_STUB_OUTPUT", "1")
        # Natural live pacing default. Can be overridden explicitly.
        os.environ.setdefault("ROONIE_OUTPUT_RATE_LIMIT_SECONDS", "6")

    @staticmethod
    def _rate_limit_retry_seconds() -> float:
        raw = str(os.getenv("ROONIE_OUTPUT_RATE_LIMIT_SECONDS", "")).strip()
        if raw:
            try:
                val = float(raw)
                if val >= 0.0:
                    return max(0.0, val)
            except Exception:
                pass
        return 6.0

    @staticmethod
    def _max_retry_attempts() -> int:
        raw = str(os.getenv("ROONIE_LIVE_MAX_RETRY_ATTEMPTS", "")).strip()
        if raw:
            try:
                val = int(raw)
                if val > 0:
                    return val
            except Exception:
                pass
        return 8

    def _next_event_id(self) -> str:
        self._event_counter += 1
        return f"live-{int(time.time() * 1000)}-{self._event_counter}"

    @staticmethod
    def _normalize_director_name(value: str) -> str:
        text = str(value or "").strip().lower()
        if text in {"offlinedirector", "offline"}:
            return "OfflineDirector"
        return "ProviderDirector"

    def _ensure_runtime_director(self, active_director: str) -> tuple[str, Any]:
        selected = self._normalize_director_name(active_director)
        if self._runtime_director is None or self._runtime_director_name != selected:
            self._runtime_director_name = selected
            self._runtime_director = (
                ProviderDirector() if selected == "ProviderDirector" else OfflineDirector()
            )
        return selected, self._runtime_director

    def _emit_payload_message(
        self,
        *,
        actor: str,
        message: str,
        channel: str,
        is_direct_mention: bool,
        metadata_extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        status = self._storage.get_status().to_dict()
        can_post = bool(status.get("can_post", False))
        blocked_by = status.get("blocked_by", [])
        requested_director = str(status.get("active_director", "ProviderDirector"))
        normalized_director = self._normalize_director_name(requested_director)
        routing_enabled = bool(status.get("routing_enabled", True))
        session_raw = status.get("session_id")
        active_session_id = session_raw.strip() if isinstance(session_raw, str) else ""
        run_session_id = active_session_id or f"twitch-live-session-{int(time.time() * 1000)}"
        event_id = self._next_event_id()
        metadata: Dict[str, Any] = {
            "user": str(actor or "viewer"),
            "platform": "twitch",
            "channel": str(channel or ""),
            "is_direct_mention": bool(is_direct_mention),
            "mode": "live",
            "session_id": active_session_id,
            "active_director": normalized_director,
            "routing_enabled": routing_enabled,
        }
        if hasattr(self._storage, "get_studio_profile"):
            try:
                profile = self._storage.get_studio_profile()
                approved = profile.get("approved_emotes", []) if isinstance(profile, dict) else []
                if isinstance(approved, list) and approved:
                    metadata["approved_emotes"] = [str(item).strip() for item in approved if str(item).strip()]
            except Exception:
                pass
        if isinstance(metadata_extra, dict):
            metadata.update(dict(metadata_extra))

        payload = {
            "session_id": run_session_id,
            "active_director": normalized_director,
            "inputs": [
                {
                    "event_id": event_id,
                    "message": str(message or ""),
                    "metadata": metadata,
                }
            ],
        }
        with self._runtime_lock:
            active_director, director = self._ensure_runtime_director(requested_director)
            payload["active_director"] = active_director
            run_path = run_payload(
                payload,
                emit_outputs=True,
                director_instance=director,
                env_instance=self._runtime_env,
            )
        emitted = False
        emit_reason = "NO_OUTPUT_RECORD"
        try:
            run_doc = json.loads(run_path.read_text(encoding="utf-8-sig"))
            if hasattr(self._storage, "ingest_memory_candidates_from_run"):
                try:
                    self._storage.ingest_memory_candidates_from_run(run_doc)
                except Exception as exc:
                    self._log(f"[LiveChatBridge] memory candidate ingest skipped: {exc}")
            outputs = run_doc.get("outputs", [])
            if isinstance(outputs, list):
                for item in outputs:
                    if not isinstance(item, dict):
                        continue
                    if str(item.get("event_id", "")).strip() == event_id:
                        emitted = bool(item.get("emitted", False))
                        emit_reason = str(item.get("reason", "")).strip() or "UNKNOWN"
                        break
        except Exception:
            pass

        return {
            "event_id": event_id,
            "session_id": (active_session_id or None),
            "emitted": emitted,
            "reason": emit_reason,
            "blocked_by": blocked_by,
            "can_post": can_post,
            "run_path": str(run_path),
        }

    def _emit_one(self, msg: TwitchMsg, *, bot_nick: str) -> None:
        viewer = str(msg.nick or "viewer")
        text = str(msg.message or "")
        is_direct = self._is_direct_mention(msg, bot_nick)
        self._log(f"[CHAT] {viewer}: {text}")
        try:
            result = self._emit_payload_message(
                actor=viewer,
                message=text,
                channel=str(msg.channel or ""),
                is_direct_mention=is_direct,
                metadata_extra=None,
            )
        except Exception as exc:
            self._log(f"[LiveChatBridge] process error user={viewer}: {exc}")
            return
        if bool(result.get("emitted", False)):
            self._log(
                f"[LiveChatBridge] emitted event_id={result.get('event_id')} user={msg.nick} reason={result.get('reason')}"
            )
        elif bool(result.get("can_post", False)):
            self._log(
                f"[LiveChatBridge] processed(no-emit) event_id={result.get('event_id')} reason={result.get('reason')}"
            )
            reason = str(result.get("reason", "")).strip().upper()
            if reason == "RATE_LIMIT":
                self._queue_retry(
                    actor=viewer,
                    message=text,
                    channel=str(msg.channel or ""),
                    is_direct_mention=is_direct,
                    metadata_extra=None,
                    attempt=1,
                    delay_seconds=self._rate_limit_retry_seconds(),
                )
        else:
            self._log(
                f"[LiveChatBridge] processed(no-emit) event_id={result.get('event_id')} blocked_by={result.get('blocked_by')}"
            )

    def _queue_retry(
        self,
        *,
        actor: str,
        message: str,
        channel: str,
        is_direct_mention: bool,
        metadata_extra: Optional[Dict[str, Any]],
        attempt: int,
        delay_seconds: float,
    ) -> None:
        due_ts = time.time() + max(0.0, float(delay_seconds))
        item = {
            "due_ts": due_ts,
            "actor": str(actor or "viewer"),
            "message": str(message or ""),
            "channel": str(channel or ""),
            "is_direct_mention": bool(is_direct_mention),
            "metadata_extra": dict(metadata_extra) if isinstance(metadata_extra, dict) else None,
            "attempt": int(attempt),
        }
        with self._pending_lock:
            self._pending_retries.append(item)
            self._pending_retries.sort(key=lambda row: float(row.get("due_ts", 0.0)))
        self._pending_ready.set()
        self._log(
            f"[LiveChatBridge] queued retry attempt={int(attempt)} in {round(max(0.0, float(delay_seconds)), 2)}s"
        )

    def _process_retry_item(self, item: Dict[str, Any]) -> None:
        actor = str(item.get("actor", "viewer"))
        message = str(item.get("message", ""))
        channel = str(item.get("channel", ""))
        is_direct = bool(item.get("is_direct_mention", False))
        metadata_extra = item.get("metadata_extra")
        if not isinstance(metadata_extra, dict):
            metadata_extra = None
        attempt = int(item.get("attempt", 1))
        max_attempts = self._max_retry_attempts()

        try:
            result = self._emit_payload_message(
                actor=actor,
                message=message,
                channel=channel,
                is_direct_mention=is_direct,
                metadata_extra=metadata_extra,
            )
        except Exception as exc:
            self._log(f"[LiveChatBridge] retry process error attempt={attempt}: {exc}")
            if attempt < max_attempts:
                self._queue_retry(
                    actor=actor,
                    message=message,
                    channel=channel,
                    is_direct_mention=is_direct,
                    metadata_extra=metadata_extra,
                    attempt=attempt + 1,
                    delay_seconds=self._rate_limit_retry_seconds(),
                )
            return

        if bool(result.get("emitted", False)):
            self._log(
                f"[LiveChatBridge] emitted(retry) event_id={result.get('event_id')} user={actor} reason={result.get('reason')}"
            )
            return

        reason = str(result.get("reason", "")).strip().upper()
        if reason == "RATE_LIMIT" and attempt < max_attempts:
            self._queue_retry(
                actor=actor,
                message=message,
                channel=channel,
                is_direct_mention=is_direct,
                metadata_extra=metadata_extra,
                attempt=attempt + 1,
                delay_seconds=self._rate_limit_retry_seconds(),
            )
            return

        self._log(
            f"[LiveChatBridge] dropped retry attempt={attempt} reason={reason or 'UNKNOWN'}"
        )

    def _retry_loop(self) -> None:
        while not self._stop.is_set():
            item: Optional[Dict[str, Any]] = None
            wait_s = 1.0
            with self._pending_lock:
                if self._pending_retries:
                    self._pending_retries.sort(key=lambda row: float(row.get("due_ts", 0.0)))
                    first = self._pending_retries[0]
                    due_ts = float(first.get("due_ts", 0.0))
                    now = time.time()
                    if due_ts <= now:
                        item = self._pending_retries.pop(0)
                    else:
                        wait_s = max(0.0, due_ts - now)
            if item is None:
                self._pending_ready.wait(wait_s)
                self._pending_ready.clear()
                continue
            self._process_retry_item(item)

    def ingest_eventsub_event(self, normalized_event: Dict[str, Any], *, text: str) -> Dict[str, Any]:
        metadata_extra = {
            "source": "eventsub",
            "event_type": str(normalized_event.get("event_type", "")).strip().upper(),
            "raw_type": str(normalized_event.get("raw_type", "")).strip(),
            "twitch_event_id": str(normalized_event.get("twitch_event_id", "")).strip(),
            "event_ts": str(normalized_event.get("timestamp", "")).strip(),
        }
        actor = str(
            normalized_event.get("user_login")
            or normalized_event.get("display_name")
            or "eventsub"
        ).strip()
        channel = str(normalized_event.get("channel", "")).strip()
        return self._emit_payload_message(
            actor=actor or "eventsub",
            message=str(text or ""),
            channel=channel,
            is_direct_mention=True,
            metadata_extra=metadata_extra,
        )

    def _run(self) -> None:
        backoff_s = 2.0
        while not self._stop.is_set():
            creds = self._storage.get_live_twitch_credentials(account=self._account)
            if not bool(creds.get("ok", False)):
                reason = str(creds.get("error") or "DISCONNECTED").strip() or "DISCONNECTED"
                detail = str(creds.get("detail") or "").strip()
                stamp = f"{reason}:{detail}"
                if stamp != self._last_credential_error:
                    self._last_credential_error = stamp
                    self._log(f"[LiveChatBridge] waiting for Twitch credentials ({reason}) {detail}")
                self._stop.wait(2.0)
                continue

            self._last_credential_error = None
            self._sync_output_env(creds)
            oauth_token = str(creds.get("oauth_token", "")).strip()
            nick = str(creds.get("nick", "")).strip()
            channel = str(creds.get("channel", "")).strip()
            self._log(f"[LiveChatBridge] connecting to #{channel} as {nick}")
            try:
                for incoming in iter_twitch_messages(
                    oauth_token=oauth_token,
                    nick=nick,
                    channel=channel,
                    debug=bool(str(os.getenv("ROONIE_LIVE_DEBUG_IRC", "0")).strip() in {"1", "true", "yes", "on"}),
                ):
                    if self._stop.is_set():
                        break
                    if str(incoming.nick or "").strip().lower() == nick.lower():
                        continue
                    self._emit_one(incoming, bot_nick=nick)
                backoff_s = 2.0
            except Exception as exc:
                self._log(f"[LiveChatBridge] read loop error: {exc}")
                self._stop.wait(backoff_s)
                backoff_s = min(backoff_s * 2.0, 15.0)

        self._log("[LiveChatBridge] stopped")
