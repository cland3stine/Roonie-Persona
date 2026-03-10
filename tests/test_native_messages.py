from __future__ import annotations

from providers.base import Provider
from providers.router import route_generate
from roonie.prompting import COMPRESSED_STYLE, DEFAULT_STYLE, build_roonie_messages, build_roonie_prompt


class _CaptureProvider(Provider):
    def __init__(self) -> None:
        super().__init__(name="capture", enabled=True)
        self.last_prompt = None
        self.last_messages = None
        self.last_context = None

    def generate(self, *, prompt="", messages=None, context=None):
        self.last_prompt = prompt
        self.last_messages = messages
        self.last_context = dict(context or {})
        return "captured"


class _Registry:
    def __init__(self, provider: Provider) -> None:
        self._provider = provider

    def get_default(self) -> Provider:
        return self._provider


def test_build_roonie_prompt_preserves_legacy_default_style():
    prompt = build_roonie_prompt(message="hello there", metadata={"viewer": "fraggyxx"})

    assert DEFAULT_STYLE.splitlines()[0] in prompt
    assert COMPRESSED_STYLE.splitlines()[0] not in prompt


def test_build_roonie_messages_uses_compressed_style_and_examples():
    messages = build_roonie_messages(
        message="this low-end is ridiculous",
        metadata={"viewer": "djpapakuma", "channel": "ruleof6ix"},
    )

    assert messages[0]["role"] == "system"
    assert COMPRESSED_STYLE.splitlines()[0] in messages[0]["content"]
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"].endswith("djpapakuma: this low-end is ridiculous")
    assert any(msg["role"] == "assistant" and msg["content"] == "[SKIP]" for msg in messages)


def test_route_generate_forwards_messages_when_present():
    provider = _CaptureProvider()
    registry = _Registry(provider)
    messages = [
        {"role": "system", "content": "system block"},
        {"role": "user", "content": "viewer: hello"},
    ]

    out = route_generate(
        registry=registry,
        routing_cfg={},
        prompt="flattened prompt fallback",
        context={"message_text": "hello"},
        messages=messages,
    )

    assert out == "captured"
    assert provider.last_prompt == "flattened prompt fallback"
    assert provider.last_messages == messages