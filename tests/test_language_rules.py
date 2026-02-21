from __future__ import annotations

from roonie.language_rules import (
    contains_direct_verb_word,
    is_live_greeting_message,
    is_pure_greeting_message,
    starts_with_direct_verb,
)


def test_starts_with_direct_verb_uses_word_boundaries() -> None:
    assert starts_with_direct_verb("show me the cue point") is True
    assert starts_with_direct_verb("show, me the cue point") is True
    assert starts_with_direct_verb("showing me the cue point") is False
    assert starts_with_direct_verb("dojo vibes tonight") is False


def test_contains_direct_verb_word_uses_word_tokens() -> None:
    assert contains_direct_verb_word("can you show me that again") is True
    assert contains_direct_verb_word("please refresh it") is True
    assert contains_direct_verb_word("showing support for the set") is False
    assert contains_direct_verb_word("the dojo lane is locked in") is False


def test_is_pure_greeting_message_is_strict_about_followups() -> None:
    assert is_pure_greeting_message("@RoonieTheCat hey there!") is True
    assert is_pure_greeting_message("@RoonieTheCat hey") is True
    assert is_pure_greeting_message("@RoonieTheCat hey what are you doing?") is False
    assert is_pure_greeting_message("@RoonieTheCat hey what's new") is False


def test_is_live_greeting_message_requires_live_or_twitch() -> None:
    assert is_live_greeting_message(message="@RoonieTheCat hey there", mode="live", platform="") is True
    assert is_live_greeting_message(message="@RoonieTheCat hey there", mode="", platform="twitch") is True
    assert is_live_greeting_message(message="@RoonieTheCat hey there", mode="offline", platform="discord") is False
