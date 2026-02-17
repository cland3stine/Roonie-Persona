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
    assert "Conversation continuity hint:" in prompt
    assert "Active topic from recent chat: Maze 28" in prompt
    assert "Do not invent new artist or track names when uncertain" in prompt


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
    assert "Conversation continuity hint:" not in prompt
    assert "Active topic from recent chat: Maze 28" not in prompt
    assert "Active topic anchor: Maze 28" not in prompt
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
    assert "Conversation continuity hint:" in prompt
    assert "Active topic from recent chat:" in prompt
    assert "Maze" in prompt
    assert "Library grounding (local)" not in prompt
