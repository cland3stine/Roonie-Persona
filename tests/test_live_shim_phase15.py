from __future__ import annotations

import json
from pathlib import Path

from live_shim.record_run import run_payload
from roonie.offline_director import OfflineDirector
from roonie.provider_director import ProviderDirector
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


def test_live_payload_defaults_to_provider_director(tmp_path, monkeypatch) -> None:
    runs_dir = tmp_path / "runtime-runs"
    monkeypatch.setenv("ROONIE_DASHBOARD_RUNS_DIR", str(runs_dir))
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(tmp_path / "providers_config.json"))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(tmp_path / "routing_config.json"))
    monkeypatch.setenv("ROONIE_OUTPUT_DISABLED", "0")

    payload = {
        "session_id": "live-provider-default",
        "inputs": [
            {
                "event_id": "evt-1",
                "message": "@RoonieTheCat how are you?",
                "metadata": {
                    "user": "ruleofrune",
                    "is_direct_mention": True,
                    "mode": "live",
                    "platform": "twitch",
                },
            }
        ],
    }
    out_path = run_payload(payload, emit_outputs=False)
    run_doc = json.loads(out_path.read_text(encoding="utf-8"))
    assert run_doc["active_director"] == "ProviderDirector"
    decision = run_doc["decisions"][0]
    assert decision["trace"]["director"]["type"] == "ProviderDirector"
    assert decision["trace"]["proposal"]["provider_used"] == "openai"
    assert decision["action"] == "RESPOND_PUBLIC"
    assert str(decision["response_text"]).startswith("[openai stub]")


def test_live_payload_reuses_director_instance_for_context_carry_forward(tmp_path, monkeypatch) -> None:
    runs_dir = tmp_path / "runtime-runs"
    monkeypatch.setenv("ROONIE_DASHBOARD_RUNS_DIR", str(runs_dir))
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(tmp_path / "providers_config.json"))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(tmp_path / "routing_config.json"))
    monkeypatch.setenv("ROONIE_OUTPUT_DISABLED", "0")
    monkeypatch.setattr("roonie.provider_director.route_generate", lambda **kwargs: "all good")

    director = ProviderDirector()
    env = Env(offline=False)

    first_path = run_payload(
        {
            "session_id": "live-context-carry",
            "active_director": "ProviderDirector",
            "inputs": [
                {
                    "event_id": "evt-1",
                    "message": "@RoonieTheCat hey there",
                    "metadata": {
                        "user": "ruleofrune",
                        "is_direct_mention": True,
                        "mode": "live",
                        "platform": "twitch",
                    },
                }
            ],
        },
        emit_outputs=False,
        director_instance=director,
        env_instance=env,
    )
    first_doc = json.loads(first_path.read_text(encoding="utf-8"))
    first_decision = first_doc["decisions"][0]
    assert bool(first_decision.get("context_active", False)) is False
    assert int(first_decision.get("context_turns_used", 0)) == 0

    second_path = run_payload(
        {
            "session_id": "live-context-carry",
            "active_director": "ProviderDirector",
            "inputs": [
                {
                    "event_id": "evt-2",
                    "message": "@RoonieTheCat how are you?",
                    "metadata": {
                        "user": "ruleofrune",
                        "is_direct_mention": True,
                        "mode": "live",
                        "platform": "twitch",
                    },
                }
            ],
        },
        emit_outputs=False,
        director_instance=director,
        env_instance=env,
    )
    second_doc = json.loads(second_path.read_text(encoding="utf-8"))
    second_decision = second_doc["decisions"][0]
    assert bool(second_decision.get("context_active", False)) is True
    assert int(second_decision.get("context_turns_used", 0)) >= 1


def test_live_payload_honors_manual_offline_director_override(tmp_path, monkeypatch) -> None:
    runs_dir = tmp_path / "runtime-runs"
    monkeypatch.setenv("ROONIE_DASHBOARD_RUNS_DIR", str(runs_dir))
    payload = {
        "session_id": "live-offline-override",
        "active_director": "OfflineDirector",
        "inputs": [
            {
                "event_id": "evt-1",
                "message": "@RoonieTheCat hey there!",
                "metadata": {
                    "user": "ruleofrune",
                    "is_direct_mention": True,
                    "mode": "live",
                    "platform": "twitch",
                },
            }
        ],
    }
    out_path = run_payload(payload, emit_outputs=False)
    run_doc = json.loads(out_path.read_text(encoding="utf-8"))
    assert run_doc["active_director"] == "OfflineDirector"
    decision = run_doc["decisions"][0]
    assert decision["route"] == "responder:neutral_ack"
    assert decision["response_text"] == "Hey there! Good to see you."


def test_provider_failure_no_auto_fallback_and_not_postable(tmp_path, monkeypatch) -> None:
    runs_dir = tmp_path / "runtime-runs"
    monkeypatch.setenv("ROONIE_DASHBOARD_RUNS_DIR", str(runs_dir))
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(tmp_path / "providers_config.json"))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(tmp_path / "routing_config.json"))
    monkeypatch.setenv("ROONIE_OUTPUT_DISABLED", "0")

    payload = {
        "session_id": "live-provider-fail",
        "inputs": [
            {
                "event_id": "evt-1",
                "message": "@RoonieTheCat can you help?",
                "metadata": {
                    "user": "ruleofrune",
                    "is_direct_mention": True,
                    "mode": "live",
                    "platform": "twitch",
                    "provider_test_overrides": {"primary_behavior": "throw"},
                },
            }
        ],
    }
    out_path = run_payload(payload, emit_outputs=True)
    run_doc = json.loads(out_path.read_text(encoding="utf-8"))
    decision = run_doc["decisions"][0]
    output = run_doc["outputs"][0]
    assert decision["trace"]["director"]["type"] == "ProviderDirector"
    assert decision["action"] == "NOOP"
    assert decision["response_text"] is None
    assert output["emitted"] is False
    assert output["reason"] == "PROVIDER_ERROR"


def test_disarmed_session_output_gate_suppresses_provider_proposal(tmp_path, monkeypatch) -> None:
    runs_dir = tmp_path / "runtime-runs"
    monkeypatch.setenv("ROONIE_DASHBOARD_RUNS_DIR", str(runs_dir))
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(tmp_path / "providers_config.json"))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(tmp_path / "routing_config.json"))
    monkeypatch.setenv("ROONIE_OUTPUT_DISABLED", "1")

    payload = {
        "session_id": "live-disarmed-suppress",
        "inputs": [
            {
                "event_id": "evt-1",
                "message": "@RoonieTheCat how's chat?",
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
    assert output["emitted"] is False
    assert output["reason"] == "OUTPUT_DISABLED"
