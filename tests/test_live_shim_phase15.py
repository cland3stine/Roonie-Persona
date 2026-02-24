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


def test_run_payload_strips_inner_circle_from_persisted_inputs(tmp_path, monkeypatch) -> None:
    runs_dir = tmp_path / "runtime-runs"
    monkeypatch.setenv("ROONIE_DASHBOARD_RUNS_DIR", str(runs_dir))

    payload = {
        "session_id": "live-shim-strip-inner-circle",
        "inputs": [
            {
                "event_id": "evt-1",
                "message": "@RoonieTheCat hello",
                "metadata": {
                    "user": "ruleofrune",
                    "is_direct_mention": True,
                    "inner_circle": [{"username": "jen", "display_name": "Jen"}],
                },
            },
            {
                "event_id": "evt-2",
                "message": "@RoonieTheCat hi again",
                "metadata": {
                    "user": "ruleofrune",
                    "inner_circle": [{"username": "art", "display_name": "Art"}],
                },
            },
        ],
    }

    out_path = run_payload(payload, emit_outputs=False)
    doc = json.loads(out_path.read_text(encoding="utf-8"))
    for inp in doc["inputs"]:
        metadata = inp.get("metadata", {})
        assert "inner_circle" not in metadata
        assert metadata.get("user") == "ruleofrune"


def test_run_payload_strip_does_not_mutate_original_metadata(tmp_path, monkeypatch) -> None:
    runs_dir = tmp_path / "runtime-runs"
    monkeypatch.setenv("ROONIE_DASHBOARD_RUNS_DIR", str(runs_dir))

    inner_circle = [{"username": "jen", "display_name": "Jen", "role": "mod"}]
    metadata = {
        "user": "ruleofrune",
        "is_direct_mention": True,
        "inner_circle": inner_circle,
    }
    payload = {
        "session_id": "live-shim-strip-no-mutate",
        "inputs": [
            {
                "event_id": "evt-1",
                "message": "@RoonieTheCat hello",
                "metadata": metadata,
            }
        ],
    }

    _ = run_payload(payload, emit_outputs=False)
    assert "inner_circle" in payload["inputs"][0]["metadata"]
    assert payload["inputs"][0]["metadata"]["inner_circle"] == inner_circle


def test_run_payload_strips_model_metadata_from_persisted_decisions(tmp_path, monkeypatch) -> None:
    runs_dir = tmp_path / "runtime-runs"
    monkeypatch.setenv("ROONIE_DASHBOARD_RUNS_DIR", str(runs_dir))

    decision_payload = {
        "event_id": "evt-1",
        "action": "RESPOND_PUBLIC",
        "route": "primary:openai",
        "response_text": "all good",
        "trace": {
            "proposal": {
                "provider_used": "openai",
                "model_used": "gpt-5.2",
                "model": "gpt-5.2",
                "active_model": "gpt-5.2",
            },
            "routing": {
                "provider_selected": "openai",
                "model_selected": "gpt-5.2",
                "active_model": "gpt-5.2",
            },
        },
    }

    class _DecisionStub:
        def __init__(self, payload):
            self._payload = payload

        def to_dict(self, exclude_defaults=True):
            return self._payload

    class _DirectorStub:
        def evaluate(self, event, env):
            return _DecisionStub(decision_payload)

    payload = {
        "session_id": "live-shim-strip-model-metadata",
        "active_director": "ProviderDirector",
        "inputs": [
            {
                "event_id": "evt-1",
                "message": "@RoonieTheCat hey",
                "metadata": {"user": "ruleofrune", "is_direct_mention": True, "mode": "live"},
            }
        ],
    }

    out_path = run_payload(
        payload,
        emit_outputs=False,
        director_instance=_DirectorStub(),
        env_instance=Env(offline=False),
    )
    doc = json.loads(out_path.read_text(encoding="utf-8"))
    stored = doc["decisions"][0]
    proposal = stored["trace"]["proposal"]
    routing = stored["trace"]["routing"]
    assert proposal["provider_used"] == "openai"
    assert routing["provider_selected"] == "openai"
    assert "model_used" not in proposal
    assert "model" not in proposal
    assert "active_model" not in proposal
    assert "model_selected" not in routing
    assert "active_model" not in routing


def test_run_payload_model_sanitization_does_not_mutate_runtime_decision(tmp_path, monkeypatch) -> None:
    runs_dir = tmp_path / "runtime-runs"
    monkeypatch.setenv("ROONIE_DASHBOARD_RUNS_DIR", str(runs_dir))

    runtime_decision = {
        "event_id": "evt-1",
        "action": "RESPOND_PUBLIC",
        "route": "primary:openai",
        "response_text": "all good",
        "trace": {
            "proposal": {"provider_used": "openai", "model_used": "gpt-5.2"},
            "routing": {"provider_selected": "openai", "model_selected": "gpt-5.2"},
        },
    }

    class _DecisionStub:
        def __init__(self, payload):
            self._payload = payload

        def to_dict(self, exclude_defaults=True):
            return self._payload

    class _DirectorStub:
        def evaluate(self, event, env):
            return _DecisionStub(runtime_decision)

    payload = {
        "session_id": "live-shim-strip-model-no-mutate",
        "active_director": "ProviderDirector",
        "inputs": [
            {
                "event_id": "evt-1",
                "message": "@RoonieTheCat yo",
                "metadata": {"user": "ruleofrune", "is_direct_mention": True, "mode": "live"},
            }
        ],
    }
    _ = run_payload(
        payload,
        emit_outputs=False,
        director_instance=_DirectorStub(),
        env_instance=Env(offline=False),
    )
    assert runtime_decision["trace"]["proposal"]["model_used"] == "gpt-5.2"
    assert runtime_decision["trace"]["routing"]["model_selected"] == "gpt-5.2"


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


