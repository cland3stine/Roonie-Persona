"""BL-PER-002 — Banter-ratio tuning prompt & guidance assertions.

Validates that deflection coaching, opener variety, and smooth-redirect
instructions are present in the prompt and behavior guidance, and that
existing guardrails remain intact.
"""

from __future__ import annotations

from roonie.behavior_spec import (
    CATEGORY_BANTER,
    CATEGORY_GREETING,
    behavior_guidance,
)
from roonie.prompting import DEFAULT_STYLE


# ---------------------------------------------------------------------------
# Prompt text assertions (DEFAULT_STYLE)
# ---------------------------------------------------------------------------


class TestDeflectionCoaching:
    """Deflection coaching examples appear in 'Respect and boundaries'."""

    def test_contains_example_fraggy(self):
        assert "nah, I like fraggy" in DEFAULT_STYLE

    def test_contains_policy_document_antipattern(self):
        assert "policy document" in DEFAULT_STYLE

    def test_coaching_after_roast_rule(self):
        roast_pos = DEFAULT_STYLE.index("You are not a weapon pointed at other people.")
        coaching_pos = DEFAULT_STYLE.index("not like a policy document")
        assert coaching_pos > roast_pos

    def test_coaching_before_plushie_life(self):
        coaching_pos = DEFAULT_STYLE.index("not like a policy document")
        plushie_pos = DEFAULT_STYLE.index("Your plushie life:")
        assert coaching_pos < plushie_pos


class TestOpenerVariety:
    """Opener variety instruction appears in 'How you talk'."""

    def test_contains_opener_variety_instruction(self):
        assert "Don't start every response the same way" in DEFAULT_STYLE

    def test_variety_in_how_you_talk_section(self):
        how_you_talk_pos = DEFAULT_STYLE.index("How you talk:")
        reading_room_pos = DEFAULT_STYLE.index("Reading the room:")
        variety_pos = DEFAULT_STYLE.index("Don't start every response the same way")
        assert how_you_talk_pos < variety_pos < reading_room_pos


class TestExistingGuardrailsIntact:
    """Core guardrails remain after banter-tuning additions."""

    def test_roast_refusal_present(self):
        assert "You do not roast, mock, or make fun of anyone on request" in DEFAULT_STYLE

    def test_fabrication_rule_present(self):
        assert "You do not fabricate memories" in DEFAULT_STYLE

    def test_teasing_scope_present(self):
        assert "Light, playful teasing between you and your humans" in DEFAULT_STYLE


# ---------------------------------------------------------------------------
# Behavior guidance assertions (behavior_spec)
# ---------------------------------------------------------------------------


class TestBanterGuidance:
    """BANTER category includes smooth-redirect instruction."""

    def _banter_guidance(self, **kwargs):
        return behavior_guidance(
            category=CATEGORY_BANTER,
            approved_emotes=["roonieLove"],
            now_playing_available=False,
            **kwargs,
        )

    def test_smooth_redirect_present(self):
        text = self._banter_guidance()
        assert "Never sound like you're reading a policy" in text

    def test_chat_naturally_still_present(self):
        text = self._banter_guidance()
        assert "Chat naturally" in text

    def test_teasing_scope_still_present(self):
        text = self._banter_guidance()
        assert "people you know well" in text

    def test_short_ack_still_works(self):
        text = self._banter_guidance(short_ack_preferred=True)
        assert "one short acknowledgment sentence" in text


class TestGreetingGuidanceUnchanged:
    """GREETING guidance should NOT include redirect instruction."""

    def test_no_redirect_in_greeting(self):
        text = behavior_guidance(
            category=CATEGORY_GREETING,
            approved_emotes=[],
            now_playing_available=False,
        )
        assert "Never sound like you're reading a policy" not in text
