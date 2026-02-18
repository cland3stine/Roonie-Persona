from __future__ import annotations

import os

import pytest

from responders.typing_delay import compute_typing_delay


class TestComputeTypingDelay:
    """Unit tests for compute_typing_delay — no actual sleeping."""

    def test_short_message(self):
        # "hey, welcome in" ≈ 15 chars → raw ≈ 2.75 s → jittered 2.06-3.44
        for _ in range(50):
            d = compute_typing_delay("hey, welcome in")
            assert 2.0 <= d <= 4.5

    def test_medium_message(self):
        msg = "that bassline is doing serious work underneath those pads"
        for _ in range(50):
            d = compute_typing_delay(msg)
            assert 4.0 <= d <= 9.0

    def test_long_message(self):
        msg = (
            "I love how you layered the reverb on that vocal chop, "
            "it really opened up the whole mix. The percussion swap "
            "around the two-minute mark was a nice touch too."
        )
        for _ in range(50):
            d = compute_typing_delay(msg)
            assert 8.0 <= d <= 12.0

    def test_very_long_capped_at_12(self):
        msg = "x" * 500
        for _ in range(50):
            d = compute_typing_delay(msg)
            assert d == 12.0

    def test_empty_string_gets_floor(self):
        for _ in range(50):
            d = compute_typing_delay("")
            assert d == 2.0

    def test_disabled_via_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ROONIE_TYPING_DELAY_ENABLED", "0")
        assert compute_typing_delay("hello world") == 0.0

    def test_enabled_by_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ROONIE_TYPING_DELAY_ENABLED", raising=False)
        d = compute_typing_delay("hello world")
        assert d >= 2.0

    def test_result_varies(self):
        # Jitter should produce different values across calls
        results = {compute_typing_delay("some test message") for _ in range(20)}
        assert len(results) > 1, "expected jitter to produce varying delays"
