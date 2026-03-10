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
            "channel.subscription.message",
            {"user_login": "bob", "user_name": "Bob", "tier": "1000", "cumulative_months": 14},
            "SUB",
        ),
        (
            "channel.subscription.gift",
            {"user_login": "gifter", "user_name": "Gifter", "tier": "1000", "total": 5, "cumulative_total": 42},
            "GIFTED_SUB",
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
        (
            "stream.online",
            {"broadcaster_user_login": "ruleofrune", "broadcaster_user_name": "RuleOfRune"},
            "STREAM_ONLINE",
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



def test_eventsub_subscription_message_normalization_carries_resub_months() -> None:
    message = {
        "metadata": {
            "message_type": "notification",
            "message_id": "evt-resub-1",
            "message_timestamp": "2026-02-15T00:00:01Z",
        },
        "payload": {
            "subscription": {"type": "channel.subscription.message"},
            "event": {
                "user_login": "bob",
                "user_name": "Bob",
                "tier": "1000",
                "cumulative_months": 14,
            },
        },
    }
    normalized = normalize_eventsub_notification(message)
    assert normalized is not None
    assert normalized["event_type"] == "SUB"
    assert normalized["is_resub"] is True
    assert normalized["months"] == 14
    assert normalized["tier"] == "1000"


def test_eventsub_subscription_gift_normalization_carries_gifter_identity() -> None:
    message = {
        "metadata": {
            "message_type": "notification",
            "message_id": "evt-gift-1",
            "message_timestamp": "2026-02-15T00:00:01Z",
        },
        "payload": {
            "subscription": {"type": "channel.subscription.gift"},
            "event": {
                "user_login": "gifter",
                "user_name": "Gifter",
                "tier": "1000",
                "total": 5,
                "cumulative_total": 42,
                "is_anonymous": False,
            },
        },
    }
    normalized = normalize_eventsub_notification(message)
    assert normalized is not None
    assert normalized["event_type"] == "GIFTED_SUB"
    assert normalized["user_login"] == "gifter"
    assert normalized["display_name"] == "Gifter"
    assert normalized["gift_count"] == 5
    assert normalized["cumulative_total"] == 42
    assert normalized["is_gift"] is True


def test_eventsub_subscriptions_include_phase1_types(monkeypatch) -> None:
    posted_types: List[str] = []
    client = EventSubWSClient(
        oauth_token="oauth:testtoken",
        client_id="cid",
        broadcaster_user_id="1234",
        on_event=lambda _event: None,
        ws_factory=lambda _url: None,
    )

    def _post_subscription(*, session_id: str, sub_type: str, version: str, condition: Dict[str, Any]) -> None:
        _ = (session_id, version, condition)
        posted_types.append(sub_type)

    monkeypatch.setattr(client, "_post_subscription", _post_subscription)
    client._ensure_subscriptions("session-1")
    assert "channel.subscription.message" in posted_types
    assert "channel.subscription.gift" in posted_types
    assert "stream.online" in posted_types


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


def test_eventsub_pipeline_disarmed_raid_suppresses_and_does_not_send(tmp_path: Path, monkeypatch) -> None:
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
        "event_type": "RAID",
        "raw_type": "channel.raid",
        "twitch_event_id": "evt-raid-1",
        "user_login": "djx",
        "display_name": "DJX",
        "raid_viewer_count": 42,
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
    assert last_disarmed["twitch_event_id"] == "evt-raid-1"
    assert last_disarmed["session_id"] is None
    assert last_disarmed["emitted"] is False
    assert last_disarmed["suppression_reason"] in {"OUTPUT_DISABLED", "DISARMED", "ACTION_NOT_ALLOWED"}

    # Armed session should carry a concrete session_id through telemetry.
    storage.set_armed(True)
    active_session_id = str(storage.get_status().to_dict().get("session_id", "")).strip()
    assert active_session_id
    normalized2 = dict(normalized)
    normalized2["twitch_event_id"] = "evt-raid-2"
    eventsub_bridge._on_event(normalized2)
    eventsub_log2 = (tmp_path / "logs" / "eventsub_events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    last_armed = json.loads(eventsub_log2[-1])
    assert last_armed["twitch_event_id"] == "evt-raid-2"
    assert str(last_armed.get("session_id", "")).strip() == active_session_id




def test_eventsub_follow_events_are_suppressed_before_persona_path(tmp_path: Path, monkeypatch) -> None:
    _set_dashboard_paths(monkeypatch, tmp_path)
    storage = DashboardStorage(runs_dir=tmp_path / "runs")
    live_bridge = LiveChatBridge(storage=storage, account="bot")
    eventsub_bridge = EventSubBridge(storage=storage, live_bridge=live_bridge)

    ingest_calls: List[Dict[str, Any]] = []

    def _ingest_stub(normalized_event: Dict[str, Any], *, text: str) -> Dict[str, Any]:
        ingest_calls.append({"normalized_event": dict(normalized_event), "text": str(text)})
        return {"emitted": True, "reason": "SENT", "session_id": "sid-follow"}

    monkeypatch.setattr(live_bridge, "ingest_eventsub_event", _ingest_stub)

    storage.set_armed(True)
    eventsub_bridge._on_event(
        {
            "event_type": "FOLLOW",
            "raw_type": "channel.follow",
            "twitch_event_id": "evt-follow-1",
            "user_login": "alice",
            "display_name": "Alice",
            "timestamp": "2026-02-28T00:00:01Z",
        }
    )

    assert ingest_calls == []

    rows = [
        json.loads(line)
        for line in (tmp_path / "logs" / "eventsub_events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows
    last = rows[-1]
    assert last["twitch_event_id"] == "evt-follow-1"
    assert last["event_type"] == "FOLLOW"
    assert last["emitted"] is False
    assert last["session_id"] is None
    assert last["suppression_reason"] == "SUPPRESSED_EVENT_TYPE:FOLLOW"


def test_eventsub_sub_events_are_suppressed_by_event_type_before_reenable(tmp_path: Path, monkeypatch) -> None:
    _set_dashboard_paths(monkeypatch, tmp_path)
    storage = DashboardStorage(runs_dir=tmp_path / "runs")
    live_bridge = LiveChatBridge(storage=storage, account="bot")
    eventsub_bridge = EventSubBridge(storage=storage, live_bridge=live_bridge)

    ingest_calls: List[Dict[str, Any]] = []

    def _ingest_stub(normalized_event: Dict[str, Any], *, text: str) -> Dict[str, Any]:
        ingest_calls.append({"normalized_event": dict(normalized_event), "text": str(text)})
        return {"emitted": True, "reason": "SENT", "session_id": "sid-sub"}

    monkeypatch.setattr(live_bridge, "ingest_eventsub_event", _ingest_stub)

    eventsub_bridge._on_event(
        {
            "event_type": "SUB",
            "raw_type": "channel.subscribe",
            "twitch_event_id": "evt-sub-general-1",
            "user_login": "alice",
            "display_name": "Alice",
            "tier": "1000",
            "timestamp": "2026-02-28T00:00:01Z",
        }
    )

    assert ingest_calls == []

    rows = [
        json.loads(line)
        for line in (tmp_path / "logs" / "eventsub_events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows
    last = rows[-1]
    assert last["twitch_event_id"] == "evt-sub-general-1"
    assert last["event_type"] == "SUB"
    assert last["emitted"] is False
    assert last["session_id"] is None
    assert last["suppression_reason"] == "SUPPRESSED_EVENT_TYPE:SUB"



def test_eventsub_gifted_sub_events_are_suppressed_before_reenable(tmp_path: Path, monkeypatch) -> None:
    _set_dashboard_paths(monkeypatch, tmp_path)
    storage = DashboardStorage(runs_dir=tmp_path / "runs")
    live_bridge = LiveChatBridge(storage=storage, account="bot")
    eventsub_bridge = EventSubBridge(storage=storage, live_bridge=live_bridge)

    ingest_calls: List[Dict[str, Any]] = []

    def _ingest_stub(normalized_event: Dict[str, Any], *, text: str) -> Dict[str, Any]:
        ingest_calls.append({"normalized_event": dict(normalized_event), "text": str(text)})
        return {"emitted": True, "reason": "SENT", "session_id": "sid-gift"}

    monkeypatch.setattr(live_bridge, "ingest_eventsub_event", _ingest_stub)

    eventsub_bridge._on_event(
        {
            "event_type": "GIFTED_SUB",
            "raw_type": "channel.subscription.gift",
            "twitch_event_id": "evt-gifted-sub-1",
            "user_login": "gifter",
            "display_name": "Gifter",
            "tier": "1000",
            "gift_count": 3,
            "timestamp": "2026-02-28T00:00:01Z",
        }
    )

    assert ingest_calls == []

    rows = [
        json.loads(line)
        for line in (tmp_path / "logs" / "eventsub_events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows
    last = rows[-1]
    assert last["twitch_event_id"] == "evt-gifted-sub-1"
    assert last["event_type"] == "GIFTED_SUB"
    assert last["emitted"] is False
    assert last["session_id"] is None
    assert last["suppression_reason"] == "SUPPRESSED_EVENT_TYPE:GIFTED_SUB"


def test_eventsub_sub_events_from_inner_circle_are_suppressed(tmp_path: Path, monkeypatch) -> None:
    _set_dashboard_paths(monkeypatch, tmp_path)
    storage = DashboardStorage(runs_dir=tmp_path / "runs")
    live_bridge = LiveChatBridge(storage=storage, account="bot")
    eventsub_bridge = EventSubBridge(storage=storage, live_bridge=live_bridge)

    ingest_calls: List[Dict[str, Any]] = []

    def _ingest_stub(normalized_event: Dict[str, Any], *, text: str) -> Dict[str, Any]:
        ingest_calls.append({"normalized_event": dict(normalized_event), "text": str(text)})
        return {"emitted": True, "reason": "SENT", "session_id": "sid-1"}

    monkeypatch.setattr(live_bridge, "ingest_eventsub_event", _ingest_stub)

    blocked_users = ["cland3stine", "c0rcyra", "RuleOfRune"]
    for idx, user in enumerate(blocked_users, start=1):
        eventsub_bridge._on_event(
            {
                "event_type": "SUB",
                "raw_type": "channel.subscribe",
                "twitch_event_id": f"evt-sub-{idx}",
                "user_login": user,
                "display_name": user,
                "tier": "1000",
                "timestamp": "2026-02-28T00:00:01Z",
            }
        )

    assert ingest_calls == []

    eventsub_log_path = tmp_path / "logs" / "eventsub_events.jsonl"
    rows = [
        json.loads(line)
        for line in eventsub_log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == len(blocked_users)
    assert all(row["emitted"] is False for row in rows)
    assert all(row["suppression_reason"] == "IGNORED_SELF_SUB" for row in rows)

    eventsub_bridge._on_event(
        {
            "event_type": "RAID",
            "raw_type": "channel.raid",
            "twitch_event_id": "evt-raid-1",
            "user_login": "cland3stine",
            "display_name": "cland3stine",
            "raid_viewer_count": 14,
            "timestamp": "2026-02-28T00:00:02Z",
        }
    )
    assert len(ingest_calls) == 1


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


def test_eventsub_stream_online_routes_to_social_announcer(tmp_path: Path, monkeypatch) -> None:
    _set_dashboard_paths(monkeypatch, tmp_path)
    storage = DashboardStorage(runs_dir=tmp_path / "runs")
    live_bridge = LiveChatBridge(storage=storage, account="bot")
    eventsub_bridge = EventSubBridge(storage=storage, live_bridge=live_bridge)

    ingest_calls: List[Dict[str, Any]] = []
    social_calls: List[Dict[str, Any]] = []

    def _ingest_stub(normalized_event: Dict[str, Any], *, text: str) -> Dict[str, Any]:
        ingest_calls.append({"normalized_event": dict(normalized_event), "text": str(text)})
        return {"emitted": True, "reason": "SENT", "session_id": "sid-ingest"}

    class _SocialStub:
        def announce_stream_online(self, normalized_event: Dict[str, Any]) -> Dict[str, Any]:
            social_calls.append(dict(normalized_event))
            return {"ok": True, "sent": True, "reason": "SENT"}

    monkeypatch.setattr(live_bridge, "ingest_eventsub_event", _ingest_stub)
    eventsub_bridge._social_announcer = _SocialStub()

    eventsub_bridge._on_event(
        {
            "event_type": "STREAM_ONLINE",
            "raw_type": "stream.online",
            "twitch_event_id": "evt-stream-online-1",
            "user_login": "ruleofrune",
            "display_name": "RuleOfRune",
            "channel": "ruleofrune",
            "timestamp": "2026-03-03T00:00:01Z",
        }
    )

    assert ingest_calls == []
    assert len(social_calls) == 1
    eventsub_log = (tmp_path / "logs" / "eventsub_events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert eventsub_log
    last = json.loads(eventsub_log[-1])
    assert last["twitch_event_id"] == "evt-stream-online-1"
    assert last["event_type"] == "STREAM_ONLINE"
    assert last["emitted"] is True
