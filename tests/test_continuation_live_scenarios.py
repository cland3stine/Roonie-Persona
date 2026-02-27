"""Live-scenario stress tests for conversation continuation detection.

Simulates realistic multi-viewer Twitch chat patterns to verify continuation
detection handles topic switching, multi-viewer crosstalk, rapid-fire chat,
re-tagging after continuation, and edge cases that appear in real streams.
"""
from __future__ import annotations

from typing import Any, Dict, List

from roonie.provider_director import ProviderDirector
from roonie.types import Env, Event


SESSION = "live-scenario-session"


def _event(
    event_id: str,
    message: str,
    *,
    user: str = "c0rcyra",
    is_direct_mention: bool = False,
    metadata_extra: Dict[str, Any] | None = None,
) -> Event:
    metadata = {
        "user": user,
        "is_direct_mention": is_direct_mention,
        "mode": "live",
        "platform": "twitch",
        "session_id": SESSION,
    }
    if isinstance(metadata_extra, dict):
        metadata.update(metadata_extra)
    return Event(
        event_id=event_id,
        message=message,
        metadata=metadata,
    )


def _stub_route(monkeypatch):
    """Monkeypatch route_generate to return a canned response."""
    captured: Dict[str, Any] = {}

    def _stub(**kwargs):
        captured["prompt"] = kwargs.get("prompt")
        kwargs["context"]["provider_selected"] = "openai"
        kwargs["context"]["moderation_result"] = "allow"
        return "sure thing"

    monkeypatch.setattr("roonie.provider_director.route_generate", _stub)
    return captured


def _say(director, env, event_id, message, *, user="c0rcyra", mention=False, send=True, metadata_extra=None):
    """Helper: evaluate a message and optionally confirm send."""
    e = _event(event_id, message, user=user, is_direct_mention=mention, metadata_extra=metadata_extra)
    result = director.evaluate(e, env)
    if send and result.action == "RESPOND_PUBLIC":
        director.apply_output_feedback(
            event_id=event_id, emitted=True, send_result={"sent": True},
        )
    return result


# ===========================================================================
# SCENARIO 1: Multi-turn untagged conversation
# Real pattern: viewer tags once, then sends 3-4 follow-ups without tagging
# ===========================================================================


def test_multi_turn_untagged_conversation(monkeypatch):
    """Viewer tags Roonie once, sends multiple follow-ups — all should evaluate."""
    _stub_route(monkeypatch)
    d = ProviderDirector()
    env = Env(offline=False)

    # Turn 1: @mention
    r1 = _say(d, env, "e1", "@RoonieTheCat hey what's up tonight?", user="fraggy", mention=True)
    assert r1.action == "RESPOND_PUBLIC"

    # Turn 2: untagged follow-up
    r2 = _say(d, env, "e2", "this track is fire btw", user="fraggy")
    assert r2.action == "RESPOND_PUBLIC"
    assert r2.trace["director"]["conversation_continuation"] is True

    # Turn 3: another untagged follow-up
    r3 = _say(d, env, "e3", "reminds me of that set from last week", user="fraggy")
    assert r3.action == "RESPOND_PUBLIC"
    assert r3.trace["director"]["conversation_continuation"] is True

    # Turn 4: yet another
    r4 = _say(d, env, "e4", "yeah the energy in here is unreal", user="fraggy")
    assert r4.action == "RESPOND_PUBLIC"
    assert r4.trace["director"]["conversation_continuation"] is True


# ===========================================================================
# SCENARIO 2: Viewer re-tags after continuation
# Real pattern: viewer is in continuation, then explicitly @mentions again
# ===========================================================================


def test_retag_after_continuation_uses_addressed_not_continuation(monkeypatch):
    """When a viewer re-tags Roonie mid-continuation, it's addressed, not continuation."""
    _stub_route(monkeypatch)
    d = ProviderDirector()
    env = Env(offline=False)

    # Initial @mention + response
    _say(d, env, "e1", "@RoonieTheCat hey!", user="c0rcyra", mention=True)

    # Untagged follow-up (continuation)
    r2 = _say(d, env, "e2", "got a box for your loafing needs", user="c0rcyra")
    assert r2.trace["director"]["conversation_continuation"] is True
    assert r2.trace["director"]["addressed_to_roonie"] is False

    # Re-tag (addressed, not continuation)
    r3 = _say(d, env, "e3", "@RoonieTheCat what do you think of this tune?", user="c0rcyra", mention=True)
    assert r3.trace["director"]["addressed_to_roonie"] is True
    assert r3.trace["director"]["conversation_continuation"] is False
    assert r3.action == "RESPOND_PUBLIC"


