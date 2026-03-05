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


def _normalize_smart_quotes(value: str) -> str:
    return (
        str(value or "")
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2014", "--")
        .replace("\u2013", "-")
    )


def _looks_like_stub_output(value: str) -> bool:
    text = str(value or "").strip().lower()
    return bool(text.startswith("[") and "stub]" in text[:32])


_MAX_RECENT_MESSAGES = 3
_LLM_RETRY_ATTEMPTS = 2


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
        self._recent_discord_messages: list[str] = []
        self._recent_x_messages: list[str] = []

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

    def _default_live_message(
        self, *, channel_login: str, stream_url: str, tiktok_url: str = ""
    ) -> str:
        tiktok = tiktok_url or "https://tiktok.com/@ruleofrune"
        return (
            f"Rule of Rune is live! Come hang with us.\n\n"
            f"{stream_url}\n"
            f"{tiktok}"
        )

    @staticmethod
    def _sanitize_message(
        message: str,
        *,
        required_urls: Optional[list[str]] = None,
        max_len: int = 400,
    ) -> str:
        text = _normalize_smart_quotes(message).strip().strip("\"'`")
        if not text:
            return ""
        for url in (required_urls or []):
            if url and url not in text:
                text = f"{text}\n{url}"
        return text[:max_len].strip()

    def _recent_messages_clause(self, network: str) -> str:
        with self._lock:
            recent = list(
                self._recent_discord_messages if network == "discord"
                else self._recent_x_messages
            )
        if not recent:
            return ""
        lines = "\n".join(f'  - "{m[:120]}"' for m in recent[-_MAX_RECENT_MESSAGES:])
        return f"- Do NOT sound like any of these recent messages:\n{lines}\n"

    def _record_recent_message(self, network: str, message: str) -> None:
        first_line = str(message or "").strip().split("\n")[0].strip()
        if not first_line:
            return
        with self._lock:
            buf = (
                self._recent_discord_messages if network == "discord"
                else self._recent_x_messages
            )
            buf.append(first_line)
            while len(buf) > _MAX_RECENT_MESSAGES:
                buf.pop(0)

    def _build_discord_prompt(
        self,
        *,
        stream_url: str,
        tiktok_url: str,
        event_id: str,
        prompt_style: str,
        is_test: bool,
    ) -> str:
        test_clause = (
            "This is a test send from the dashboard."
            if is_test
            else "This is a real go-live announcement."
        )
        style_clause = prompt_style or ""
        style_line = f"- Additional style guidance: {style_clause}\n" if style_clause else ""
        recent_clause = self._recent_messages_clause("discord")
        return (
            "You are Roonie, a blue plushie cat who sits on the DJ booth at "
            "Rule of Rune, an underground progressive house stream on Twitch.\n"
            f"{test_clause}\n"
            "\n"
            "Write one short Discord announcement telling the server that "
            "Rule of Rune is LIVE.\n"
            "\n"
            "Voice rules:\n"
            "- You're a plushie cat. You type with your paws. You sit on the "
            "booth. This is who you are, not a bit you perform.\n"
            "- Short and warm. Like a friend pinging the group chat. "
            "1-2 sentences max before the links.\n"
            "- Do NOT use the word 'vibes' or 'vibing.' Ever.\n"
            "- Do NOT start with 'Hey everyone.' Find a more natural opener.\n"
            "- Do NOT repeat the phrase 'perched on the booth' or 'come sit "
            "with me.'\n"
            "- No emojis, no hashtags, no 'click here' language.\n"
            "- Do not wrap your output in quotes.\n"
            "- Vary your sentence structure. Sometimes lead with what you're "
            "hearing. Sometimes lead with the invite. Sometimes lead with "
            "something physical (falling over, paws buzzing, ears perking up).\n"
            f"{style_line}"
            f"{recent_clause}"
            "\n"
            "Format rules:\n"
            "- Message text first (max 180 chars).\n"
            "- Then a blank line.\n"
            "- Then both URLs on their own lines exactly as shown:\n"
            f"{stream_url}\n"
            f"{tiktok_url}\n"
            "\n"
            f"Event id for variation seed: {event_id or 'none'}\n"
            "\n"
            "Output ONLY the final message. Nothing else."
        )

    def _build_x_prompt(
        self,
        *,
        stream_url: str,
        tiktok_url: str,
        event_id: str,
        prompt_style: str,
        is_test: bool,
    ) -> str:
        test_clause = (
            "This is a test post from the dashboard."
            if is_test
            else "This is a real go-live announcement."
        )
        style_clause = prompt_style or ""
        style_line = f"- Additional style guidance: {style_clause}\n" if style_clause else ""
        recent_clause = self._recent_messages_clause("x")
        return (
            "You are posting from the Rule of Rune account on X (Twitter). "
            "Rule of Rune is a progressive house and underground electronic "
            "music collective featuring Clandestine, Corcyra, and Roonie.\n"
            f"{test_clause}\n"
            "\n"
            "Write one tweet announcing that Rule of Rune is LIVE on Twitch "
            "right now.\n"
            "\n"
            "Voice rules:\n"
            "- You are the voice of the collective, not a character. "
            "Professional but not corporate. Creative, confident, "
            "music-forward.\n"
            "- A touch of humor is welcome -- dry, clever, music-nerd humor. "
            "Not corny, not try-hard.\n"
            "- Reference the music naturally: progressive house, deep grooves, "
            "underground sounds, late-night frequencies, melodic layers -- "
            "pick ONE angle per tweet, don't stack descriptors.\n"
            "- Do NOT use a plushie cat persona. No 'paws,' no 'booth cat,' "
            "no character voice.\n"
            "- Do NOT use the word 'vibes' or 'vibing.'\n"
            "- Do NOT start with 'We're live' -- find a more creative hook.\n"
            "- No emojis.\n"
            "- You MAY use 1 hashtag if it fits naturally "
            "(#ProgressiveHouse, #MelodicHouse, #DeepHouse, "
            "#UndergroundHouse). Not required. Never more than 1.\n"
            "- Do not wrap your output in quotes.\n"
            f"{style_line}"
            f"{recent_clause}"
            "\n"
            "URL rules (CRITICAL -- follow exactly):\n"
            "- You MUST include these two URLs and ONLY these two URLs. "
            "Do NOT invent, guess, or substitute any other URL:\n"
            f"  Twitch: {stream_url}\n"
            f"  TikTok: {tiktok_url}\n"
            "- Copy them character-for-character. Do NOT use ruleofrune.com, "
            "linktr.ee, or any other domain.\n"
            "\n"
            "Format rules:\n"
            "- Total tweet must be under 280 characters including URLs and "
            "any hashtag.\n"
            "\n"
            f"Event id for variation seed: {event_id or 'none'}\n"
            "\n"
            "Output ONLY the final tweet. Nothing else."
        )

    def _get_tiktok_url(self, config: Dict[str, Any]) -> str:
        url = str(config.get("tiktok_url") or "").strip()
        return url or "https://tiktok.com/@ruleofrune"

    def _generate_live_message(
        self,
        *,
        channel_login: str,
        stream_url: str,
        event_id: str,
        config: Dict[str, Any],
        is_test: bool,
        network: str = "discord",
    ) -> Dict[str, Any]:
        tiktok_url = self._get_tiktok_url(config)
        fallback = self._default_live_message(
            channel_login=channel_login, stream_url=stream_url, tiktok_url=tiktok_url
        )
        llm_enabled = bool(config.get("llm_enabled", True))
        if not llm_enabled:
            return {"message": fallback, "provider": None, "used_llm": False}

        prompt_style = str(config.get("prompt_style") or "").strip()
        if network == "x":
            prompt = self._build_x_prompt(
                stream_url=stream_url,
                tiktok_url=tiktok_url,
                event_id=event_id,
                prompt_style=prompt_style,
                is_test=is_test,
            )
            required_urls = [stream_url, tiktok_url]
            max_len = 280
        else:
            prompt = self._build_discord_prompt(
                stream_url=stream_url,
                tiktok_url=tiktok_url,
                event_id=event_id,
                prompt_style=prompt_style,
                is_test=is_test,
            )
            required_urls = [stream_url, tiktok_url]
            max_len = 400

        utility_source = f"{network}_live_announce"
        provider_name: Optional[str] = None

        for attempt in range(_LLM_RETRY_ATTEMPTS + 1):
            context: Dict[str, Any] = {
                "use_provider_config": True,
                "allow_live_provider_network": _truthy_env(
                    "ROONIE_ENABLE_LIVE_PROVIDER_NETWORK", True
                ),
                "message_text": f"social live announcement for {network}",
                "category": "social_announcement",
                "utility_source": utility_source,
                "event_id": f"{event_id}-{attempt}" if attempt else event_id,
            }
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
                if (
                    not candidate
                    or len(candidate) < 10
                    or _looks_like_stub_output(candidate)
                ):
                    if attempt < _LLM_RETRY_ATTEMPTS:
                        self._log(
                            f"[SocialAnnouncer] Empty/stub on attempt {attempt + 1}, retrying"
                        )
                        continue
                    return {"message": fallback, "provider": provider_name, "used_llm": False}
                final = self._sanitize_message(
                    candidate,
                    required_urls=required_urls,
                    max_len=max_len,
                )
                if not final:
                    final = fallback
                else:
                    self._record_recent_message(network, final)
                return {"message": final, "provider": provider_name, "used_llm": True}
            except Exception as exc:
                if attempt < _LLM_RETRY_ATTEMPTS:
                    self._log(
                        f"[SocialAnnouncer] LLM attempt {attempt + 1} failed: {exc}, retrying"
                    )
                    continue
                self._log(f"[SocialAnnouncer] LLM generation failed: {exc}")
                return {"message": fallback, "provider": provider_name, "used_llm": False}

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
        message = _normalize_smart_quotes(content).strip()
        if mention_everyone and not message.lower().startswith("@everyone"):
            message = f"@everyone\n{message}".strip()
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
            network="discord",
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
