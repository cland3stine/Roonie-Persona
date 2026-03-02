"""Mid-sentence and edge-case name detection for LiveChatBridge._is_direct_mention."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock


def _msg(text: str, *, tags: dict | None = None) -> MagicMock:
    m = MagicMock()
    m.message = text
    m.nick = "testviewer"
    m.tags = tags or {}
    return m


def _check(text: str, *, bot_nick: str = "rooniethecat", tags: dict | None = None) -> bool:
    from roonie.control_room.live_chat import LiveChatBridge
    return LiveChatBridge._is_direct_mention(_msg(text, tags=tags), bot_nick)


class TestExistingDetection:
    """Verify pre-existing first-word and @-prefix detection still works."""

    def test_at_prefix(self):
        assert _check("@roonie hello") is True

    def test_at_prefix_roony(self):
        assert _check("@roony what's up") is True

    def test_first_word(self):
        assert _check("roonie this beat is wild") is True

    def test_first_word_rooney(self):
        assert _check("rooney do you like this?") is True

    def test_first_word_runi(self):
        assert _check("runi hey") is True

    def test_bot_nick_match(self):
        assert _check("rooniethecat hello", bot_nick="rooniethecat") is True

    def test_reply_parent_tag(self):
        assert _check("yes I agree", tags={"reply-parent-user-login": "rooniethecat"}) is True


class TestMidSentenceDetection:
    """Mid-sentence name references that the old code would miss."""

    def test_rooney_mid_sentence(self):
        assert _check("Um... Rooney just sent me an email") is True

    def test_roonie_mid_sentence(self):
        assert _check("I think roonie is cool") is True

    def test_roonie_end_of_sentence(self):
        assert _check("lol roonie is a flirt") is True

    def test_that_is_what_roonie_said(self):
        assert _check("that's what roonie said earlier") is True

    def test_name_with_trailing_punctuation(self):
        assert _check("wait, roonie!") is True

    def test_rooney_in_question(self):
        assert _check("did rooney say something?") is True


class TestNegativeCases:
    """Messages that should NOT trigger direct mention."""

    def test_possessive_form(self):
        assert _check("roonie's laptop is tiny") is False

    def test_no_name_at_all(self):
        assert _check("this track is fire") is False

    def test_emote_only(self):
        assert _check(":) ruleof6Cheshire") is False

    def test_empty_message(self):
        assert _check("") is False

    def test_similar_but_not_alias(self):
        # "roomie" is NOT in _NICK_ALIASES
        assert _check("my roomie is asleep") is False

    def test_embedded_in_longer_word(self):
        # "maroonie" should not match because \b word boundary enforcement
        assert _check("that maroonie color is nice") is False

    def test_possessive_rooney(self):
        assert _check("rooney's screen is small") is False


class TestEdgeCases:
    """Edge cases for robustness."""

    @pytest.mark.parametrize("text,expected", [
        ("ROONIE do something", True),   # uppercase first word
        ("hey ROONIE", True),            # uppercase mid-sentence
        ("roonie, help!", True),          # first word with comma
        ("yo roonie", True),             # second word (mid-sentence)
        ("playing some rooney tunes", True),  # acceptable false positive — rare name
    ])
    def test_mixed_case_and_punctuation(self, text, expected):
        assert _check(text) is expected
