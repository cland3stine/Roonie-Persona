from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from roonie.control_room.eventsub_bridge import EventSubBridge
from roonie.control_room.live_chat import LiveChatBridge
from roonie.dashboard_api.storage import DashboardStorage
from roonie.types import DecisionRecord, Env, Event
from twitch.eventsub_ws import EventSubWSClient, normalize_eventsub_notification


def _set_dashboard_paths(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ROONIE_DASHBOARD_LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("ROONIE_DASHBOARD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ROONIE_DASHBOARD_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(tmp_path / "data" / "providers_config.json"))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(tmp_path / "data" / "routing_config.json"))


def test_eventsub_normalization_maps_supported_types() -> None:
    samples = [
        (
            "channel.follow",
            {"user_login": "alice", "user_name": "Alice", "followed_at": "2026-02-15T00:00:00Z"},
            "FOLLOW",
        ),
        (
            "channel.subscribe",
            {"user_login": "bob", "user_name": "Bob", "tier": "1000", "cumulative_months": 3},
            "SUB",
        ),
        (
            "channel.cheer",
            {"user_login": "cat", "user_name": "Cat", "bits": 250},
            "CHEER",
        ),
        (
            "channel.raid",
            {"from_broadcaster_user_login": "djx", "from_broadcaster_user_name": "DJX", "viewers": 42},
            "RAID",
        ),
    ]
    for idx, (raw_type, event_payload, expected_type) in enumerate(samples, start=1):
        message = {
            "metadata": {
                "message_type": "notification",
                "message_id": f"evt-{idx}",
                "message_timestamp": "2026-02-15T00:00:01Z",
            },
            "payload": {
                "subscription": {"type": raw_type},
                "event": event_payload,
            },
        }
        normalized = normalize_eventsub_notification(message)
        assert normalized is not None
        assert normalized["event_type"] == expected_type
        assert normalized["raw_type"] == raw_type
        assert normalized["twitch_event_id"] == f"evt-{idx}"


def test_eventsub_dedupe_by_twitch_event_id() -> None:
    seen: List[Dict[str, Any]] = []
    client = EventSubWSClient(
        oauth_token="oauth:testtoken",
        client_id="cid",
        broadcaster_user_id="1234",
        on_event=lambda event: seen.append(dict(event)),
        ws_factory=lambda url: None,
    )
    raw = json.dumps(
        {
            "metadata": {
                "message_type": "notification",
                "message_id": "dup-1",
                "message_timestamp": "2026-02-15T00:00:01Z",
            },
            "payload": {
                "subscription": {"type": "channel.follow"},
                "event": {"user_login": "alice", "user_name": "Alice"},
            },
        }
    )
    client.handle_raw_message(raw)
    client.handle_raw_message(raw)
    assert len(seen) == 1
    assert seen[0]["twitch_event_id"] == "dup-1"


def test_eventsub_reconnect_backoff_is_scheduled_on_disconnect() -> None:
    sleep_calls: List[float] = []
    state_events: List[Dict[str, Any]] = []

    def _raising_factory(url: str):
        raise OSError("socket down")

    client: EventSubWSClient

    def _sleep(seconds: float) -> None:
        sleep_calls.append(float(seconds))
        client.stop()

    client = EventSubWSClient(
        oauth_token="oauth:testtoken",
        client_id="cid",
        broadcaster_user_id="1234",
        on_event=lambda _event: None,
        on_state=lambda state: state_events.append(dict(state)),
        ws_factory=_raising_factory,
        sleep_fn=_sleep,
        random_fn=lambda: 0.0,
    )
    client.run_forever()

    assert len(sleep_calls) >= 1
    assert sleep_calls[0] >= 1.0
    assert any(int(item.get("reconnect_count", 0)) >= 1 for item in state_events)


def test_eventsub_pipeline_disarmed_suppresses_and_does_not_send(tmp_path: Path, monkeypatch) -> None:
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_KILL_SWITCH", "0")
    storage = DashboardStorage(runs_dir=tmp_path / "runs")
    live_bridge = LiveChatBridge(storage=storage, account="bot")
    eventsub_bridge = EventSubBridge(storage=storage, live_bridge=live_bridge)

    sent_calls: List[Dict[str, Any]] = []

    def _spy_handle_output(self, output: Dict[str, Any], metadata: Dict[str, Any]) -> None:
        sent_calls.append({"output": dict(output), "metadata": dict(metadata)})

    def _provider_stub(self, event: Event, env: Env) -> DecisionRecord:
        session_id = str(event.metadata.get("session_id", "")).strip() or None
        return DecisionRecord(
            case_id="live",
            event_id=event.event_id,
            action="RESPOND_PUBLIC",
            route="primary:openai",
            response_text="eventsub stub response",
            trace={
                "director": {"type": "ProviderDirector"},
                "proposal": {
                    "text": "eventsub stub response",
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

    monkeypatch.setattr("adapters.twitch_output.TwitchOutputAdapter.handle_output", _spy_handle_output)
    monkeypatch.setattr("roonie.provider_director.ProviderDirector.evaluate", _provider_stub)

    normalized = {
        "event_type": "FOLLOW",
        "raw_type": "channel.follow",
        "twitch_event_id": "evt-follow-1",
        "user_login": "alice",
        "display_name": "Alice",
        "timestamp": "2026-02-15T00:00:01Z",
    }
    eventsub_bridge._on_event(normalized)
    assert sent_calls == []

    # Disarmed default: no active session_id should be attached by EventSub path.
    disarmed_status = storage.get_status().to_dict()
    assert disarmed_status["armed"] is False
    assert disarmed_status["session_id"] is None
    assert "DISARMED" in disarmed_status["blocked_by"]

    eventsub_log = (tmp_path / "logs" / "eventsub_events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(eventsub_log) >= 1
    last_disarmed = json.loads(eventsub_log[-1])
    assert last_disarmed["twitch_event_id"] == "evt-follow-1"
    assert last_disarmed["session_id"] is None
    assert last_disarmed["emitted"] is False
    assert last_disarmed["suppression_reason"] in {"OUTPUT_DISABLED", "DISARMED", "ACTION_NOT_ALLOWED"}

    # Armed session should carry a concrete session_id through telemetry.
    storage.set_armed(True)
    active_session_id = str(storage.get_status().to_dict().get("session_id", "")).strip()
    assert active_session_id
    normalized2 = dict(normalized)
    normalized2["twitch_event_id"] = "evt-follow-2"
    eventsub_bridge._on_event(normalized2)
    eventsub_log2 = (tmp_path / "logs" / "eventsub_events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    last_armed = json.loads(eventsub_log2[-1])
    assert last_armed["twitch_event_id"] == "evt-follow-2"
    assert str(last_armed.get("session_id", "")).strip() == active_session_id


def test_status_exposes_eventsub_runtime_fields(tmp_path: Path, monkeypatch) -> None:
    _set_dashboard_paths(monkeypatch, tmp_path)
    storage = DashboardStorage(runs_dir=tmp_path / "runs")
    storage.set_eventsub_runtime_state(
        connected=True,
        session_id="es-session-1",
        last_message_ts="2026-02-15T12:00:00+00:00",
        reconnect_count=3,
        last_error="",
    )
    status = storage.get_status().to_dict()
    assert status["eventsub_connected"] is True
    assert status["eventsub_session_id"] == "es-session-1"
    assert status["eventsub_last_message_ts"] == "2026-02-15T12:00:00+00:00"
    assert status["eventsub_reconnect_count"] == 3
