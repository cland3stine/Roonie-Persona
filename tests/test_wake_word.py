"""Tests for the wake-word detector (audio.wake_word)."""
from __future__ import annotations

import pytest

from audio.wake_word import WakeWordDetector, WakeWordResult


@pytest.fixture
def detector() -> WakeWordDetector:
    return WakeWordDetector()


# ── basic detection ─────────────────────────────────────────


def test_exact_roonie(detector: WakeWordDetector):
    result = detector.detect("roonie what song is this")
    assert result.detected is True
    assert result.confidence == 1.0
    assert result.remaining_text == "what song is this"


def test_hey_roonie(detector: WakeWordDetector):
    result = detector.detect("hey roonie are you there")
    assert result.detected is True
    assert result.trigger_phrase.lower().startswith("hey roonie")
    assert result.remaining_text == "are you there"


def test_case_insensitive(detector: WakeWordDetector):
    result = detector.detect("ROONIE play something")
    assert result.detected is True
    assert result.confidence == 1.0


def test_hey_Roonie_mixed_case(detector: WakeWordDetector):
    result = detector.detect("Hey Roonie, what's playing?")
    assert result.detected is True
    assert "what's playing?" in result.remaining_text


# ── fuzzy variants ──────────────────────────────────────────


def test_runi_variant(detector: WakeWordDetector):
    result = detector.detect("runi what track is this")
    assert result.detected is True
    assert result.confidence == 0.85


def test_runie_variant(detector: WakeWordDetector):
    result = detector.detect("hey runie are you listening")
    assert result.detected is True
    assert result.confidence == 0.80


def test_rooney_variant(detector: WakeWordDetector):
    result = detector.detect("rooney what time is the next stream")
    assert result.detected is True
    assert result.confidence == 0.75


def test_roomie_variant(detector: WakeWordDetector):
    result = detector.detect("roomie tell me about the set")
    assert result.detected is True
    assert result.confidence == 0.70


# ── non-detection ───────────────────────────────────────────


def test_no_wake_word(detector: WakeWordDetector):
    result = detector.detect("this song is amazing")
    assert result.detected is False
    assert result.confidence == 0.0
    assert result.remaining_text == ""


def test_empty_string(detector: WakeWordDetector):
    result = detector.detect("")
    assert result.detected is False


def test_none_like_empty(detector: WakeWordDetector):
    result = detector.detect("   ")
    assert result.detected is False


# ── edge cases ──────────────────────────────────────────────


def test_wake_word_at_end(detector: WakeWordDetector):
    result = detector.detect("I love roonie")
    assert result.detected is True
    assert result.remaining_text == ""


def test_wake_word_only(detector: WakeWordDetector):
    result = detector.detect("roonie")
    assert result.detected is True
    assert result.remaining_text == ""


def test_wake_word_with_punctuation(detector: WakeWordDetector):
    result = detector.detect("hey roonie, what's the next stream?")
    assert result.detected is True
    assert "what's the next stream?" in result.remaining_text


def test_wake_word_mid_sentence(detector: WakeWordDetector):
    result = detector.detect("so hey roonie what do you think")
    assert result.detected is True
    assert "what do you think" in result.remaining_text


def test_partial_word_not_detected(detector: WakeWordDetector):
    """'cartoonie' should not trigger the wake word."""
    result = detector.detect("that was cartoonie style")
    assert result.detected is False


def test_highest_confidence_wins(detector: WakeWordDetector):
    """When 'roonie' is present, it should match at 1.0 even if 'roomie' also appears later."""
    result = detector.detect("roonie and roomie walked in")
    assert result.detected is True
    assert result.confidence == 1.0
