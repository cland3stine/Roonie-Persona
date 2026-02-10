from __future__ import annotations

from typing import Any, Dict, Optional

from src.providers.base import Provider
from src.roonie.network.types import Transport


class AnthropicProvider(Provider):
    transport: Transport
    api_key: str

    def __init__(
        self,
        *,
        enabled: bool,
        transport: Transport,
        api_key: str = "",
        name: str = "anthropic",
    ):
        super().__init__(name=name, enabled=enabled)
        object.__setattr__(self, "transport", transport)
        object.__setattr__(self, "api_key", api_key)

    def generate(self, *, prompt: str, context: Dict[str, Any]) -> Optional[str]:
        if not self.enabled:
            return None

        fixture_name = context.get("fixture_name")
        url = "https://api.anthropic.com/v1/messages"
        payload = {
            "model": context.get("model", "claude-sonnet-4-5-20250929"),
            "max_tokens": int(context.get("max_tokens", 140)),
            "messages": [{"role": "user", "content": prompt}],
        }
        api_key = (self.api_key or "").strip() or "REDACTED"
        headers = {"x-api-key": api_key}

        resp = self.transport.post_json(
            url,
            payload=payload,
            headers=headers,
            fixture_name=str(fixture_name) if fixture_name is not None else None,
        )

        if int(resp.status) != 200 or not isinstance(resp.body, dict):
            return None

        try:
            body = resp.body

            # 1) Anthropic Messages API common shape:
            # {"content":[{"type":"text","text":"..."}], ...}
            content = body.get("content") if isinstance(body, dict) else None
            if isinstance(content, list):
                parts = []
                for part in content:
                    if isinstance(part, dict):
                        t = part.get("text")
                        if t is not None:
                            parts.append(str(t))
                if parts:
                    return "\n".join(parts)

            # 2) Some variants use 'completion'
            completion = body.get("completion") if isinstance(body, dict) else None
            if isinstance(completion, str) and completion.strip():
                return completion

            # 3) Gateway / wrapper variants (OpenAI-ish):
            # {"choices":[{"message":{"content":"..."}}]}
            choices = body.get("choices") if isinstance(body, dict) else None
            if isinstance(choices, list) and choices:
                ch0 = choices[0]
                if isinstance(ch0, dict):
                    msg = ch0.get("message")
                    if isinstance(msg, dict):
                        c = msg.get("content")
                        if isinstance(c, str) and c.strip():
                            return c
                    # sometimes: {"text":"..."}
                    t = ch0.get("text")
                    if isinstance(t, str) and t.strip():
                        return t

            # If we got here, response succeeded but we couldn't find text.
            # Raise a schema-only error (safe) so the caller can log it in shadow mode.
            if isinstance(body, dict):
                keys = sorted(list(body.keys()))
                sample = {k: type(body.get(k)).__name__ for k in keys[:25]}
                # If we got here, response succeeded but we couldn't find text.
            # Return schema-only marker for shadow logs (safe, no content).
            if isinstance(body, dict):
                keys = sorted(list(body.keys()))
                sample = {k: type(body.get(k)).__name__ for k in keys[:25]}
                return f"[[SCHEMA]] keys={keys[:25]} types={sample}"
            return f"[[SCHEMA]] body_type={type(body).__name__}"
        except Exception:
            return None
