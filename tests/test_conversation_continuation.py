"""Tests for conversation continuation detection.

When Roonie recently replied to a viewer, their follow-up messages should be
treated as addressed — even without an explicit @mention.
"""
from __future__ import annotations

from typing import Any, Dict

from roonie.provider_director import ProviderDirector
from roonie.types import Env, Event


SESSION = "continuation-test-session"


def _event(
    event_id: str,
    message: str,
    *,
    user: str = "c0rcyra",
    is_direct_mention: bool = False,
) -> Event:
    return Event(
        event_id=event_id,
        message=message,
        metadata={
            "user": user,
            "is_direct_mention": is_direct_mention,
            "mode": "live",
            "platform": "twitch",
            "session_id": SESSION,
        },
    )


def _stub_route(monkeypatch):
    """Monkeypatch route_generate to return a canned response."""
    captured: Dict[str, Any] = {}

    def _stub(**kwargs):
        captured["prompt"] = kwargs.get("prompt")
        kwargs["context"]["provider_selected"] = "openai"
        kwargs["context"]["moderation_result"] = "allow"
        return "hey, welcome in"

    monkeypatch.setattr("roonie.provider_director.route_generate", _stub)
    return captured


# ---------------------------------------------------------------------------
# Core behavior
# ---------------------------------------------------------------------------


def test_continuation_responds_to_followup(monkeypatch):
    """Viewer addressed Roonie, Roonie responded, viewer sends unaddressed
    follow-up → action is RESPOND_PUBLIC (not NOOP)."""
    _stub_route(monkeypatch)
    director = ProviderDirector()
    env = Env(offline=False)

    # Step 1: viewer @mentions Roonie
    r1 = director.evaluate(_event("e1", "@RoonieTheCat hey!", is_direct_mention=True), env)
    assert r1.action == "RESPOND_PUBLIC"

    # Step 2: confirm Roonie's response was sent
    director.apply_output_feedback(
        event_id="e1", emitted=True, send_result={"sent": True},
    )

    # Step 3: viewer sends follow-up without @mention
    r2 = director.evaluate(_event("e2", "I also have a cardboard box for all your loafing needs"), env)
    assert r2.action == "RESPOND_PUBLIC"


def test_no_prior_context_noops(monkeypatch):
    """No prior context → unaddressed message → NOOP."""
    _stub_route(monkeypatch)
    director = ProviderDirector()
    env = Env(offline=False)

    result = director.evaluate(_event("e1", "random message nobody tagged roonie in"), env)
    # Not addressed (no "roonie" in msg, no is_direct_mention) and no continuation
    # Actually "roonie" IS in that message. Use a message without it.
    result = director.evaluate(_event("e2", "random unrelated chat message lol"), env)
    assert result.action == "NOOP"


def test_different_viewer_noops(monkeypatch):
    """Roonie responded to viewer_a, viewer_b sends unaddressed message → NOOP."""
    _stub_route(monkeypatch)
    director = ProviderDirector()
    env = Env(offline=False)

    # viewer_a talks to Roonie
    r1 = director.evaluate(
        _event("e1", "@RoonieTheCat hey!", user="viewer_a", is_direct_mention=True), env,
    )
    assert r1.action == "RESPOND_PUBLIC"
    director.apply_output_feedback(event_id="e1", emitted=True, send_result={"sent": True})

    # viewer_b sends unaddressed message
    r2 = director.evaluate(_event("e2", "anyone know what track this is", user="viewer_b"), env)
    assert r2.action == "NOOP"


def test_conversation_moved_on_noops(monkeypatch):
    """Roonie responded to viewer_a, then to viewer_b → viewer_a follow-up → NOOP."""
    _stub_route(monkeypatch)
    director = ProviderDirector()
    env = Env(offline=False)

    # viewer_a talks to Roonie
    director.evaluate(
        _event("e1", "@RoonieTheCat hey!", user="viewer_a", is_direct_mention=True), env,
    )
    director.apply_output_feedback(event_id="e1", emitted=True, send_result={"sent": True})

    # viewer_b talks to Roonie
    director.evaluate(
        _event("e2", "@RoonieTheCat what's playing?", user="viewer_b", is_direct_mention=True), env,
    )
    director.apply_output_feedback(event_id="e2", emitted=True, send_result={"sent": True})

    # viewer_a sends unaddressed follow-up — conversation has moved on
    r3 = director.evaluate(_event("e3", "that box is really comfy btw", user="viewer_a"), env)
    assert r3.action == "NOOP"


# ---------------------------------------------------------------------------
# Context storage
# ---------------------------------------------------------------------------


