from __future__ import annotations

import json
from pathlib import Path

from src.providers.registry import ProviderRegistry
from src.roonie.context.context_buffer import ContextBuffer
from src.roonie.live_director import LiveDirector
from src.roonie.types import Env, Event


def _load_fixture(name: str) -> dict:
    p = Path("tests/fixtures/v1_12a_context") / name
    return json.loads(p.read_text(encoding="utf-8"))


def test_context_buffer_never_exceeds_n() -> None:
    buf = ContextBuffer(max_turns=3)
    for i in range(5):
        stored = buf.add_turn(
            speaker="user",
            text=f"what is track {i}?",
            tags={"direct_address": False, "category": "utility_track_id"},
        )
        assert stored is True

    turns = buf.get_context(max_turns=3)
    assert len(turns) == 3
    assert turns[0].text == "what is track 4?"
    assert turns[2].text == "what is track 2?"


def test_irrelevant_chatter_is_not_stored() -> None:
    buf = ContextBuffer(max_turns=3)
    stored = buf.add_turn(
        speaker="user",
        text="lol nice beat",
        tags={"direct_address": False, "category": "banter"},
    )
    assert stored is False
    assert buf.get_context() == []


def test_roonie_turn_requires_sent_and_related() -> None:
    buf = ContextBuffer(max_turns=3)
    assert buf.add_turn(speaker="user", text="can you help?", tags={"direct_address": True}) is True
    assert buf.add_turn(speaker="roonie", text="sure", sent=False, related_to_stored_user=True) is False
    assert buf.add_turn(speaker="roonie", text="sure", sent=True, related_to_stored_user=False) is False
    assert buf.add_turn(speaker="roonie", text="sure", sent=True, related_to_stored_user=True) is True


def test_fixture_job_then_construction_uses_recent_context() -> None:
    fx = _load_fixture("case_job_then_construction.json")
    reg = ProviderRegistry.from_dict(fx["provider_cfg"])
    director = LiveDirector(registry=reg, routing_cfg=fx["routing_cfg"])
    env = Env(offline=False)

    decisions = []
    for e in fx["events"]:
        event = Event(
            event_id=e["event_id"],
            message=e["message"],
            actor=e.get("actor", "viewer"),
            metadata=e.get("metadata", {}),
        )
        decisions.append(director.evaluate(event, env))

    assert decisions[0].context_active is False
    assert decisions[0].context_turns_used == 0
    assert decisions[1].context_active is True
    assert decisions[1].context_turns_used >= 1

    second = (decisions[1].response_text or "").lower()
    # Provider output can be empty in CI when no live credentials are configured.
    if second:
        assert "construction in seattle" in second
        assert "where can i find a job" in second