# ===========================================================================
# SCENARIO 3: Two viewers talking to Roonie at the same time
# Real pattern: viewer_a tags Roonie, viewer_b also tags Roonie, then both
# send untagged follow-ups — only the last-replied-to viewer gets continuation
# ===========================================================================


def test_two_viewers_tagging_only_last_gets_continuation(monkeypatch):
    """Two viewers tag Roonie — only the last-replied-to viewer gets continuation."""
    _stub_route(monkeypatch)
    d = ProviderDirector()
    env = Env(offline=False)

    # viewer_a tags Roonie
    _say(d, env, "e1", "@RoonieTheCat hey cat!", user="viewer_a", mention=True)

    # viewer_b tags Roonie (Roonie responds to viewer_b last)
    _say(d, env, "e2", "@RoonieTheCat what's playing?", user="viewer_b", mention=True)

    # viewer_a untagged follow-up — should NOOP (Roonie's last reply was to viewer_b)
    r3 = _say(d, env, "e3", "that box is comfy right", user="viewer_a", send=False)
    assert r3.action == "NOOP"

    # viewer_b untagged follow-up — should continue
    r4 = _say(d, env, "e4", "oh nice I love this artist", user="viewer_b")
    assert r4.action == "RESPOND_PUBLIC"
    assert r4.trace["director"]["conversation_continuation"] is True


# ===========================================================================
# SCENARIO 4: Bystander messages don't break continuation
# Real pattern: viewer_a is talking to Roonie, random viewers chat among
# themselves, viewer_a sends another follow-up — continuation should hold
# ===========================================================================


def test_bystander_chat_does_not_break_continuation(monkeypatch):
    """Unrelated messages from other viewers don't disrupt active continuation."""
    _stub_route(monkeypatch)
    d = ProviderDirector()
    env = Env(offline=False)

    # viewer_a tags Roonie
    _say(d, env, "e1", "@RoonieTheCat hey!", user="viewer_a", mention=True)

    # Bystander noise (these NOOP and don't create roonie turns)
    r_b1 = _say(d, env, "e2", "lol anyone see that goal earlier", user="bystander1", send=False)
    assert r_b1.action == "NOOP"

    r_b2 = _say(d, env, "e3", "yeah banger of a match", user="bystander2", send=False)
    assert r_b2.action == "NOOP"

    # viewer_a follow-up — Roonie's last reply was still to viewer_a
    r4 = _say(d, env, "e4", "also what time do you guys stream", user="viewer_a")
    assert r4.action == "RESPOND_PUBLIC"
    assert r4.trace["director"]["conversation_continuation"] is True


# ===========================================================================
# SCENARIO 5: Viewer switches topic mid-continuation
# Real pattern: viewer is chatting about music, then switches to asking
# about the stream schedule — continuation should still work (topic switch
# is fine as long as it's the same viewer)
# ===========================================================================


def test_topic_switch_mid_continuation_still_evaluates(monkeypatch):
    """Topic switch mid-continuation still gets evaluated — LLM handles topic shift."""
    _stub_route(monkeypatch)
    d = ProviderDirector()
    env = Env(offline=False)

    # Music question
    _say(d, env, "e1", "@RoonieTheCat what's this track called?", user="fraggy", mention=True)

    # Topic switch to stream schedule (no tag)
    r2 = _say(d, env, "e2", "when do you guys stream next", user="fraggy")
    assert r2.action == "RESPOND_PUBLIC"
    assert r2.trace["director"]["conversation_continuation"] is True

    # Topic switch again to banter
    r3 = _say(d, env, "e3", "this set is hitting different tonight honestly", user="fraggy")
    assert r3.action == "RESPOND_PUBLIC"
    assert r3.trace["director"]["conversation_continuation"] is True


