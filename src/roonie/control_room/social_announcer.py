from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from providers.registry import ProviderRegistry
from providers.router import get_provider_runtime_status, route_generate


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truthy_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _provider_registry_from_runtime() -> ProviderRegistry:
    runtime = get_provider_runtime_status()
    approved = [
        str(item).strip().lower()
        for item in runtime.get("approved_providers", [])
        if str(item).strip()
    ]
    if not approved:
        approved = ["openai"]
    if "openai" not in approved:
        approved.insert(0, "openai")
    active = str(runtime.get("active_provider", "openai")).strip().lower() or "openai"
    if active not in approved:
        active = "openai"
    providers_cfg = {
        name: {"enabled": (name in approved)}
        for name in ("openai", "grok", "anthropic")
    }
    return ProviderRegistry.from_dict(
        {
            "default_provider": active,
            "providers": providers_cfg,
        }
    )


def _collapse_whitespace(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def _looks_like_stub_output(value: str) -> bool:
    text = str(value or "").strip().lower()
    return bool(text.startswith("[") and "stub]" in text[:32])


class SocialAnnouncer:
    def __init__(
        self,
        *,
        storage: Any,
        logger: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._storage = storage
        self._logger = logger
        self._lock = threading.Lock()
        self._seen_stream_event_ids: set[str] = set()

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

    def _stream_channel_login(self, normalized_event: Dict[str, Any]) -> str:
        channel = str(normalized_event.get("channel") or "").strip().lstrip("#").lower()
        if channel:
            return channel
        fallback = str(normalized_event.get("user_login") or "").strip().lstrip("#").lower()
        if fallback:
            return fallback
        if hasattr(self._storage, "get_twitch_status"):
            try:
                status = self._storage.get_twitch_status()
                status_channel = str(status.get("primary_channel") or "").strip().lstrip("#").lower()
                if status_channel:
                    return status_channel
            except Exception:
                pass
        return "ruleofrune"

    def _default_live_message(self, *, channel_login: str, stream_url: str) -> str:
        if channel_login:
            return (
                f"Hey everyone, Roonie here. We're live now at Rule of Rune. "
                f"Come hang with us: {stream_url}"
            )
        return f"Hey everyone, Roonie here. We're live now. Come hang with us: {stream_url}"

    @staticmethod
    def _sanitize_message(message: str, *, stream_url: str) -> str:
        text = _collapse_whitespace(message).strip().strip("\"'`")
        if not text:
            text = ""
        if stream_url and stream_url not in text:
            text = f"{text} {stream_url}".strip()
        return text[:280].strip()

    def _build_live_prompt(
        self,
        *,
        channel_login: str,
        stream_url: str,
        event_id: str,
        prompt_style: str,
        previous_message: str,
        is_test: bool,
    ) -> str:
        test_clause = "This is a test send from the dashboard." if is_test else "This is a real go-live announcement."
        style_clause = prompt_style or "Friendly, welcoming, and energetic without sounding spammy."
        prev_clause = previous_message or "None"
        return (
            "You are Roonie, writing one Discord announcement.\n"
            f"{test_clause}\n"
            "Goal: tell viewers Rule of Rune is live on Twitch and invite them in.\n"
            "Requirements:\n"
            "- Return exactly one short message (max 220 characters before URL).\n"
            "- Keep it natural and human. No hashtags.\n"
            "- Mention 'Rule of Rune' by name.\n"
            f"- Include this exact URL somewhere in the message: {stream_url}\n"
            "- Do not wrap the message in quotes.\n"
            f"- Avoid repeating this previous message style: {prev_clause}\n"
            f"- Style guidance: {style_clause}\n"
            f"- Event id for variation: {event_id or 'none'}\n"
            f"- Twitch login: {channel_login or 'ruleofrune'}\n"
            "Output only the final message."
        )

    def _generate_live_message(
        self,
        *,
        channel_login: str,
        stream_url: str,
        event_id: str,
        config: Dict[str, Any],
        is_test: bool,
    ) -> Dict[str, Any]:
        fallback = self._default_live_message(channel_login=channel_login, stream_url=stream_url)
        llm_enabled = bool(config.get("llm_enabled", True))
        if not llm_enabled:
            return {"message": fallback, "provider": None, "used_llm": False}

        runtime_state = {}
        if hasattr(self._storage, "get_socials_runtime_state"):
            try:
                runtime_state = self._storage.get_socials_runtime_state()
            except Exception:
                runtime_state = {}
        previous_message = str(runtime_state.get("last_message") or "").strip()
        prompt_style = str(config.get("prompt_style") or "").strip()

        prompt = self._build_live_prompt(
            channel_login=channel_login,
            stream_url=stream_url,
            event_id=event_id,
            prompt_style=prompt_style,
            previous_message=previous_message,
            is_test=is_test,
        )
        context: Dict[str, Any] = {
            "use_provider_config": True,
            "allow_live_provider_network": _truthy_env("ROONIE_ENABLE_LIVE_PROVIDER_NETWORK", True),
            "message_text": "social live announcement for discord",
            "category": "social_announcement",
            "utility_source": "discord_live_announce",
            "event_id": event_id,
        }
        provider_name: Optional[str] = None
        try:
            registry = _provider_registry_from_runtime()
            out = route_generate(
                registry=registry,
                routing_cfg={},
                prompt=prompt,
                context=context,
            )
            provider_name = str(
                context.get("provider_selected")
                or context.get("active_provider")
                or registry.get_default().name
            ).strip().lower() or None
            candidate = str(out or "").strip()
            if not candidate or _looks_like_stub_output(candidate):
                return {"message": fallback, "provider": provider_name, "used_llm": False}
            final = self._sanitize_message(candidate, stream_url=stream_url)
            if not final:
                final = fallback
            return {"message": final, "provider": provider_name, "used_llm": True}
        except Exception as exc:
            self._log(f"[SocialAnnouncer] LLM generation failed: {exc}")
            return {"message": fallback, "provider": provider_name, "used_llm": False}

    def _send_discord(
        self,
        *,
        discord_cfg: Dict[str, Any],
        content: str,
    ) -> Dict[str, Any]:
        webhook_url = str(discord_cfg.get("webhook_url") or "").strip()
        if not webhook_url:
            return {"ok": False, "sent": False, "reason": "DISCORD_WEBHOOK_MISSING"}
        username_override = str(discord_cfg.get("username_override") or "").strip()
        avatar_url = str(discord_cfg.get("avatar_url") or "").strip()
        mention_everyone = bool(discord_cfg.get("mention_everyone", False))
        message = _collapse_whitespace(content)
        if mention_everyone and not message.lower().startswith("@everyone"):
            message = f"@everyone {message}".strip()
        payload: Dict[str, Any] = {"content": message}
        if username_override:
            payload["username"] = username_override
        if avatar_url:
            payload["avatar_url"] = avatar_url
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10.0) as response:
                status = int(getattr(response, "status", 0) or 0)
                if status in {200, 204}:
                    return {"ok": True, "sent": True, "status": status, "reason": "SENT"}
                return {"ok": False, "sent": False, "status": status, "reason": f"HTTP_{status}"}
        except urllib.error.HTTPError as exc:
            return {"ok": False, "sent": False, "status": int(exc.code), "reason": f"HTTP_{int(exc.code)}"}
        except Exception as exc:
            return {"ok": False, "sent": False, "reason": f"DISCORD_SEND_ERROR:{exc}"}

    def _is_duplicate_stream_event(self, event_id: str) -> bool:
        key = str(event_id or "").strip()
        if not key:
            return False
        with self._lock:
            if key in self._seen_stream_event_ids:
                return True
            self._seen_stream_event_ids.add(key)
            if len(self._seen_stream_event_ids) > 256:
                # Keep this bounded in long runtimes.
                self._seen_stream_event_ids = set(list(self._seen_stream_event_ids)[-128:])
            return False

    def _record_runtime(
        self,
        *,
        network: str,
        event_type: str,
        message: str,
        provider: Optional[str],
        sent: bool,
        error: Optional[str],
    ) -> None:
        if hasattr(self._storage, "record_social_delivery"):
            try:
                self._storage.record_social_delivery(
                    network=network,
                    event_type=event_type,
                    message=message,
                    provider=provider,
                    sent=bool(sent),
                    error=error,
                )
            except Exception:
                pass

    def announce_stream_online(self, normalized_event: Dict[str, Any], *, is_test: bool = False) -> Dict[str, Any]:
        event_type = str(normalized_event.get("event_type", "")).strip().upper() or "UNKNOWN"
        event_id = str(normalized_event.get("twitch_event_id", "")).strip()
        channel_login = self._stream_channel_login(normalized_event)
        stream_url = f"https://twitch.tv/{channel_login}"

        if event_type != "STREAM_ONLINE":
            return {
                "ok": False,
                "sent": False,
                "reason": "UNSUPPORTED_EVENT_TYPE",
                "event_type": event_type,
            }
        if not is_test and event_id and self._is_duplicate_stream_event(event_id):
            return {
                "ok": False,
                "sent": False,
                "reason": "DUPLICATE_STREAM_ONLINE",
                "event_type": event_type,
                "event_id": event_id,
            }
        if hasattr(self._storage, "is_read_only_mode") and bool(self._storage.is_read_only_mode()):
            return {
                "ok": False,
                "sent": False,
                "reason": "READ_ONLY_MODE",
                "event_type": event_type,
                "event_id": event_id,
            }

        try:
            config = self._storage.get_socials_config() if hasattr(self._storage, "get_socials_config") else {}
        except Exception:
            config = {}

        socials_enabled = bool(config.get("enabled", False))
        discord_cfg = config.get("discord", {}) if isinstance(config.get("discord"), dict) else {}
        discord_enabled = bool(discord_cfg.get("enabled", False))
        webhook_url = str(discord_cfg.get("webhook_url") or "").strip()

        if not socials_enabled:
            return {
                "ok": False,
                "sent": False,
                "reason": "SOCIALS_DISABLED",
                "event_type": event_type,
                "event_id": event_id,
            }
        if not discord_enabled:
            return {
                "ok": False,
                "sent": False,
                "reason": "DISCORD_DISABLED",
                "event_type": event_type,
                "event_id": event_id,
            }
        if not webhook_url:
            return {
                "ok": False,
                "sent": False,
                "reason": "DISCORD_WEBHOOK_MISSING",
                "event_type": event_type,
                "event_id": event_id,
            }

        generated = self._generate_live_message(
            channel_login=channel_login,
            stream_url=stream_url,
            event_id=(event_id or f"stream-online-{int(time.time() * 1000)}"),
            config=config,
            is_test=is_test,
        )
        message = str(generated.get("message") or "").strip()
        provider = str(generated.get("provider") or "").strip().lower() or None
        send = self._send_discord(discord_cfg=discord_cfg, content=message)

        sent = bool(send.get("sent", False))
        reason = str(send.get("reason") or ("SENT" if sent else "SEND_FAILED")).strip()
        error = None if sent else reason
        self._record_runtime(
            network="discord",
            event_type=event_type,
            message=message,
            provider=provider,
            sent=sent,
            error=error,
        )
        return {
            "ok": sent,
            "sent": sent,
            "network": "discord",
            "event_type": event_type,
            "event_id": event_id or None,
            "channel": channel_login,
            "stream_url": stream_url,
            "provider": provider,
            "used_llm": bool(generated.get("used_llm", False)),
            "message": message,
            "reason": reason,
            "is_test": bool(is_test),
            "attempted_at": _utc_now_iso(),
        }

    def send_test(self, *, network: str = "discord") -> Dict[str, Any]:
        selected = str(network or "discord").strip().lower() or "discord"
        if selected != "discord":
            return {
                "ok": False,
                "sent": False,
                "reason": "NETWORK_NOT_IMPLEMENTED",
                "network": selected,
            }
        channel_login = "ruleofrune"
        if hasattr(self._storage, "get_twitch_status"):
            try:
                status = self._storage.get_twitch_status()
                channel_login = str(status.get("primary_channel") or channel_login).strip().lstrip("#").lower() or channel_login
            except Exception:
                pass
        test_event = {
            "event_type": "STREAM_ONLINE",
            "raw_type": "stream.online",
            "twitch_event_id": f"test-social-{int(time.time() * 1000)}",
            "channel": channel_login,
            "timestamp": _utc_now_iso(),
        }
        return self.announce_stream_online(test_event, is_test=True)
