from __future__ import annotations

from pathlib import Path

from roonie.network.transports import FakeTransport

def _fixture_dir() -> Path:
    return Path("tests/fixtures/v1_11a_providers")

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
