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
            # Common shapes:
            # - {"content":[{"type":"text","text":"..."}]}
            # - {"content":"..."}  (less common)
            content = resp.body.get("content")
            if isinstance(content, list):
                # collect any text fields
                text_parts = []
                for part in content:
                    if isinstance(part, dict):
                        t = part.get("text")
                        if t is not None:
                            text_parts.append(str(t))
                if text_parts:
                    return "\n".join(text_parts)
                return None
            if isinstance(content, str):
                return content
            # Some error-ish bodies still return 200 in edge cases; be conservative.
            return None
        except Exception:
            return None