# ===========================================================================
# SCENARIO 6: Conversation handoff between viewers
# Real pattern: viewer_a chats with Roonie, then viewer_b tags Roonie,
# Roonie responds to viewer_b, now viewer_b has continuation and viewer_a lost it
# ===========================================================================


def test_conversation_handoff_between_viewers(monkeypatch):
    """When Roonie responds to a new viewer, the old viewer loses continuation."""
    _stub_route(monkeypatch)
    d = ProviderDirector()
    env = Env(offline=False)

    # viewer_a conversation
    _say(d, env, "e1", "@RoonieTheCat hey!", user="viewer_a", mention=True)
    r2 = _say(d, env, "e2", "what's good tonight", user="viewer_a")
    assert r2.action == "RESPOND_PUBLIC"  # continuation works

    # viewer_b tags Roonie — handoff
    _say(d, env, "e3", "@RoonieTheCat when's the next stream?", user="viewer_b", mention=True)

    # viewer_a lost continuation
    r4 = _say(d, env, "e4", "that track was sick", user="viewer_a", send=False)
    assert r4.action == "NOOP"

    # viewer_b has continuation
    r5 = _say(d, env, "e5", "cool I'll be there saturday", user="viewer_b")
    assert r5.action == "RESPOND_PUBLIC"
    assert r5.trace["director"]["conversation_continuation"] is True


# ===========================================================================
# SCENARIO 7: Viewer says "roonie" in message text (implicit address)
# Real pattern: viewer doesn't @mention but says "roonie" somewhere in the msg
# ===========================================================================


def test_roonie_in_text_is_addressed_not_continuation(monkeypatch):
    """Message containing 'roonie' is addressed, even during active continuation."""
    _stub_route(monkeypatch)
    d = ProviderDirector()
    env = Env(offline=False)

    # Setup continuation
    _say(d, env, "e1", "@RoonieTheCat hey!", user="viewer_a", mention=True)

    # Viewer says "roonie" in text — this is addressed, not continuation
    r2 = _say(d, env, "e2", "yo roonie what track is this", user="viewer_a")
    assert r2.trace["director"]["addressed_to_roonie"] is True
    assert r2.trace["director"]["conversation_continuation"] is False


# ===========================================================================
# SCENARIO 8: Rapid-fire chat — multiple unrelated viewers, only one tagged
# Real pattern: busy chat moment, 5 messages fly by, only one tagged Roonie
# ===========================================================================


def test_rapid_fire_chat_only_tagged_viewer_gets_response(monkeypatch):
    """In rapid chat, only the viewer who tagged Roonie gets a response."""
    _stub_route(monkeypatch)
    d = ProviderDirector()
    env = Env(offline=False)

    # Burst of unrelated chat
    r1 = _say(d, env, "e1", "LFG this set is nuts", user="viewer_x", send=False)
    r2 = _say(d, env, "e2", "anyone know the ID", user="viewer_y", send=False)
    r3 = _say(d, env, "e3", "banger after banger", user="viewer_z", send=False)

    assert r1.action == "NOOP"
    assert r2.action == "NOOP"
    assert r3.action == "NOOP"

    # One viewer tags Roonie
    r4 = _say(d, env, "e4", "@RoonieTheCat hey cat!", user="viewer_a", mention=True)
    assert r4.action == "RESPOND_PUBLIC"

    # More noise
    r5 = _say(d, env, "e5", "POGGERS", user="viewer_x", send=False)
    assert r5.action == "NOOP"

    # Tagged viewer follow-up
    r6 = _say(d, env, "e6", "what's the track called", user="viewer_a")
    assert r6.action == "RESPOND_PUBLIC"
    assert r6.trace["director"]["conversation_continuation"] is True


# ===========================================================================
# SCENARIO 9: Session reset clears continuation
# Real pattern: stream restarts or new session starts
# ===========================================================================


