from __future__ import annotations

from typing import Any

from roonie.provider_director import ProviderDirector
from roonie.types import Env, Event


def _event(event_id: str, message: str, **metadata: Any) -> Event:
    base = {
        "user": "cland3stine",
        "mode": "live",
        "platform": "twitch",
        "session_id": "specificity-session",
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


def test_specificity_gate_logs_shadow_reject_for_generic_greeting_ceremony(monkeypatch) -> None:
    monkeypatch.setenv("ROONIE_SPECIFICITY_GATE_MODE", "shadow")
    monkeypatch.setattr(
        "roonie.provider_director.route_generate",
        _stub_route_generate_factory("@cland3stine welcome in. glad you're here"),
    )

    director = ProviderDirector()
    record = director.evaluate(
        _event("evt-shadow", "@RoonieTheCat hey there", is_direct_mention=True),
        Env(offline=False),
    )

    assert record.action == "RESPOND_PUBLIC"
    assert record.response_text == "@cland3stine welcome in. glad you're here"
    assert record.trace["specificity_would_reject"] is True
    assert record.trace["specificity"]["mode"] == "shadow"
    assert "welcome_in" in record.trace["specificity"]["generic_hits"]
    assert record.trace["specificity"]["suppressed"] is False


def test_specificity_gate_active_mode_suppresses_generic_greeting_ceremony(monkeypatch) -> None:
    monkeypatch.setenv("ROONIE_SPECIFICITY_GATE_MODE", "active")
    monkeypatch.setattr(
        "roonie.provider_director.route_generate",
        _stub_route_generate_factory("@cland3stine welcome in. glad you're here"),
    )

    director = ProviderDirector()
    record = director.evaluate(
        _event("evt-active", "@RoonieTheCat hey there", is_direct_mention=True),
        Env(offline=False),
    )

    assert record.action == "NOOP"
    assert record.route == "suppressed:specificity"
    assert record.response_text is None
    assert record.trace["specificity_would_reject"] is True
    assert record.trace["specificity"]["suppressed"] is True
    assert record.trace["suppression_reason"] == "SPECIFICITY_GATE"



def test_specificity_gate_logs_shadow_reject_for_good_to_see_you_greeting(monkeypatch) -> None:
    monkeypatch.setenv("ROONIE_SPECIFICITY_GATE_MODE", "shadow")
    monkeypatch.setattr(
        "roonie.provider_director.route_generate",
        _stub_route_generate_factory("@cland3stine hey, good to see you."),
    )

    director = ProviderDirector()
    record = director.evaluate(
        _event("evt-shadow-good-see", "@RoonieTheCat hey there", is_direct_mention=True),
        Env(offline=False),
    )

    assert record.action == "RESPOND_PUBLIC"
    assert record.response_text == "@cland3stine hey, good to see you."
    assert record.trace["specificity_would_reject"] is True
    assert "good_to_see_you" in record.trace["specificity"]["generic_hits"]
    assert record.trace["specificity"]["suppressed"] is False



def test_specificity_gate_active_mode_suppresses_generic_cheer_without_specific_anchor(monkeypatch) -> None:
    monkeypatch.setenv("ROONIE_SPECIFICITY_GATE_MODE", "active")
    monkeypatch.setattr(
        "roonie.provider_director.route_generate",
        _stub_route_generate_factory("@cat appreciate you for the bits."),
    )

    director = ProviderDirector()
    record = director.evaluate(
        _event(
            "evt-cheer-generic",
            "@RoonieTheCat heads up: cat cheered 250 bits.",
            user="cat",
            category="EVENT_CHEER",
        ),
        Env(offline=False),
    )

    assert record.action == "NOOP"
    assert record.route == "suppressed:specificity"
    assert record.response_text is None
    assert record.trace["specificity_would_reject"] is True
    assert "appreciate_bits" in record.trace["specificity"]["generic_hits"]
    assert record.trace["specificity"]["anchor_hits"] == []


def test_specificity_gate_active_mode_allows_anchored_cheer_response(monkeypatch) -> None:
    monkeypatch.setenv("ROONIE_SPECIFICITY_GATE_MODE", "active")
    monkeypatch.setattr(
        "roonie.provider_director.route_generate",
        _stub_route_generate_factory("@cat 250 bits? that landed right on the drop."),
    )

    director = ProviderDirector()
    record = director.evaluate(
        _event(
            "evt-cheer-anchored",
            "@RoonieTheCat heads up: cat cheered 250 bits.",
            user="cat",
            category="EVENT_CHEER",
        ),
        Env(offline=False),
    )

    assert record.action == "RESPOND_PUBLIC"
    assert record.trace["specificity_would_reject"] is False
    assert "event_detail" in record.trace["specificity"]["anchor_hits"]
def test_specificity_gate_allows_anchored_banter_response_in_active_mode(monkeypatch) -> None:
    monkeypatch.setenv("ROONIE_SPECIFICITY_GATE_MODE", "active")
    monkeypatch.setattr(
        "roonie.provider_director.route_generate",
        _stub_route_generate_factory(
            "@cland3stine no top hat, just the booth running hot tonight. paws are keeping up."
        ),
    )

    director = ProviderDirector()
    record = director.evaluate(
        _event("evt-anchored", "@RoonieTheCat top hat tonight", is_direct_mention=True),
        Env(offline=False),
    )

    assert record.action == "RESPOND_PUBLIC"
    assert record.trace["specificity_would_reject"] is False
    assert any(hit.startswith("viewer_callback:") or hit == "booth_image" for hit in record.trace["specificity"]["anchor_hits"])


def test_specificity_gate_exempts_direct_answer_banter(monkeypatch) -> None:
    monkeypatch.setenv("ROONIE_SPECIFICITY_GATE_MODE", "active")
    monkeypatch.setattr("roonie.provider_director._specificity_generic_hits", lambda candidate: ["forced_generic"])
    monkeypatch.setattr("roonie.provider_director._specificity_anchor_hits", lambda **kwargs: [])
    monkeypatch.setattr(
        "roonie.provider_director.route_generate",
        _stub_route_generate_factory("@cland3stine all good up here on the booth"),
    )

    director = ProviderDirector()
    record = director.evaluate(
        _event("evt-banter", "@RoonieTheCat how are you?", is_direct_mention=True),
        Env(offline=False),
    )

    assert record.action == "RESPOND_PUBLIC"
    assert record.trace["specificity_would_reject"] is False
    assert record.trace["specificity"]["exempt_reason"] == "direct_answer_banter"


def test_specificity_gate_exempts_continuation(monkeypatch) -> None:
    monkeypatch.setenv("ROONIE_SPECIFICITY_GATE_MODE", "active")

    def _forced_generic(candidate: str) -> list[str]:
        if "lol fair" in candidate.lower():
            return ["forced_generic"]
        return []

    monkeypatch.setattr("roonie.provider_director._specificity_generic_hits", _forced_generic)
    monkeypatch.setattr("roonie.provider_director._specificity_anchor_hits", lambda **kwargs: [])
    monkeypatch.setattr(
        "roonie.provider_director.route_generate",
        _stub_route_generate_factory(
            "@cland3stine maze is still in heavy rotation",
            "@cland3stine lol fair",
        ),
    )

    director = ProviderDirector()
    env = Env(offline=False)

    first = director.evaluate(_event("evt-1", "@RoonieTheCat maze 28 still slaps", is_direct_mention=True), env)
    assert first.action == "RESPOND_PUBLIC"
    director.apply_output_feedback(
        event_id="evt-1",
        emitted=True,
        send_result={"sent": True, "reason": "OK"},
    )

    second = director.evaluate(_event("evt-2", "lol fair", is_direct_mention=False), env)

    assert second.action == "RESPOND_PUBLIC"
    assert second.response_text == "@cland3stine lol fair"
    assert second.trace["specificity_would_reject"] is False
    assert second.trace["specificity"]["exempt_reason"] == "continuation"