def test_continuation_stored_with_continuation_tag(monkeypatch):
    """Continuation message stored with continuation=True and direct_address=False."""
    _stub_route(monkeypatch)
    director = ProviderDirector()
    env = Env(offline=False)

    # Setup: addressed message + confirmed send
    director.evaluate(_event("e1", "@RoonieTheCat hey!", is_direct_mention=True), env)
    director.apply_output_feedback(event_id="e1", emitted=True, send_result={"sent": True})

    # Continuation message
    director.evaluate(_event("e2", "also have a cardboard box for you"), env)

    # Check the context buffer — continuation tag should be True, direct_address should be False
    turns = director.context_buffer.get_context(max_turns=12)
    continuation_turns = [
        t for t in turns
        if t.speaker == "user" and "cardboard" in t.text.lower()
    ]
    assert len(continuation_turns) == 1
    assert continuation_turns[0].tags.get("direct_address") is False
    assert continuation_turns[0].tags.get("continuation") is True


# ---------------------------------------------------------------------------
# Trace data
# ---------------------------------------------------------------------------


def test_noop_trace_has_continuation_false(monkeypatch):
    """NOOP trace includes conversation_continuation: False."""
    _stub_route(monkeypatch)
    director = ProviderDirector()
    env = Env(offline=False)

    result = director.evaluate(_event("e1", "random chat message lol"), env)
    assert result.action == "NOOP"
    assert result.trace["director"]["conversation_continuation"] is False


def test_respond_trace_has_continuation_true(monkeypatch):
    """RESPOND trace includes conversation_continuation: True for continuation messages."""
    _stub_route(monkeypatch)
    director = ProviderDirector()
    env = Env(offline=False)

    # Setup
    director.evaluate(_event("e1", "@RoonieTheCat hey!", is_direct_mention=True), env)
    director.apply_output_feedback(event_id="e1", emitted=True, send_result={"sent": True})

    # Continuation
    r2 = director.evaluate(_event("e2", "I got a box for your loafing needs"), env)
    assert r2.action == "RESPOND_PUBLIC"
    assert r2.trace["director"]["conversation_continuation"] is True


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_addressed_message_does_not_check_continuation(monkeypatch):
    """Already-addressed message has continuation=False (optimization)."""
    _stub_route(monkeypatch)
    director = ProviderDirector()
    env = Env(offline=False)

    # Setup
    director.evaluate(_event("e1", "@RoonieTheCat hey!", is_direct_mention=True), env)
    director.apply_output_feedback(event_id="e1", emitted=True, send_result={"sent": True})

    # Addressed follow-up — continuation should be False because addressed is True
    r2 = director.evaluate(_event("e2", "@RoonieTheCat nice one", is_direct_mention=True), env)
    assert r2.action == "RESPOND_PUBLIC"
    assert r2.trace["director"]["conversation_continuation"] is False
    assert r2.trace["director"]["addressed_to_roonie"] is True


def test_empty_context_buffer_no_continuation(monkeypatch):
    """Empty context buffer → no continuation."""
    _stub_route(monkeypatch)
    director = ProviderDirector()
    env = Env(offline=False)

    result = director.evaluate(_event("e1", "hello everyone"), env)
    assert result.action == "NOOP"
    assert result.trace["director"]["conversation_continuation"] is False


def test_continuation_works_for_other_category(monkeypatch):
    """Continuation works even for OTHER-category messages (bypasses trigger requirement)."""
    _stub_route(monkeypatch)
    director = ProviderDirector()
    env = Env(offline=False)

    # Setup
    director.evaluate(_event("e1", "@RoonieTheCat hey!", is_direct_mention=True), env)
    director.apply_output_feedback(event_id="e1", emitted=True, send_result={"sent": True})

    # OTHER-category message with no trigger (no ?, no direct verb, long enough)
    # This would normally NOOP even if addressed, because trigger=False for OTHER + no trigger markers.
    # But as a continuation, it should still be evaluated.
    r2 = director.evaluate(_event("e2", "yeah the vibes tonight are absolutely incredible"), env)
    assert r2.action == "RESPOND_PUBLIC"
    assert r2.trace["director"]["conversation_continuation"] is True


def test_unsent_response_does_not_create_continuation(monkeypatch):
    """If Roonie's response was not actually sent, no continuation is created."""
    _stub_route(monkeypatch)
    director = ProviderDirector()
    env = Env(offline=False)

    # Addressed message
    director.evaluate(_event("e1", "@RoonieTheCat hey!", is_direct_mention=True), env)
    # Feedback says NOT sent
    director.apply_output_feedback(event_id="e1", emitted=False, send_result={"sent": False})

    # Follow-up — should NOOP because there's no roonie turn in context
    r2 = director.evaluate(_event("e2", "hello again is anyone there"), env)
    assert r2.action == "NOOP"


# ---------------------------------------------------------------------------
# Recency gate
# ---------------------------------------------------------------------------


