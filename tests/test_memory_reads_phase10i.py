from __future__ import annotations

import json
from pathlib import Path


def _fx(name: str) -> dict:
    p = Path("tests/fixtures/v1_10i_memory_reads") / name
    return json.loads(p.read_text(encoding="utf-8"))


def test_no_explicit_context_does_not_use_memory():
    from memory.read_policy import apply_memory_read_policy

    store = _fx("memory_store.json")
    case = _fx("case_no_explicit_context.json")

    out = apply_memory_read_policy(
        store=store,
        viewer_key=case["viewer_key"],
        explicit_context=case["explicit_context"],
        requested_slots=case["requested_slots"],
    )
    assert out == case["expected"]


def test_explicit_likes_includes_likes_suppresses_dislikes():
    from memory.read_policy import apply_memory_read_policy

    store = _fx("memory_store.json")
    case = _fx("case_explicit_likes.json")

    out = apply_memory_read_policy(
        store=store,
        viewer_key=case["viewer_key"],
        explicit_context=case["explicit_context"],
        requested_slots=case["requested_slots"],
    )
    assert out == case["expected"]


def test_explicit_dislikes_are_still_suppressed():
    from memory.read_policy import apply_memory_read_policy

    store = _fx("memory_store.json")
    case = _fx("case_explicit_dislikes_suppressed.json")

    out = apply_memory_read_policy(
        store=store,
        viewer_key=case["viewer_key"],
        explicit_context=case["explicit_context"],
        requested_slots=case["requested_slots"],
    )
    assert out == case["expected"]


def test_explicit_fact_included():
    from memory.read_policy import apply_memory_read_policy

    store = _fx("memory_store.json")
    case = _fx("case_explicit_fact.json")

    out = apply_memory_read_policy(
        store=store,
        viewer_key=case["viewer_key"],
        explicit_context=case["explicit_context"],
        requested_slots=case["requested_slots"],
    )
    assert out == case["expected"]
