from __future__ import annotations

from pathlib import Path

from live_shim.record_run import run_payload
from roonie.offline_director import OfflineDirector
from roonie.types import Env, Event


def test_run_payload_writes_to_dashboard_runs_dir_env(tmp_path, monkeypatch) -> None:
    runs_dir = tmp_path / "runtime-runs"
    monkeypatch.setenv("ROONIE_DASHBOARD_RUNS_DIR", str(runs_dir))

    payload = {
        "session_id": "live-shim-phase15",
        "inputs": [
            {
                "event_id": "evt-1",
                "message": "@RoonieTheCat hello",
                "metadata": {"user": "ruleofrune", "is_direct_mention": True},
            }
        ],
    }

    out_path = run_payload(payload, emit_outputs=False)
    assert out_path == runs_dir / "live-shim-phase15.json"
    assert out_path.exists()


def test_live_direct_greeting_routes_to_ack() -> None:
    director = OfflineDirector()
    env = Env(offline=False)
    event = Event(
        event_id="evt-greet-1",
        message="@RoonieTheCat hey there!",
        metadata={"is_direct_mention": True, "mode": "live", "platform": "twitch"},
    )
    decision = director.evaluate(event, env)
    assert decision.action == "RESPOND_PUBLIC"
    assert decision.route == "responder:neutral_ack"
    assert decision.response_text == "Hey there! Good to see you."
