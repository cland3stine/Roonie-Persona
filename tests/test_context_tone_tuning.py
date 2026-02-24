from __future__ import annotations

from typing import Any, Dict

from roonie.behavior_spec import CATEGORY_BANTER, CATEGORY_GREETING, classify_behavior_category
from roonie.provider_director import ProviderDirector
from roonie.types import Env, Event


def _live_event(event_id: str, message: str) -> Event:
    return Event(
        event_id=event_id,
        message=message,
        metadata={
            "user": "cland3stine",
            "is_direct_mention": True,
            "mode": "live",
            "platform": "twitch",
            "session_id": "ctx-tone-session",
        },
    )


def test_greeting_with_followup_question_routes_to_banter() -> None:
    cat = classify_behavior_category(
        message="@RoonieTheCat hey buddy how are you?",
        metadata={},
    )
    assert cat == CATEGORY_BANTER


def test_pure_greeting_stays_in_greeting_bucket() -> None:
    cat = classify_behavior_category(
        message="@RoonieTheCat hey there!",
        metadata={},
    )
    assert cat == CATEGORY_GREETING


def test_provider_director_injects_topic_anchor_for_continuity(monkeypatch) -> None:
    captured: Dict[str, Any] = {}

    def _stub_route_generate(**kwargs):
        captured["prompt"] = kwargs.get("prompt")
        kwargs["context"]["provider_selected"] = "openai"
        kwargs["context"]["moderation_result"] = "allow"
        return "ok"

    monkeypatch.setattr("roonie.provider_director.route_generate", _stub_route_generate)

    director = ProviderDirector()
    env = Env(offline=False)

    messages = [
        "@RoonieTheCat have you heard the latest Maze 28 release?",
        "@RoonieTheCat what label was it on?",
        "@RoonieTheCat when did it drop?",
        "@RoonieTheCat which track was that one?",
        "@RoonieTheCat it was Maze... something...",
    ]

    for idx, msg in enumerate(messages, start=1):
        director.evaluate(_live_event(f"evt-{idx}", msg), env)

    prompt = str(captured.get("prompt") or "")
    assert "Recent topic: Maze 28" in prompt


def test_topic_anchor_does_not_bleed_into_unrelated_banter(monkeypatch) -> None:
    captured: Dict[str, Any] = {}

    def _stub_route_generate(**kwargs):
        captured["prompt"] = kwargs.get("prompt")
        kwargs["context"]["provider_selected"] = "openai"
        kwargs["context"]["moderation_result"] = "allow"
        return "ok"

    monkeypatch.setattr("roonie.provider_director.route_generate", _stub_route_generate)

    director = ProviderDirector()
    env = Env(offline=False)

    director.evaluate(
        _live_event("evt-1", "@RoonieTheCat have you heard the latest Maze 28 release?"),
        env,
    )
    director.evaluate(
        _live_event("evt-2", "@RoonieTheCat oh same old shit... working..."),
        env,
    )

    prompt = str(captured.get("prompt") or "")
    assert "Recent topic: Maze 28" not in prompt
    assert "Library grounding (local)" not in prompt


def test_topic_anchor_can_apply_to_general_topics_on_deictic_followup(monkeypatch) -> None:
    captured: Dict[str, Any] = {}

    def _stub_route_generate(**kwargs):
        captured["prompt"] = kwargs.get("prompt")
        kwargs["context"]["provider_selected"] = "openai"
        kwargs["context"]["moderation_result"] = "allow"
        return "ok"

    monkeypatch.setattr("roonie.provider_director.route_generate", _stub_route_generate)

    director = ProviderDirector()
    env = Env(offline=False)

    director.evaluate(
        _live_event("evt-1", "@RoonieTheCat have you seen Maze runner lately?"),
        env,
    )
    director.evaluate(
        _live_event("evt-2", "@RoonieTheCat when did it come out?"),
        env,
    )

    prompt = str(captured.get("prompt") or "")
    assert "Recent topic:" in prompt
    assert "Maze" in prompt
    assert "Library grounding (local)" not in prompt


def test_provider_director_stores_assistant_turn_after_send_feedback(monkeypatch) -> None:
    captured: Dict[str, Any] = {"prompts": []}
    replies = ["maze is still in heavy rotation", "same, that one stays on repeat"]

    def _stub_route_generate(**kwargs):
        captured["prompts"].append(kwargs.get("prompt"))
        kwargs["context"]["provider_selected"] = "openai"
        kwargs["context"]["moderation_result"] = "allow"
        return replies.pop(0)

    monkeypatch.setattr("roonie.provider_director.route_generate", _stub_route_generate)

    director = ProviderDirector()
    env = Env(offline=False)

    first = director.evaluate(_live_event("evt-1", "@RoonieTheCat maze 28 still slaps"), env)
    assert first.action == "RESPOND_PUBLIC"

    before_feedback = director.context_buffer.get_context(max_turns=12)
    assert not any(str(turn.speaker).strip().lower() == "roonie" for turn in before_feedback)

    director.apply_output_feedback(
        event_id="evt-1",
        emitted=True,
        send_result={"sent": True, "reason": "OK"},
    )

    after_feedback = director.context_buffer.get_context(max_turns=12)
    assert any(str(turn.speaker).strip().lower() == "roonie" for turn in after_feedback)

    director.evaluate(_live_event("evt-2", "@RoonieTheCat what was that one again?"), env)
    prompt = str(captured["prompts"][-1] or "")
    assert "Roonie: maze is still in heavy rotation" in prompt


def test_provider_director_does_not_store_assistant_turn_when_not_sent(monkeypatch) -> None:
    captured: Dict[str, Any] = {"prompts": []}
    replies = ["this one gets wild", "you caught the mood right away"]

    def _stub_route_generate(**kwargs):
        captured["prompts"].append(kwargs.get("prompt"))
        kwargs["context"]["provider_selected"] = "openai"
        kwargs["context"]["moderation_result"] = "allow"
        return replies.pop(0)

    monkeypatch.setattr("roonie.provider_director.route_generate", _stub_route_generate)

    director = ProviderDirector()
    env = Env(offline=False)

    first = director.evaluate(_live_event("evt-1", "@RoonieTheCat this part is nuts"), env)
    assert first.action == "RESPOND_PUBLIC"

    director.apply_output_feedback(
        event_id="evt-1",
        emitted=False,
        send_result={"sent": False, "reason": "RATE_LIMIT"},
    )

    after_feedback = director.context_buffer.get_context(max_turns=12)
    assert not any(str(turn.speaker).strip().lower() == "roonie" for turn in after_feedback)

    director.evaluate(_live_event("evt-2", "@RoonieTheCat and what about this drop?"), env)
    prompt = str(captured["prompts"][-1] or "")
    assert "Roonie: this one gets wild" not in prompt
