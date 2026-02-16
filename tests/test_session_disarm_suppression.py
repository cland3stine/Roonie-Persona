from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from live_shim.record_run import run_payload
from roonie.dashboard_api.storage import DashboardStorage
from roonie.types import DecisionRecord, Env, Event


def _set_dashboard_paths(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ROONIE_DASHBOARD_LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("ROONIE_DASHBOARD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ROONIE_DASHBOARD_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(tmp_path / "data" / "providers_config.json"))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(tmp_path / "data" / "routing_config.json"))


def test_disarmed_suppresses_even_with_non_null_session_id_and_provider_proposal(tmp_path, monkeypatch) -> None:
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_KILL_SWITCH", "0")

    storage = DashboardStorage(runs_dir=tmp_path / "runs")
    armed_state = storage.set_armed(True)
    assert armed_state["armed"] is True
    session_id = str(armed_state.get("session_id", "")).strip()
    assert session_id

    disarmed_state = storage.set_armed(False)
    assert disarmed_state["armed"] is False
    assert str(disarmed_state.get("session_id", "")).strip() == session_id
    assert "DISARMED" in storage.get_status().to_dict().get("blocked_by", [])

    def _provider_director_stub(self, event: Event, env: Env) -> DecisionRecord:
        return DecisionRecord(
            case_id="live",
            event_id=event.event_id,
            action="RESPOND_PUBLIC",
            route="primary:openai",
            response_text="stub response",
            trace={
                "director": {"type": "ProviderDirector"},
                "proposal": {
                    "text": "stub response",
                    "provider_used": "openai",
                    "route_used": "primary:openai",
                    "moderation_status": "allow",
                    "session_id": session_id,
                    "token_usage_if_available": None,
                },
            },
            context_active=False,
            context_turns_used=0,
        )

    sent_calls: list[dict] = []

    def _spy_handle_output(self, output: Dict[str, Any], metadata: Dict[str, Any]) -> None:
        sent_calls.append({"output": dict(output), "metadata": dict(metadata)})

    monkeypatch.setattr("roonie.provider_director.ProviderDirector.evaluate", _provider_director_stub)
    monkeypatch.setattr("adapters.twitch_output.TwitchOutputAdapter.handle_output", _spy_handle_output)

    payload = {
        "session_id": session_id,
        "active_director": "ProviderDirector",
        "inputs": [
            {
                "event_id": "evt-1",
                "message": "@RoonieTheCat hey",
                "metadata": {
                    "user": "ruleofrune",
                    "is_direct_mention": True,
                    "mode": "live",
                    "platform": "twitch",
                },
            }
        ],
    }
    out_path = run_payload(payload, emit_outputs=True)
    run_doc = json.loads(out_path.read_text(encoding="utf-8"))
    output = run_doc["outputs"][0]

    assert run_doc["session_id"] == session_id
    assert output["emitted"] is False
    assert output["reason"] == "OUTPUT_DISABLED"
    assert str(output.get("session_id", "")).strip() == session_id
    assert sent_calls == []
    assert "DISARMED" in storage.get_status().to_dict().get("blocked_by", [])