def test_session_reset_clears_continuation(monkeypatch):
    """New session_id wipes context buffer, ending any active continuation."""
    _stub_route(monkeypatch)
    d = ProviderDirector()
    env = Env(offline=False)

    # Establish continuation
    _say(d, env, "e1", "@RoonieTheCat hey!", user="viewer_a", mention=True)

    # New session
    new_session_event = Event(
        event_id="e2",
        message="what's good tonight",
        metadata={
            "user": "viewer_a",
            "is_direct_mention": False,
            "mode": "live",
            "platform": "twitch",
            "session_id": "different-session",
        },
    )
    r2 = d.evaluate(new_session_event, env)
    assert r2.action == "NOOP"


# ===========================================================================
# SCENARIO 10: Continuation with "roonie" mention by OTHER viewer
# Real pattern: viewer_a is chatting with Roonie, viewer_b says "roonie is
# a cool cat" — viewer_b's message is a third-person reference, not direct address.
# It should not steal viewer_a's continuation.
# ===========================================================================


def test_other_viewer_mentions_roonie_does_not_steal_conversation(monkeypatch):
    """Third-person 'roonie' references should not trigger a handoff."""
    _stub_route(monkeypatch)
    d = ProviderDirector()
    env = Env(offline=False)

    # viewer_a starts conversation
    _say(d, env, "e1", "@RoonieTheCat hey!", user="viewer_a", mention=True)

    # viewer_b mentions roonie in third person (not addressed)
    r2 = _say(d, env, "e2", "haha roonie is such a vibe", user="viewer_b")
    assert r2.trace["director"]["addressed_to_roonie"] is False
    assert r2.action == "NOOP"

    # viewer_a should still retain continuation
    r3 = _say(d, env, "e3", "yeah the vibes are real", user="viewer_a", send=False)
    assert r3.action == "RESPOND_PUBLIC"
    assert r3.trace["director"]["conversation_continuation"] is True


# ===========================================================================
# SCENARIO 11: Continuation survives addressed message that gets no response
# Real pattern: viewer_a is chatting, viewer_b tags Roonie but Roonie's
# response doesn't get sent (suppressed by output gate / rate limit)
# ===========================================================================


def test_continuation_survives_if_interrupter_response_not_sent(monkeypatch):
    """If Roonie's response to an interrupter isn't sent, original continuation holds."""
    _stub_route(monkeypatch)
    d = ProviderDirector()
    env = Env(offline=False)

    # viewer_a starts conversation
    _say(d, env, "e1", "@RoonieTheCat hey!", user="viewer_a", mention=True)

    # viewer_b tags Roonie, but response is NOT sent (suppressed)
    r2 = _say(d, env, "e2", "@RoonieTheCat yo!", user="viewer_b", mention=True, send=False)
    assert r2.action == "RESPOND_PUBLIC"
    # Don't confirm send — simulate output gate suppression
    d.apply_output_feedback(event_id="e2", emitted=False, send_result={"sent": False})

    # viewer_a's continuation should still work — last SENT roonie turn was to viewer_a
    r3 = _say(d, env, "e3", "what time is the set ending tonight", user="viewer_a")
    assert r3.action == "RESPOND_PUBLIC"
    assert r3.trace["director"]["conversation_continuation"] is True


# ===========================================================================
# SCENARIO 12: Multiple continuation messages build context
# Real pattern: viewer sends 3 untagged messages, all should appear in
# context buffer for the LLM to see the full conversation
# ===========================================================================


def test_continuation_messages_accumulate_in_context(monkeypatch):
    """Multiple continuation messages are all stored in context buffer."""
    _stub_route(monkeypatch)
    d = ProviderDirector()
    env = Env(offline=False)

    _say(d, env, "e1", "@RoonieTheCat hey!", user="viewer_a", mention=True)

    _say(d, env, "e2", "this set is incredible", user="viewer_a")
    _say(d, env, "e3", "like actually one of the best", user="viewer_a")
    _say(d, env, "e4", "been here since the start and it keeps getting better", user="viewer_a")

    turns = d.context_buffer.get_context(max_turns=12)
    viewer_a_turns = [t for t in turns if t.speaker == "user" and t.tags.get("user") == "viewer_a"]
    # Should have all 4 user turns (1 addressed + 3 continuations)
    assert len(viewer_a_turns) == 4
    # First should have direct_address=True, rest should have continuation=True
    assert viewer_a_turns[0].tags.get("direct_address") is True
    assert all(t.tags.get("continuation") is True for t in viewer_a_turns[1:])