def test_live_payload_suppressed_output_does_not_add_assistant_context(tmp_path, monkeypatch) -> None:
    runs_dir = tmp_path / "runtime-runs"
    monkeypatch.setenv("ROONIE_DASHBOARD_RUNS_DIR", str(runs_dir))
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(tmp_path / "providers_config.json"))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(tmp_path / "routing_config.json"))
    monkeypatch.setenv("ROONIE_OUTPUT_RATE_LIMIT_SECONDS", "0")

    import responders.output_gate as output_gate

    output_gate._LAST_EMIT_TS = 0.0
    output_gate._LAST_EMIT_BY_KEY.clear()

    captured_prompts = []

    def _stub_route_generate(**kwargs):
        captured_prompts.append(str(kwargs.get("prompt") or ""))
        kwargs["context"]["provider_selected"] = "openai"
        kwargs["context"]["moderation_result"] = "allow"
        return "all good"

    monkeypatch.setattr("roonie.provider_director.route_generate", _stub_route_generate)

    director = ProviderDirector()
    env = Env(offline=False)

    # First turn is suppressed by OutputGate, so assistant text should not enter continuity context.
    monkeypatch.setenv("ROONIE_OUTPUT_DISABLED", "1")
    first_path = run_payload(
        {
            "session_id": "live-suppressed-context",
            "active_director": "ProviderDirector",
            "inputs": [
                {
                    "event_id": "evt-1",
                    "message": "@RoonieTheCat first message",
                    "metadata": {
                        "user": "ruleofrune",
                        "is_direct_mention": True,
                        "mode": "live",
                        "platform": "twitch",
                    },
                }
            ],
        },
        emit_outputs=True,
        director_instance=director,
        env_instance=env,
    )
    first_doc = json.loads(first_path.read_text(encoding="utf-8"))
    assert first_doc["outputs"][0]["emitted"] is False
    assert first_doc["outputs"][0]["reason"] == "OUTPUT_DISABLED"

    monkeypatch.setenv("ROONIE_OUTPUT_DISABLED", "0")
    _ = run_payload(
        {
            "session_id": "live-suppressed-context",
            "active_director": "ProviderDirector",
            "inputs": [
                {
                    "event_id": "evt-2",
                    "message": "@RoonieTheCat second message",
                    "metadata": {
                        "user": "ruleofrune",
                        "is_direct_mention": True,
                        "mode": "live",
                        "platform": "twitch",
                    },
                }
            ],
        },
        emit_outputs=True,
        director_instance=director,
        env_instance=env,
    )

    assert len(captured_prompts) >= 2
    second_prompt = captured_prompts[-1]
    assert "ruleofrune: @RoonieTheCat first message" in second_prompt
    assert "Roonie: all good" not in second_prompt


def test_live_payload_emitted_output_can_add_assistant_context(tmp_path, monkeypatch) -> None:
    runs_dir = tmp_path / "runtime-runs"
    monkeypatch.setenv("ROONIE_DASHBOARD_RUNS_DIR", str(runs_dir))
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(tmp_path / "providers_config.json"))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(tmp_path / "routing_config.json"))
    monkeypatch.setenv("ROONIE_OUTPUT_DISABLED", "0")
    monkeypatch.setenv("ROONIE_OUTPUT_RATE_LIMIT_SECONDS", "0")

    import responders.output_gate as output_gate

    output_gate._LAST_EMIT_TS = 0.0
    output_gate._LAST_EMIT_BY_KEY.clear()

    captured_prompts = []

    def _stub_route_generate(**kwargs):
        captured_prompts.append(str(kwargs.get("prompt") or ""))
        kwargs["context"]["provider_selected"] = "openai"
        kwargs["context"]["moderation_result"] = "allow"
        return "all good"

    monkeypatch.setattr("roonie.provider_director.route_generate", _stub_route_generate)
    monkeypatch.setattr(
        "adapters.twitch_output.TwitchOutputAdapter.handle_output",
        lambda self, envelope, ctx: {"sent": True, "reason": "OK_TEST"},
    )

    director = ProviderDirector()
    env = Env(offline=False)

    _ = run_payload(
        {
            "session_id": "live-emitted-context",
            "active_director": "ProviderDirector",
            "inputs": [
                {
                    "event_id": "evt-1",
                    "message": "@RoonieTheCat first message",
                    "metadata": {
                        "user": "ruleofrune",
                        "is_direct_mention": True,
                        "mode": "live",
                        "platform": "twitch",
                    },
                }
            ],
        },
        emit_outputs=True,
        director_instance=director,
        env_instance=env,
    )
    _ = run_payload(
        {
            "session_id": "live-emitted-context",
            "active_director": "ProviderDirector",
            "inputs": [
                {
                    "event_id": "evt-2",
                    "message": "@RoonieTheCat second message",
                    "metadata": {
                        "user": "ruleofrune",
                        "is_direct_mention": True,
                        "mode": "live",
                        "platform": "twitch",
                    },
                }
            ],
        },
        emit_outputs=True,
        director_instance=director,
        env_instance=env,
    )

    assert len(captured_prompts) >= 2
    second_prompt = captured_prompts[-1]
    assert "ruleofrune: @RoonieTheCat first message" in second_prompt
    assert "Roonie: all good" in second_prompt


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
