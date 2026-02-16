from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

import pytest

from live_shim.record_run import run_payload


def _make_payload(*, active_director: str, message: str) -> Dict[str, Any]:
    return {
        "session_id": "dry-run-fixture",
        "active_director": active_director,
        "inputs": [
            {
                "event_id": "evt-1",
                "message": message,
                "metadata": {
                    "mode": "live",
                    "platform": "twitch",
                    "channel": "ruleofrune",
                    "user": "viewer",
                    "is_direct_mention": True,
                },
            }
        ],
    }


@pytest.fixture(autouse=True)
def _reset_output_gate_globals(monkeypatch):
    # output_gate uses module-level rate limit globals; reset for deterministic tests.
    import responders.output_gate as og

    og._LAST_EMIT_TS = 0.0
    og._LAST_EMIT_BY_KEY = {}
    monkeypatch.delenv("ROONIE_READ_ONLY_MODE", raising=False)
    monkeypatch.delenv("ROONIE_DRY_RUN", raising=False)
    monkeypatch.setenv("ROONIE_OUTPUT_DISABLED", "0")


def test_dry_run_suppresses_respond_public(monkeypatch, tmp_path: Path) -> None:
    # Force a decision that would otherwise emit.
    from roonie.types import DecisionRecord
    from roonie.offline_director import OfflineDirector

    def _fake_evaluate(self, event, env):  # type: ignore[no-untyped-def]
        return DecisionRecord(
            case_id="case",
            event_id=event.event_id,
            action="RESPOND_PUBLIC",
            route="offline:test",
            response_text="hello",
            trace={},
        )

    monkeypatch.setattr(OfflineDirector, "evaluate", _fake_evaluate, raising=True)

    # Ensure adapter isn't called if DRY_RUN works.
    from adapters.twitch_output import TwitchOutputAdapter

    def _boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("TwitchOutputAdapter.handle_output must not be called in DRY_RUN")

    monkeypatch.setattr(TwitchOutputAdapter, "handle_output", _boom, raising=True)

    monkeypatch.setenv("ROONIE_DRY_RUN", "1")
    out_path = run_payload(_make_payload(active_director="OfflineDirector", message="@Roonie hi"), emit_outputs=True)
    run_doc = json.loads(out_path.read_text(encoding="utf-8-sig"))
    assert run_doc["outputs"][0]["emitted"] is False
    assert run_doc["outputs"][0]["reason"] == "DRY_RUN"


def test_not_dry_run_allows_emit(monkeypatch, tmp_path: Path) -> None:
    from roonie.types import DecisionRecord
    from roonie.offline_director import OfflineDirector

    def _fake_evaluate(self, event, env):  # type: ignore[no-untyped-def]
        return DecisionRecord(
            case_id="case",
            event_id=event.event_id,
            action="RESPOND_PUBLIC",
            route="offline:test",
            response_text="hello",
            trace={},
        )

    monkeypatch.setattr(OfflineDirector, "evaluate", _fake_evaluate, raising=True)

    called = {"n": 0}
    from adapters.twitch_output import TwitchOutputAdapter

    def _count(*args, **kwargs):  # type: ignore[no-untyped-def]
        called["n"] += 1
        return None

    monkeypatch.setattr(TwitchOutputAdapter, "handle_output", _count, raising=True)

    monkeypatch.setenv("ROONIE_DRY_RUN", "0")
    out_path = run_payload(_make_payload(active_director="OfflineDirector", message="@Roonie hi"), emit_outputs=True)
    run_doc = json.loads(out_path.read_text(encoding="utf-8-sig"))
    assert run_doc["outputs"][0]["emitted"] is True
    assert called["n"] == 1