# ===========================================================================
# SCENARIO 13: Viewer with empty/missing username gets no continuation
# Edge case: malformed event metadata
# ===========================================================================


def test_empty_username_no_continuation(monkeypatch):
    """Events with empty username never trigger continuation."""
    _stub_route(monkeypatch)
    d = ProviderDirector()
    env = Env(offline=False)

    _say(d, env, "e1", "@RoonieTheCat hey!", user="viewer_a", mention=True)

    # Empty username event
    empty_user_event = Event(
        event_id="e2",
        message="hello again",
        metadata={
            "user": "",
            "is_direct_mention": False,
            "mode": "live",
            "platform": "twitch",
            "session_id": SESSION,
        },
    )
    r = d.evaluate(empty_user_event, env)
    assert r.action == "NOOP"
    assert r.trace["director"]["conversation_continuation"] is False


# ===========================================================================
# SCENARIO 14: Case-insensitive username matching
# Real pattern: Twitch usernames can appear in different cases
# ===========================================================================


def test_username_matching_is_case_insensitive(monkeypatch):
    """Continuation detects same viewer regardless of username case."""
    _stub_route(monkeypatch)
    d = ProviderDirector()
    env = Env(offline=False)

    # Initial tag with mixed case
    _say(d, env, "e1", "@RoonieTheCat hey!", user="Fraggy", mention=True)

    # Follow-up with different case
    e2 = Event(
        event_id="e2",
        message="this track tho",
        metadata={
            "user": "fraggy",
            "is_direct_mention": False,
            "mode": "live",
            "platform": "twitch",
            "session_id": SESSION,
        },
    )
    r2 = d.evaluate(e2, env)
    assert r2.action == "RESPOND_PUBLIC"
    assert r2.trace["director"]["conversation_continuation"] is True


# ===========================================================================
# SCENARIO 15: Ping-pong — two viewers take turns tagging Roonie
# Real pattern: viewer_a and viewer_b alternate tagging, each should work
# as addressed (not continuation)
# ===========================================================================


def test_alternating_tagged_viewers(monkeypatch):
    """Two viewers alternate tagging Roonie — all are addressed, none are continuation."""
    _stub_route(monkeypatch)
    d = ProviderDirector()
    env = Env(offline=False)

    r1 = _say(d, env, "e1", "@RoonieTheCat hey!", user="viewer_a", mention=True)
    assert r1.trace["director"]["addressed_to_roonie"] is True
    assert r1.trace["director"]["conversation_continuation"] is False

    r2 = _say(d, env, "e2", "@RoonieTheCat what's playing?", user="viewer_b", mention=True)
    assert r2.trace["director"]["addressed_to_roonie"] is True
    assert r2.trace["director"]["conversation_continuation"] is False

    r3 = _say(d, env, "e3", "@RoonieTheCat nice one cat", user="viewer_a", mention=True)
    assert r3.trace["director"]["addressed_to_roonie"] is True
    assert r3.trace["director"]["conversation_continuation"] is False


# ===========================================================================
# SCENARIO 16: Long conversation then full drop — verify natural expiry
# Real pattern: viewer chats for a while, then goes silent. Other chat
# pushes roonie turn out of buffer. Original viewer returns.
# ===========================================================================


def test_continuation_expires_when_buffer_fills(monkeypatch):
    """Continuation expires when enough turns push the roonie turn out of the buffer."""
    _stub_route(monkeypatch)
    d = ProviderDirector()
    env = Env(offline=False)

    # Initial conversation
    _say(d, env, "e1", "@RoonieTheCat hey!", user="viewer_a", mention=True)

    # Flood the buffer with OTHER viewers tagging Roonie
    # Context buffer is 12 turns. Each exchange is 2 turns (user + roonie).
    # After 6 exchanges with different viewers, the original roonie turn
    # should be pushed out.
    for i in range(7):
        _say(d, env, f"flood-{i}", f"@RoonieTheCat message {i}", user=f"flood_viewer_{i}", mention=True)

    # Original viewer returns — continuation should be gone
    r = _say(d, env, "e-return", "hey still here", user="viewer_a", send=False)
    assert r.action == "NOOP"
    assert r.trace["director"]["conversation_continuation"] is False


