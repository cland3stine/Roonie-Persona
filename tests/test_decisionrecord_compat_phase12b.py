from __future__ import annotations

import json
from pathlib import Path

from replay.replay_run import assert_decisions_equal
from roonie.types import DecisionRecord


def _load_legacy_decision() -> dict:
    fixture = Path("tests/fixtures/v1/golden/P1-0001_noop_bias_basic.expected.json")
    return json.loads(fixture.read_text(encoding="utf-8-sig"))[0]


def test_decision_record_deserializes_legacy_fixture_with_context_defaults() -> None:
    legacy = _load_legacy_decision()
    legacy["runtime_only"] = "ignored"

    record = DecisionRecord.from_dict(legacy)

    assert record.context_active is False
    assert record.context_turns_used == 0
    serialized = record.to_dict(exclude_defaults=True)
    assert "context_active" not in serialized
    assert "context_turns_used" not in serialized


def test_legacy_golden_compare_ignores_new_context_and_unknown_fields() -> None:
    expected = [_load_legacy_decision()]
    actual = [
        {
            **expected[0],
            "context_active": False,
            "context_turns_used": 0,
            "extra_runtime_field": {"debug": True},
        }
    ]

    # Should not raise for legacy expected fixtures that lack newer fields.
    assert_decisions_equal(expected, actual)
