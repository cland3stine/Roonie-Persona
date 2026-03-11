from __future__ import annotations

from typing import Any, Dict, Optional

from providers.base import Provider
from roonie.network.types import Transport


def _extract_anthropic_text(body: Any) -> str:
    if not isinstance(body, dict):
        return ""

    content = body.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") != "text":
                continue
            text = part.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
        if parts:
            return "".join(parts).strip()

    if isinstance(content, str) and content.strip():
        return content

    completion = body.get("completion")
    if isinstance(completion, str) and completion.strip():
        return completion

    choices = body.get("choices")
    if isinstance(choices, list) and choices:
        ch0 = choices[0]
        if isinstance(ch0, dict):
            msg = ch0.get("message")
            if isinstance(msg, dict):
                c = msg.get("content")
                if isinstance(c, str) and c.strip():
                    return c
            t = ch0.get("text")
            if isinstance(t, str) and t.strip():
                return t

    return ""


def _coerce_anthropic_messages(
    *,
    prompt: str = "",
    messages: Optional[list[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    system_parts: list[str] = []
    payload_messages: list[Dict[str, str]] = []
    for message in list(messages or []):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", "user")).strip().lower() or "user"
        content = str(message.get("content", "")).strip()
        if not content:
            continue
        if role == "system":
            system_parts.append(content)
            continue
        if role not in {"user", "assistant"}:
            role = "user"
        payload_messages.append({"role": role, "content": content})
    if not payload_messages:
        payload_messages.append({"role": "user", "content": str(prompt or "")})
    payload: Dict[str, Any] = {"messages": payload_messages}
    if system_parts:
        payload["system"] = "\n\n".join(system_parts)
    return payload


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
        url = "https://api.anthropic.com/v1/messages"
        payload = {
            "model": ctx.get("model", "claude-opus-4-6"),
            "max_tokens": int(ctx.get("max_tokens", 120)),
            "temperature": float(ctx.get("temperature", 0.65)),
        }
        payload.update(_coerce_anthropic_messages(prompt=prompt, messages=messages))
        api_key = (self.api_key or "").strip() or "REDACTED"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

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

            extracted = _extract_anthropic_text(body)
            if extracted:
                return extracted

            if isinstance(body, dict):
                keys = sorted(list(body.keys()))
                sample = {k: type(body.get(k)).__name__ for k in keys[:25]}
                return f"[[SCHEMA]] keys={keys[:25]} types={sample}"
            return f"[[SCHEMA]] body_type={type(body).__name__}"
        except Exception:
            return None