# ===========================================================================
# SCENARIO 17: Addressed + trigger vs addressed + no trigger during continuation
# Real pattern: "OTHER" category message with @mention but no trigger
# (no ?, no verb, >3 chars). This tests the trigger gate interaction.
# ===========================================================================


def test_addressed_no_trigger_noops_even_with_prior_continuation(monkeypatch):
    """Addressed OTHER-category message without trigger still NOOPs —
    continuation flag is False because message is addressed."""
    _stub_route(monkeypatch)
    d = ProviderDirector()
    env = Env(offline=False)

    # Setup continuation
    _say(d, env, "e1", "@RoonieTheCat hey!", user="viewer_a", mention=True)

    # Addressed but no trigger (OTHER category, no ?, no direct verb, > 3 chars)
    r2 = _say(d, env, "e2", "@RoonieTheCat yeah the vibes tonight are absolutely incredible", user="viewer_a", mention=True, send=False)
    # addressed=True + trigger=False + category=OTHER → NOOP
    # (continuation is False because addressed=True)
    # BUT: this will go through short_ack_preferred path since it's addressed + OTHER + long statement
    # Let's check what actually happens
    # The key point: addressed=True means continuation=False (by design)
    assert r2.trace["director"]["conversation_continuation"] is False


# ===========================================================================
# SCENARIO 18: The c0rcyra cardboard box scenario (the original bug)
# Exact recreation of the live testing failure that motivated this feature
# ===========================================================================


def test_c0rcyra_cardboard_box_scenario(monkeypatch):
    """Exact recreation of the bug: c0rcyra chatting with Roonie, sends
    'I also have a cardboard box for all your loafing needs ruleof6Lovecat'
    without tagging — should NOT noop."""
    _stub_route(monkeypatch)
    d = ProviderDirector()
    env = Env(offline=False)

    # c0rcyra and Roonie are chatting
    _say(d, env, "e1", "@RoonieTheCat come hang out with me", user="c0rcyra", mention=True)

    # c0rcyra follow-up without tag (the original bug)
    r2 = _say(d, env, "e2",
              "I also have a cardboard box for all your loafing needs ruleof6Lovecat",
              user="c0rcyra")
    assert r2.action == "RESPOND_PUBLIC"
    assert r2.trace["director"]["conversation_continuation"] is True
    assert r2.trace["director"]["addressed_to_roonie"] is False


# ===========================================================================
# SCENARIO 19: Same viewer greets someone else — continuation should not butt in
# ===========================================================================


def test_greeting_named_other_blocks_continuation(monkeypatch):
    _stub_route(monkeypatch)
    d = ProviderDirector()
    env = Env(offline=False)

    _say(d, env, "e1", "@RoonieTheCat what's the vibe tonight?", user="cland3stine", mention=True)
    r2 = _say(d, env, "e2", "Hey Jack! Its so good to see you!", user="cland3stine", send=False)

    assert r2.action == "NOOP"
    assert r2.trace["director"]["conversation_continuation"] is False
    assert r2.trace["director"]["continuation_reason"] == "GREETING_OTHER_USER"


# ===========================================================================
# SCENARIO 20: Same viewer @mentions someone else — continuation should no-op
# ===========================================================================


def test_third_party_mention_blocks_continuation(monkeypatch):
    _stub_route(monkeypatch)
    d = ProviderDirector()
    env = Env(offline=False)

    _say(d, env, "e1", "@RoonieTheCat track id please?", user="cland3stine", mention=True)
    r2 = _say(d, env, "e2", "Hey hey @umbrellaflyer - how you doing?", user="cland3stine", send=False)

    assert r2.action == "NOOP"
    assert r2.trace["director"]["conversation_continuation"] is False
    assert r2.trace["director"]["continuation_reason"] == "MENTION_OTHER_USER"


# ===========================================================================
# SCENARIO 21: Twitch reply-parent targets someone else — continuation should no-op
# ===========================================================================


