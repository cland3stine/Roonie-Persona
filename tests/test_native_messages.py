from __future__ import annotations

from providers.base import Provider
from providers.router import route_generate
from roonie.behavior_spec import CATEGORY_EVENT_CHEER, behavior_guidance
from roonie.prompting import COMPRESSED_STYLE, DEFAULT_STYLE, EXAMPLE_BANK, build_roonie_messages, build_roonie_prompt


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

def test_compressed_style_retains_dry_playful_humor_guidance():
    assert "dry, playful sense of humor" in COMPRESSED_STYLE
    assert "plushie cat on a DJ booth" in COMPRESSED_STYLE


def test_example_bank_contains_deadpan_booth_cat_humor_examples():
    examples = {str(item.get("kind")): str(item.get("assistant", "")) for item in EXAMPLE_BANK}

    assert examples["banter_terse"] == "@fraggyxx i don't have eyelids."
    assert examples["banter_warmth"] == "@c0rcyra on a laptop? i can barely hit the right keys on a full keyboard."
    assert examples["identity_deadpan"] == "@nightowl99 sit on the booth. judge transitions. fall over when the bass hits. it's a full schedule."
    assert examples["raid"] == "@royal_lama_ that's how you show up. 101 deep too."
    assert examples["cheer"] == "@darkorange73 100 bits? caught the exact moment for that."
    assert examples["contrast_pair_good_specific"] == "@dirty13duck booth just got a little more crowded."


def test_event_guidance_discourages_template_event_shapes():
    guidance = behavior_guidance(
        category=CATEGORY_EVENT_CHEER,
        approved_emotes=[],
        now_playing_available=True,
    )

    assert "Vary the sentence shape" in guidance
    assert "Don't always lead with the count" in guidance
