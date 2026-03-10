from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from roonie.network.transports import FakeTransport
from roonie.network.types import HttpResponse


def _fixture_dir() -> Path:
    return Path("tests/fixtures/v1_11a_providers")


class RecordingTransport:
    def __init__(self, body: Dict[str, Any]) -> None:
        self.body = body
        self.last_url: Optional[str] = None
        self.last_payload: Optional[Dict[str, Any]] = None
        self.last_headers: Optional[Dict[str, str]] = None
        self.last_fixture_name: Optional[str] = None

    def post_json(self, url: str, *, payload: Dict[str, Any], headers=None, fixture_name=None) -> HttpResponse:
        self.last_url = url
        self.last_payload = payload
        self.last_headers = headers
        self.last_fixture_name = fixture_name
        return HttpResponse(status=200, headers={}, body=self.body)


def test_openai_real_provider_uses_transport_fixture():
    from providers.openai_real import OpenAIProvider

    t = FakeTransport(_fixture_dir() / "openai")
    p = OpenAIProvider(enabled=True, transport=t)
    out = p.generate(prompt="TEST: hello", context={"fixture_name": "chat_completion_ok"})
    assert out == "OpenAI says hello (fixture)."


def test_anthropic_real_provider_uses_transport_fixture():
    from providers.anthropic_real import AnthropicProvider

    t = FakeTransport(_fixture_dir() / "anthropic")
    p = AnthropicProvider(enabled=True, transport=t)
    out = p.generate(prompt="TEST: hello", context={"fixture_name": "messages_ok"})
    assert out == "Claude says hello (fixture)."


def test_grok_real_provider_uses_transport_fixture():
    from providers.grok_real import GrokProvider

    t = FakeTransport(_fixture_dir() / "grok")
    p = GrokProvider(enabled=True, transport=t)
    out = p.generate(prompt="TEST: hello", context={"fixture_name": "chat_completion_ok"})
    assert out == "Grok says hello (fixture)."


def test_openai_real_provider_prefers_native_messages_payload():
    from providers.openai_real import OpenAIProvider

    transport = RecordingTransport({"choices": [{"message": {"content": "ok"}}]})
    provider = OpenAIProvider(enabled=True, transport=transport)
    messages = [
        {"role": "system", "content": "system note"},
        {"role": "user", "content": "viewer: hello"},
    ]

    out = provider.generate(prompt="fallback prompt", messages=messages, context={"model": "gpt-5.2"})

    assert out == "ok"
    assert transport.last_payload is not None
    assert transport.last_payload["model"] == "gpt-5.2"
    assert transport.last_payload["messages"] == messages


def test_grok_real_provider_prefers_native_messages_payload():
    from providers.grok_real import GrokProvider

    transport = RecordingTransport({"choices": [{"message": {"content": "ok"}}]})
    provider = GrokProvider(enabled=True, transport=transport)
    messages = [
        {"role": "system", "content": "system note"},
        {"role": "user", "content": "viewer: hello"},
    ]

    out = provider.generate(prompt="fallback prompt", messages=messages, context={"model": "grok-4-1-fast-reasoning"})

    assert out == "ok"
    assert transport.last_payload is not None
    assert transport.last_payload["model"] == "grok-4-1-fast-reasoning"
    assert transport.last_payload["messages"] == messages


def test_anthropic_real_provider_splits_system_message_from_native_messages():
    from providers.anthropic_real import AnthropicProvider

    transport = RecordingTransport({"content": [{"type": "text", "text": "ok"}]})
    provider = AnthropicProvider(enabled=True, transport=transport)
    messages = [
        {"role": "system", "content": "system note"},
        {"role": "assistant", "content": "last reply"},
        {"role": "user", "content": "viewer: hello"},
    ]

    out = provider.generate(prompt="fallback prompt", messages=messages, context={"model": "claude-opus-4-6"})

    assert out == "ok"
    assert transport.last_payload is not None
    assert transport.last_payload["model"] == "claude-opus-4-6"
    assert transport.last_payload["system"] == "system note"
    assert transport.last_payload["messages"] == [
        {"role": "assistant", "content": "last reply"},
        {"role": "user", "content": "viewer: hello"},
    ]