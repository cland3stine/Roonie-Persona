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

    def generate(self, *, prompt: str, context: Dict[str, Any]) -> Optional[str]:
        if not self.enabled:
            return None

        fixture_name = context.get("fixture_name")
        url = "https://api.x.ai/v1/chat/completions"
        payload = {
            "model": context.get("model", "grok-4-1-fast-non-reasoning"),
            "messages": [{"role": "user", "content": prompt}],
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
