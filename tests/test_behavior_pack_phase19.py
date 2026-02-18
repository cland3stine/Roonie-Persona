from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from live_shim.record_run import run_payload
from roonie.types import DecisionRecord, Env, Event


def _set_runtime_paths(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ROONIE_DASHBOARD_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("ROONIE_DASHBOARD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ROONIE_DASHBOARD_LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(tmp_path / "data" / "providers_config.json"))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(tmp_path / "data" / "routing_config.json"))


def _live_input(event_id: str, message: str, *, extra_metadata: Dict[str, Any] | None = None) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        "user": "ruleofrune",
        "is_direct_mention": True,
        "mode": "live",
        "platform": "twitch",
    }
    if isinstance(extra_metadata, dict):
        metadata.update(extra_metadata)
    return {
        "event_id": event_id,
        "message": message,
        "metadata": metadata,
    }


def _provider_event_stub(category: str, response_text: str, approved_emotes: List[str] | None = None):
    emotes = list(approved_emotes or [])

    def _stub(self, event: Event, env: Env) -> DecisionRecord:
        session_id = str(event.metadata.get("session_id", "")).strip() or None
        return DecisionRecord(
            case_id="live",
            event_id=event.event_id,
            action="RESPOND_PUBLIC",
            route="primary:openai",
            response_text=response_text,
            trace={
                "director": {"type": "ProviderDirector"},
                "behavior": {
                    "category": category,
                    "approved_emotes": emotes,
                },
                "proposal": {
                    "text": response_text,
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

    return _stub


def test_event_cooldown_suppresses_second_follow_and_no_second_send(tmp_path, monkeypatch) -> None:
    import responders.output_gate as output_gate

    _set_runtime_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_OUTPUT_DISABLED", "0")
    monkeypatch.setenv("ROONIE_OUTPUT_RATE_LIMIT_SECONDS", "0")
    output_gate._LAST_EMIT_TS = 0.0
    output_gate._LAST_EMIT_BY_KEY.clear()

    sent_calls: List[Dict[str, Any]] = []

    def _spy_handle_output(self, output: Dict[str, Any], metadata: Dict[str, Any]) -> None:
        sent_calls.append({"output": dict(output), "metadata": dict(metadata)})

    monkeypatch.setattr("adapters.twitch_output.TwitchOutputAdapter.handle_output", _spy_handle_output)
    monkeypatch.setattr(
        "roonie.provider_director.ProviderDirector.evaluate",
        _provider_event_stub("EVENT_FOLLOW", "Thanks for the follow!"),
    )

    out_path = run_payload(
        {
            "session_id": "phase19-follow-cooldown",
            "active_director": "ProviderDirector",
            "inputs": [
                _live_input("evt-1", "@RoonieTheCat follow event 1"),
                _live_input("evt-2", "@RoonieTheCat follow event 2"),
            ],
        },
        emit_outputs=True,
    )
    run_doc = json.loads(out_path.read_text(encoding="utf-8"))
    outputs = run_doc["outputs"]
    assert outputs[0]["emitted"] is True
    assert outputs[1]["emitted"] is False
    assert outputs[1]["reason"] == "EVENT_COOLDOWN"
    assert outputs[1]["category"] == "EVENT_FOLLOW"
    assert len(sent_calls) == 1


def test_direct_address_greeting_emits_when_gates_open(tmp_path, monkeypatch) -> None:
    import responders.output_gate as output_gate

    _set_runtime_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_OUTPUT_DISABLED", "0")
    monkeypatch.setenv("ROONIE_DRY_RUN", "0")
    monkeypatch.setenv("ROONIE_OUTPUT_RATE_LIMIT_SECONDS", "0")
    output_gate._LAST_EMIT_TS = 0.0
    output_gate._LAST_EMIT_BY_KEY.clear()

    sent_calls: List[Dict[str, Any]] = []

    def _spy_handle_output(self, output: Dict[str, Any], metadata: Dict[str, Any]) -> None:
        sent_calls.append({"output": dict(output), "metadata": dict(metadata)})

    monkeypatch.setattr("adapters.twitch_output.TwitchOutputAdapter.handle_output", _spy_handle_output)
    monkeypatch.setattr("roonie.provider_director.route_generate", lambda **kwargs: "Hey, welcome in.")

    out_path = run_payload(
        {
            "session_id": "phase19-greeting-open",
            "active_director": "ProviderDirector",
            "inputs": [_live_input("evt-1", "@RoonieTheCat hey there!")],
        },
        emit_outputs=True,
    )
    run_doc = json.loads(out_path.read_text(encoding="utf-8"))
    decision = run_doc["decisions"][0]
    output = run_doc["outputs"][0]
    assert decision["action"] == "RESPOND_PUBLIC"
    assert decision["route"].startswith("primary:")
    assert output["emitted"] is True
    assert output["reason"] == "EMITTED"
    assert len(sent_calls) == 1


def test_direct_address_greeting_cooldown_suppresses_second_message(tmp_path, monkeypatch) -> None:
    import responders.output_gate as output_gate

    _set_runtime_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_OUTPUT_DISABLED", "0")
    monkeypatch.setenv("ROONIE_DRY_RUN", "0")
    monkeypatch.setenv("ROONIE_OUTPUT_RATE_LIMIT_SECONDS", "0")
    output_gate._LAST_EMIT_TS = 0.0
    output_gate._LAST_EMIT_BY_KEY.clear()

    sent_calls: List[Dict[str, Any]] = []

    def _spy_handle_output(self, output: Dict[str, Any], metadata: Dict[str, Any]) -> None:
        sent_calls.append({"output": dict(output), "metadata": dict(metadata)})

    monkeypatch.setattr("adapters.twitch_output.TwitchOutputAdapter.handle_output", _spy_handle_output)
    monkeypatch.setattr("roonie.provider_director.route_generate", lambda **kwargs: "Hey there.")

    out_path = run_payload(
        {
            "session_id": "phase19-greeting-cooldown",
            "active_director": "ProviderDirector",
            "inputs": [
                _live_input("evt-1", "@RoonieTheCat hey"),
                _live_input("evt-2", "@RoonieTheCat hello"),
            ],
        },
        emit_outputs=True,
    )
    run_doc = json.loads(out_path.read_text(encoding="utf-8"))
    outputs = run_doc["outputs"]
    assert outputs[0]["emitted"] is True
    assert outputs[1]["emitted"] is False
    assert outputs[1]["reason"] == "GREETING_COOLDOWN"
    assert len(sent_calls) == 1


def test_disarmed_still_suppresses_event_category(tmp_path, monkeypatch) -> None:
    _set_runtime_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_OUTPUT_DISABLED", "1")

    sent_calls: List[Dict[str, Any]] = []

    def _spy_handle_output(self, output: Dict[str, Any], metadata: Dict[str, Any]) -> None:
        sent_calls.append({"output": dict(output), "metadata": dict(metadata)})

    monkeypatch.setattr("adapters.twitch_output.TwitchOutputAdapter.handle_output", _spy_handle_output)
    monkeypatch.setattr(
        "roonie.provider_director.ProviderDirector.evaluate",
        _provider_event_stub("EVENT_SUB", "Thanks for the sub!"),
    )

    out_path = run_payload(
        {
            "session_id": "phase19-disarmed-event",
            "active_director": "ProviderDirector",
            "inputs": [_live_input("evt-1", "@RoonieTheCat sub event")],
        },
        emit_outputs=True,
    )
    run_doc = json.loads(out_path.read_text(encoding="utf-8"))
    assert run_doc["outputs"][0]["emitted"] is False
    assert run_doc["outputs"][0]["reason"] == "OUTPUT_DISABLED"
    assert sent_calls == []


def test_noop_action_uses_noop_reason_not_action_not_allowed(monkeypatch) -> None:
    import responders.output_gate as output_gate

    monkeypatch.setenv("ROONIE_OUTPUT_DISABLED", "0")
    monkeypatch.setenv("ROONIE_DRY_RUN", "0")
    monkeypatch.setenv("ROONIE_OUTPUT_RATE_LIMIT_SECONDS", "0")
    output_gate._LAST_EMIT_TS = 0.0
    output_gate._LAST_EMIT_BY_KEY.clear()

    outputs = output_gate.maybe_emit(
        [
            {
                "event_id": "evt-noop",
                "action": "NOOP",
                "trace": {
                    "behavior": {"category": "BANTER"},
                },
            }
        ]
    )
    assert len(outputs) == 1
    assert outputs[0]["emitted"] is False
    assert outputs[0]["reason"] == "NOOP"


def test_track_id_without_now_playing_goes_through_llm(tmp_path, monkeypatch) -> None:
    _set_runtime_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_OUTPUT_DISABLED", "0")

    captured: Dict[str, Any] = {}

    def _stub_route_generate(**kwargs):
        captured["prompt"] = kwargs.get("prompt", "")
        kwargs["context"]["provider_selected"] = "openai"
        kwargs["context"]["moderation_result"] = "allow"
        return "Not sure, drop a timestamp and I can check."

    monkeypatch.setattr("roonie.provider_director.route_generate", _stub_route_generate)

    out_path = run_payload(
        {
            "session_id": "phase19-track-id",
            "active_director": "ProviderDirector",
            "inputs": [_live_input("evt-1", "@RoonieTheCat what track is this?")],
        },
        emit_outputs=False,
    )
    run_doc = json.loads(out_path.read_text(encoding="utf-8"))
    decision = run_doc["decisions"][0]
    assert decision["action"] == "RESPOND_PUBLIC"
    assert decision["route"].startswith("primary:")
    # Behavior guidance tells the LLM it has no track info
    prompt = str(captured.get("prompt", ""))
    assert "track" in prompt.lower()
    assert "Don't guess track names" in prompt or "don't have track info" in prompt.lower()


def test_disallowed_emote_is_suppressed_when_allow_list_present(tmp_path, monkeypatch) -> None:
    import responders.output_gate as output_gate

    _set_runtime_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_OUTPUT_DISABLED", "0")
    monkeypatch.setenv("ROONIE_OUTPUT_RATE_LIMIT_SECONDS", "0")
    output_gate._LAST_EMIT_TS = 0.0
    output_gate._LAST_EMIT_BY_KEY.clear()

    sent_calls: List[Dict[str, Any]] = []

    def _spy_handle_output(self, output: Dict[str, Any], metadata: Dict[str, Any]) -> None:
        sent_calls.append({"output": dict(output), "metadata": dict(metadata)})

    monkeypatch.setattr("adapters.twitch_output.TwitchOutputAdapter.handle_output", _spy_handle_output)
    monkeypatch.setattr(
        "roonie.provider_director.ProviderDirector.evaluate",
        _provider_event_stub("EVENT_SUB", "Thanks BadEmote", approved_emotes=["RoonieWave"]),
    )

    out_path = run_payload(
        {
            "session_id": "phase19-emote-allowlist",
            "active_director": "ProviderDirector",
            "inputs": [_live_input("evt-1", "@RoonieTheCat sub event", extra_metadata={"approved_emotes": ["RoonieWave"]})],
        },
        emit_outputs=True,
    )
    run_doc = json.loads(out_path.read_text(encoding="utf-8"))
    assert run_doc["outputs"][0]["emitted"] is False
    assert run_doc["outputs"][0]["reason"] == "DISALLOWED_EMOTE"
    assert sent_calls == []


def test_no_allow_list_does_not_auto_inject_emotes(tmp_path, monkeypatch) -> None:
    _set_runtime_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_OUTPUT_DISABLED", "0")
    monkeypatch.setattr("roonie.provider_director.route_generate", lambda **kwargs: "Hey there")

    out_path = run_payload(
        {
            "session_id": "phase19-no-emote-inject",
            "active_director": "ProviderDirector",
            "inputs": [_live_input("evt-1", "@RoonieTheCat can you help?")],
        },
        emit_outputs=False,
    )
    run_doc = json.loads(out_path.read_text(encoding="utf-8"))
    decision = run_doc["decisions"][0]
    assert decision["response_text"] == "Hey there"
    assert "RoonieWave" not in decision["response_text"]
    assert "RoonieHi" not in decision["response_text"]


def test_live_stub_output_is_sanitized_when_flag_enabled(tmp_path, monkeypatch) -> None:
    _set_runtime_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_OUTPUT_DISABLED", "0")
    monkeypatch.setenv("ROONIE_SANITIZE_PROVIDER_STUB_OUTPUT", "1")
    monkeypatch.setattr(
        "roonie.provider_director.route_generate",
        lambda **kwargs: "[openai stub] massive prompt echo",
    )

    out_path = run_payload(
        {
            "session_id": "phase19-stub-sanitize",
            "active_director": "ProviderDirector",
            "inputs": [_live_input("evt-1", "@RoonieTheCat how are you doing?")],
        },
        emit_outputs=False,
    )
    run_doc = json.loads(out_path.read_text(encoding="utf-8"))
    decision = run_doc["decisions"][0]
    assert decision["action"] == "RESPOND_PUBLIC"
    # Stub pool — any valid how-are banter response is acceptable
    _HOW_POOL = {"I'm good. glad you're here", "doing well. this set is helping", "all good up here on the booth"}
    assert decision["response_text"] in _HOW_POOL