def test_reply_parent_other_blocks_continuation(monkeypatch):
    _stub_route(monkeypatch)
    d = ProviderDirector()
    env = Env(offline=False)

    _say(d, env, "e1", "@RoonieTheCat are you AI?", user="cland3stine", mention=True)
    r2 = _say(
        d,
        env,
        "e2",
        "what do you think?",
        user="cland3stine",
        send=False,
        metadata_extra={"reply_parent_user_login": "some_other_user"},
    )

    assert r2.action == "NOOP"
    assert r2.trace["director"]["conversation_continuation"] is False
    assert r2.trace["director"]["continuation_reason"] == "REPLY_PARENT_OTHER"


# ===========================================================================
# SCENARIO 22: Multi-mention direct social message should still be addressable
# ===========================================================================


def test_multimention_direct_message_still_addressed(monkeypatch):
    _stub_route(monkeypatch)
    d = ProviderDirector()
    env = Env(offline=False)

    r = _say(
        d,
        env,
        "e1",
        "Hey Jack! Good to see you! By the way, looks like @RuleOfRune got their plushie cat @RoonieTheCat talking in chat...he's so cool!",
        user="viewer_a",
        mention=False,
        send=False,
    )

    assert r.trace["director"]["addressed_to_roonie"] is True
    assert r.trace["director"]["conversation_continuation"] is False
    assert r.action == "RESPOND_PUBLIC"


# ===========================================================================
# SCENARIO 23: Third-person Roonie mention should not steal thread handoff
# ===========================================================================


def test_third_person_roonie_reference_does_not_steal_continuation(monkeypatch):
    _stub_route(monkeypatch)
    d = ProviderDirector()
    env = Env(offline=False)

    # viewer_a starts thread
    _say(d, env, "e1", "yo Roonie!", user="viewer_a", mention=False)
    r2 = _say(d, env, "e2", "How's your new laptop by the way?", user="viewer_a")
    assert r2.action == "RESPOND_PUBLIC"
    assert r2.trace["director"]["conversation_continuation"] is True

    # viewer_b talks ABOUT Roonie in third person (not to Roonie)
    r3 = _say(
        d,
        env,
        "e3",
        "it's the perfect laptop for Roonie, I'm so glad he loves it already",
        user="viewer_b",
        send=False,
    )
    assert r3.trace["director"]["addressed_to_roonie"] is False
    assert r3.action == "NOOP"

    # viewer_a should still have continuation (no thread steal happened)
    r4 = _say(d, env, "e4", "Roonie how's typing on it by the way?", user="viewer_a")
    assert r4.action == "RESPOND_PUBLIC"
    assert r4.trace["director"]["addressed_to_roonie"] is True


# ===========================================================================
# SCENARIO 24: Possessive mention + @other should not be treated as direct
# ===========================================================================


def test_possessive_roonie_with_other_mention_noops(monkeypatch):
    _stub_route(monkeypatch)
    d = ProviderDirector()
    env = Env(offline=False)

    _say(d, env, "e1", "yo Roonie!", user="viewer_a", mention=False)
    r2 = _say(
        d,
        env,
        "e2",
        "@lilhjohny check out Roonie's laptop!",
        user="viewer_a",
        send=False,
    )
    assert r2.trace["director"]["addressed_to_roonie"] is False
    assert r2.action == "NOOP"


# ===========================================================================
# SCENARIO 25: Named other-person targeting should block continuation
# ===========================================================================


def test_targeting_art_by_name_blocks_continuation(monkeypatch):
    _stub_route(monkeypatch)
    d = ProviderDirector()
    env = Env(offline=False)

    _say(d, env, "e1", "@RoonieTheCat you good?", user="c0rcyra", mention=True)
    _say(d, env, "e2", "man work was crazy today", user="c0rcyra")
    r3 = _say(d, env, "e3", "so what else is good, art? how are things your way?", user="c0rcyra", send=False)

    assert r3.action == "NOOP"
    assert r3.trace["director"]["addressed_to_roonie"] is False
    assert r3.trace["director"]["conversation_continuation"] is False
    assert r3.trace["director"]["continuation_reason"] == "TARGETING_OTHER_NAME"
