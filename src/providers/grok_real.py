from __future__ import annotations

from typing import Any, Dict, Optional

from providers.base import Provider
from roonie.network.types import Transport


class GrokProvider(Provider):
    transport: Transport
    api_key: str

    def __init__(
        self,
        *,
        enabled: bool,
        transport: Transport,
        api_key: str = "",
        name: str = "grok",
    ):
        super().__init__(name=name, enabled=enabled)
        object.__setattr__(self, "transport", transport)
        object.__setattr__(self, "api_key", api_key)

    def generate(
        self,
        *,
        prompt: str = "",
        messages: Optional[list[Dict[str, str]]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        if not self.enabled:
            return None

        ctx = context or {}
        fixture_name = ctx.get("fixture_name")
        url = "https://api.x.ai/v1/chat/completions"
        payload_messages = list(messages or []) or [{"role": "user", "content": prompt}]
        payload = {
            "model": ctx.get("model", "grok-4-1-fast-non-reasoning"),
            "messages": payload_messages,
            "temperature": float(ctx.get("temperature", 0.65)),
            "max_tokens": int(ctx.get("max_tokens", 120)),
        }
        api_key = (self.api_key or "").strip() or "REDACTED"
        headers = {"Authorization": f"Bearer {api_key}"}

        resp = self.transport.post_json(
            url,
            payload=payload,
            headers=headers,
            fixture_name=str(fixture_name) if fixture_name is not None else None,
        )

        if int(resp.status) != 200 or not isinstance(resp.body, dict):
            return None

        try:
            return str(resp.body["choices"][0]["message"]["content"])
        except Exception:
            return None