def test_continuation_decays_after_multiple_messages(monkeypatch):
    """After Roonie responds to viewer_a, 4+ messages from others → viewer_a follow-up is NOOP."""
    _stub_route(monkeypatch)
    director = ProviderDirector()
    env = Env(offline=False)

    # viewer_a talks to Roonie
    director.evaluate(
        _event("e1", "@RoonieTheCat hey!", user="viewer_a", is_direct_mention=True), env,
    )
    director.apply_output_feedback(event_id="e1", emitted=True, send_result={"sent": True})

    # 4 messages from other users (using ? or @mentions to ensure they get stored in buffer)
    for i in range(4):
        director.evaluate(
            _event(f"fill_{i}", f"@RoonieTheCat question {i}?", user=f"filler_{i}", is_direct_mention=True), env,
        )
        director.apply_output_feedback(event_id=f"fill_{i}", emitted=True, send_result={"sent": True})

    # viewer_a follow-up — recency gate should block continuation
    r = director.evaluate(_event("e_late", "that box was comfy btw", user="viewer_a"), env)
    assert r.action == "NOOP"


# ---------------------------------------------------------------------------
# [SKIP] opt-out
# ---------------------------------------------------------------------------


def test_skip_response_suppresses_output(monkeypatch):
    """LLM returns [SKIP] for continuation → action is NOOP."""
    def _stub_skip(**kwargs):
        kwargs["context"]["provider_selected"] = "openai"
        kwargs["context"]["moderation_result"] = "allow"
        return "[SKIP]"

    monkeypatch.setattr("roonie.provider_director.route_generate", _stub_skip)
    director = ProviderDirector()
    env = Env(offline=False)

    # Setup: addressed + confirmed
    director.evaluate(_event("e1", "@RoonieTheCat hey!", is_direct_mention=True), env)
    director.apply_output_feedback(event_id="e1", emitted=True, send_result={"sent": True})

    # Continuation — LLM returns [SKIP]
    r = director.evaluate(_event("e2", "yeah that was a great set last night"), env)
    assert r.action == "NOOP"
    assert r.trace["director"]["conversation_continuation"] is True
    assert r.trace["director"]["continuation_skipped"] is True
    assert r.response_text is None


def test_skip_not_parsed_for_direct_address(monkeypatch):
    """[SKIP] from LLM for a direct-address message should NOT suppress output."""
    def _stub_skip(**kwargs):
        kwargs["context"]["provider_selected"] = "openai"
        kwargs["context"]["moderation_result"] = "allow"
        return "[SKIP]"

    monkeypatch.setattr("roonie.provider_director.route_generate", _stub_skip)
    director = ProviderDirector()
    env = Env(offline=False)

    # Direct address — [SKIP] should be treated as literal text
    r = director.evaluate(_event("e1", "@RoonieTheCat hey!", is_direct_mention=True), env)
    assert r.action == "RESPOND_PUBLIC"
    assert r.response_text is not None


# ---------------------------------------------------------------------------
# Safety cap
# ---------------------------------------------------------------------------


def test_continuation_cap_forces_noop_after_streak(monkeypatch):
    """After 4 consecutive continuation responses, 5th is NOOP (capped)."""
    _stub_route(monkeypatch)
    director = ProviderDirector()
    env = Env(offline=False)

    # Initial direct address
    director.evaluate(
        _event("e0", "@RoonieTheCat hey!", user="viewer_a", is_direct_mention=True), env,
    )
    director.apply_output_feedback(event_id="e0", emitted=True, send_result={"sent": True})

    # 4 continuation responses (each one: message → feedback → next)
    for i in range(1, 5):
        r = director.evaluate(
            _event(f"e{i}", f"continuation message {i}", user="viewer_a"), env,
        )
        assert r.action == "RESPOND_PUBLIC", f"continuation {i} should respond"
        director.apply_output_feedback(event_id=f"e{i}", emitted=True, send_result={"sent": True})

    # 5th continuation → should be capped
    r5 = director.evaluate(_event("e5", "still chatting away here", user="viewer_a"), env)
    assert r5.action == "NOOP"
    assert r5.trace["director"]["continuation_capped"] is True


def test_continuation_cap_resets_on_direct_address(monkeypatch):
    """3 continuation responses → direct address resets streak → next continuation works."""
    _stub_route(monkeypatch)
    director = ProviderDirector()
    env = Env(offline=False)

    # Initial direct address
    director.evaluate(
        _event("e0", "@RoonieTheCat hey!", user="viewer_a", is_direct_mention=True), env,
    )
    director.apply_output_feedback(event_id="e0", emitted=True, send_result={"sent": True})

    # 3 continuation responses
    for i in range(1, 4):
        r = director.evaluate(
            _event(f"e{i}", f"continuation message {i}", user="viewer_a"), env,
        )
        assert r.action == "RESPOND_PUBLIC"
        director.apply_output_feedback(event_id=f"e{i}", emitted=True, send_result={"sent": True})

    # Direct address resets streak
    director.evaluate(
        _event("e_reset", "@RoonieTheCat check this out!", user="viewer_a", is_direct_mention=True), env,
    )
    director.apply_output_feedback(event_id="e_reset", emitted=True, send_result={"sent": True})

    # Next continuation should work (streak was reset)
    r_after = director.evaluate(
        _event("e_after", "yeah that was wild right", user="viewer_a"), env,
    )
    assert r_after.action == "RESPOND_PUBLIC"
    assert r_after.trace["director"]["conversation_continuation"] is True
