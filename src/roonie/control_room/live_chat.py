from __future__ import annotations

import os
import threading
import time
import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from live_shim.record_run import run_payload
from src.twitch.read_path import TwitchMsg, iter_twitch_messages


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
        self._event_counter = 0
        self._last_credential_error: Optional[str] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="roonie-live-chat-bridge", daemon=True)
        self._thread.start()
        self._log(f"[LiveChatBridge] started (account={self._account})")

    def stop(self) -> None:
        self._stop.set()
        self._log("[LiveChatBridge] stop requested")

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is None:
            return
        self._thread.join(timeout=timeout)

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

    def _next_event_id(self) -> str:
        self._event_counter += 1
        return f"live-{int(time.time() * 1000)}-{self._event_counter}"

    def _emit_one(self, msg: TwitchMsg, *, bot_nick: str) -> None:
        status = self._storage.get_status().to_dict()
        can_post = bool(status.get("can_post", False))
        blocked_by = status.get("blocked_by", [])
        event_id = self._next_event_id()
        is_direct = self._is_direct_mention(msg, bot_nick)

        payload = {
            "session_id": "twitch-live-session",
            "inputs": [
                {
                    "event_id": event_id,
                    "message": str(msg.message or ""),
                    "metadata": {
                        "user": str(msg.nick or "viewer"),
                        "platform": "twitch",
                        "channel": str(msg.channel or ""),
                        "is_direct_mention": is_direct,
                        "mode": "live",
                    },
                }
            ],
        }
        run_path = run_payload(payload, emit_outputs=can_post)
        emitted = False
        emit_reason = "NO_OUTPUT_RECORD"
        try:
            run_doc = json.loads(run_path.read_text(encoding="utf-8-sig"))
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

        if can_post and emitted:
            self._log(f"[LiveChatBridge] emitted event_id={event_id} user={msg.nick} reason={emit_reason}")
        elif can_post:
            self._log(f"[LiveChatBridge] processed(no-emit) event_id={event_id} reason={emit_reason}")
        else:
            self._log(f"[LiveChatBridge] processed(no-emit) event_id={event_id} blocked_by={blocked_by}")

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
