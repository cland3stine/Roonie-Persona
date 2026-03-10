from __future__ import annotations

from typing import Any

from roonie.provider_director import ProviderDirector
from roonie.types import Env, Event


def _event(event_id: str, message: str, **metadata: Any) -> Event:
    base = {
        "user": "viewer123",
        "is_direct_mention": True,
        "mode": "live",
        "platform": "twitch",
        "session_id": "stream-safety-phase22",
    }
    base.update(metadata)
    return Event(event_id=event_id, message=message, metadata=base)


def _stub_route_generate_factory(*responses: str):
    pending = list(responses)

    def _stub_route_generate(**kwargs):
        kwargs["context"]["provider_selected"] = "openai"
        kwargs["context"]["moderation_result"] = "allow"
        return pending.pop(0)

    return _stub_route_generate


def test_hidden_state_questions_use_deterministic_truthful_guardrail(monkeypatch) -> None:
    called = {"route_generate": False}

    def _should_not_run(**kwargs):
        called["route_generate"] = True
        raise AssertionError("route_generate should not be called for hidden-state questions")

    monkeypatch.setattr("roonie.provider_director.route_generate", _should_not_run)

    director = ProviderDirector()
    record = director.evaluate(
        _event("evt-lurkers", "@RoonieTheCat How many people are lurking?"),
        Env(offline=False),
    )

    assert called["route_generate"] is False
    assert record.action == "RESPOND_PUBLIC"
    assert record.route == "primary:guardrail"
    assert record.response_text == "@viewer123 hard to tell from the booth. chat's the part i can actually see."
    assert record.trace["routing"]["provider_selected"] == "guardrail"
    assert record.trace["routing"]["override_mode"] == "deterministic_guardrail"
    assert record.trace["guardrail_reason"] == "hidden_state_unknown"


def test_platform_help_questions_use_uncertainty_guardrail(monkeypatch) -> None:
    called = {"route_generate": False}

    def _should_not_run(**kwargs):
        called["route_generate"] = True
        raise AssertionError("route_generate should not be called for platform-help questions")

    monkeypatch.setattr("roonie.provider_director.route_generate", _should_not_run)

    director = ProviderDirector()
    record = director.evaluate(
        _event("evt-help", "@RoonieTheCat where is private mode on Twitch?"),
        Env(offline=False),
    )

    assert called["route_generate"] is False
    assert record.action == "RESPOND_PUBLIC"
    assert record.route == "primary:guardrail"
    assert record.response_text == "@viewer123 not sure on the exact Twitch setting for that."
    assert record.trace["guardrail_reason"] == "platform_help_unknown"


def test_platform_tech_problem_questions_use_uncertainty_guardrail(monkeypatch) -> None:
    called = {"route_generate": False}

    def _should_not_run(**kwargs):
        called["route_generate"] = True
        raise AssertionError("route_generate should not be called for platform-tech questions")

    monkeypatch.setattr("roonie.provider_director.route_generate", _should_not_run)

    director = ProviderDirector()
    record = director.evaluate(
        _event("evt-tech", "@RoonieTheCat are you streaming some things incompatible with older smart TVs? this thing keeps crashing"),
        Env(offline=False),
    )

    assert called["route_generate"] is False
    assert record.action == "RESPOND_PUBLIC"
    assert record.route == "primary:guardrail"
    assert record.response_text == "@viewer123 not sure what's causing that on Twitch's side. laptop might just be the safer bet for now."
    assert record.trace["guardrail_reason"] == "platform_tech_unknown"


def test_continuation_blocks_when_message_targets_inner_circle_alias(monkeypatch) -> None:
    monkeypatch.setattr(
        "roonie.provider_director.route_generate",
        _stub_route_generate_factory("@pwprice820 yeah that transition was ridiculous"),
    )

    director = ProviderDirector()
    env = Env(offline=False)

    first = director.evaluate(
        _event("evt-1", "@RoonieTheCat that transition was wild", user="pwprice820"),
        env,
    )
    assert first.action == "RESPOND_PUBLIC"
    director.apply_output_feedback(
        event_id="evt-1",
        emitted=True,
        send_result={"sent": True, "reason": "OK"},
    )

    second = director.evaluate(
        _event(
            "evt-2",
            "Such a sick transition Jen. Corcyra",
            user="pwprice820",
            is_direct_mention=False,
            inner_circle=[
                {
                    "username": "c0rcyra",
                    "display_name": "Jen",
                    "role": "co-streamer",
                    "note": "",
                }
            ],
        ),
        env,
    )

    assert second.action == "NOOP"
    assert second.trace["director"]["continuation_reason"] == "TARGETING_OTHER_NAME"


def test_continuation_blocks_when_message_targets_default_corcyra_alias_without_inner_circle(monkeypatch) -> None:
    monkeypatch.setattr(
        "roonie.provider_director.route_generate",
        _stub_route_generate_factory("@pwprice820 yeah that transition was ridiculous"),
    )

    director = ProviderDirector()
    env = Env(offline=False)

    first = director.evaluate(
        _event("evt-1b", "@RoonieTheCat that transition was wild", user="pwprice820"),
        env,
    )
    assert first.action == "RESPOND_PUBLIC"
    director.apply_output_feedback(
        event_id="evt-1b",
        emitted=True,
        send_result={"sent": True, "reason": "OK"},
    )

    second = director.evaluate(
        _event(
            "evt-2b",
            "Such a sick transition Jen. Corcyra",
            user="pwprice820",
            is_direct_mention=False,
        ),
        env,
    )

    assert second.action == "NOOP"
    assert second.trace["director"]["continuation_reason"] == "TARGETING_OTHER_NAME"


def test_specificity_tracks_means_a_lot_even_when_cheer_is_anchored(monkeypatch) -> None:
    monkeypatch.setenv("ROONIE_SPECIFICITY_GATE_MODE", "active")
    monkeypatch.setattr(
        "roonie.provider_director.route_generate",
        _stub_route_generate_factory("@darkorange73 100 bits on that drop. means a lot."),
    )

    director = ProviderDirector()
    record = director.evaluate(
        _event(
            "evt-cheer-means-a-lot",
            "@RoonieTheCat heads up: darkorange73 cheered 100 bits.",
            user="darkorange73",
            category="EVENT_CHEER",
        ),
        Env(offline=False),
    )

    assert record.action == "RESPOND_PUBLIC"
    assert record.trace["specificity_would_reject"] is False
    assert "means_a_lot" in record.trace["specificity"]["generic_hits"]
    assert "event_detail" in record.trace["specificity"]["anchor_hits"]
