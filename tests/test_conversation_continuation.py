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


def test_continuation_stored_with_direct_address_tag(monkeypatch):
    """Continuation message stored in context buffer with direct_address=True tag."""
    _stub_route(monkeypatch)
    director = ProviderDirector()
    env = Env(offline=False)

    # Setup: addressed message + confirmed send
    director.evaluate(_event("e1", "@RoonieTheCat hey!", is_direct_mention=True), env)
    director.apply_output_feedback(event_id="e1", emitted=True, send_result={"sent": True})

    # Continuation message
    director.evaluate(_event("e2", "also have a cardboard box for you"), env)

    # Check the context buffer — the continuation turn should have direct_address=True
    turns = director.context_buffer.get_context(max_turns=12)
    continuation_turns = [
        t for t in turns
        if t.speaker == "user" and "cardboard" in t.text.lower()
    ]
    assert len(continuation_turns) == 1
    assert continuation_turns[0].tags.get("direct_address") is True


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
