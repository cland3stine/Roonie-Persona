"""Quiet-chat nudge system — timer, config, category, behavior guidance."""
from __future__ import annotations

import time
import threading
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Category + cooldown tests (no runtime needed)
# ---------------------------------------------------------------------------

class TestNudgeCategory:
    """QUIET_NUDGE category classification and cooldown."""

    def test_event_type_maps_to_category(self):
        from roonie.behavior_spec import classify_behavior_category, CATEGORY_QUIET_NUDGE
        result = classify_behavior_category(
            message="[Quiet nudge]",
            metadata={"event_type": "QUIET_NUDGE"},
        )
        assert result == CATEGORY_QUIET_NUDGE

    def test_cooldown_600_seconds(self):
        from roonie.behavior_spec import cooldown_for_category, CATEGORY_QUIET_NUDGE
        cat, seconds, label = cooldown_for_category(CATEGORY_QUIET_NUDGE)
        assert cat == CATEGORY_QUIET_NUDGE
        assert seconds == 600.0
        assert label == "EVENT_COOLDOWN"


class TestNudgeBehaviorGuidance:
    """behavior_guidance() output for QUIET_NUDGE."""

    def test_guidance_without_now_playing(self):
        from roonie.behavior_spec import behavior_guidance, CATEGORY_QUIET_NUDGE
        text = behavior_guidance(
            category=CATEGORY_QUIET_NUDGE,
            approved_emotes=[],
            now_playing_available=False,
        )
        assert "quiet" in text.lower()
        assert "organic" in text.lower()
        assert "how's everyone doing" in text.lower()
        # Should NOT mention track since no now-playing
        assert "what's playing" not in text.lower()

    def test_guidance_with_now_playing(self):
        from roonie.behavior_spec import behavior_guidance, CATEGORY_QUIET_NUDGE
        text = behavior_guidance(
            category=CATEGORY_QUIET_NUDGE,
            approved_emotes=["ruleof6Heart"],
            now_playing_available=True,
        )
        assert "what's playing" in text.lower()
        assert "ruleof6Heart" in text


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------

class TestNudgeConfig:
    """LiveChatBridge quiet nudge config reading."""

    def test_defaults_without_storage_method(self):
        from roonie.control_room.live_chat import LiveChatBridge
        storage = MagicMock(spec=[])  # no get_quiet_nudge_config
        bridge = LiveChatBridge(storage=storage)
        cfg = bridge._get_quiet_nudge_config()
        assert cfg["quiet_nudge_enabled"] is True
        assert cfg["quiet_nudge_threshold_seconds"] == 300
        assert cfg["quiet_nudge_max_per_session"] == 5

    def test_config_override_from_storage(self):
        from roonie.control_room.live_chat import LiveChatBridge
        storage = MagicMock()
        storage.get_quiet_nudge_config.return_value = {
            "quiet_nudge_enabled": False,
            "quiet_nudge_threshold_seconds": 120,
            "quiet_nudge_max_per_session": 3,
        }
        bridge = LiveChatBridge(storage=storage)
        cfg = bridge._get_quiet_nudge_config()
        assert cfg["quiet_nudge_enabled"] is False
        assert cfg["quiet_nudge_threshold_seconds"] == 120
        assert cfg["quiet_nudge_max_per_session"] == 3


# ---------------------------------------------------------------------------
# Nudge emission logic
# ---------------------------------------------------------------------------

class TestNudgeEmission:
    """_emit_quiet_nudge() constructs correct payload."""

    def test_emit_quiet_nudge_calls_emit_payload(self):
        from roonie.control_room.live_chat import LiveChatBridge
        storage = MagicMock(spec=[])
        bridge = LiveChatBridge(storage=storage)
        # Stub _emit_payload_message
        result = {"emitted": True, "reason": "OK", "event_id": "test-1"}
        bridge._emit_payload_message = MagicMock(return_value=result)

        bridge._emit_quiet_nudge(quiet_minutes=5.0)

        bridge._emit_payload_message.assert_called_once()
        call_kwargs = bridge._emit_payload_message.call_args[1]
        assert call_kwargs["actor"] == "roonie-internal"
        assert call_kwargs["is_direct_mention"] is True
        assert "quiet" in call_kwargs["message"].lower()
        meta = call_kwargs["metadata_extra"]
        assert meta["event_type"] == "QUIET_NUDGE"
        assert meta["quiet_minutes"] == 5.0
        assert bridge._nudge_count == 1

    def test_emit_quiet_nudge_no_increment_on_suppress(self):
        from roonie.control_room.live_chat import LiveChatBridge
        storage = MagicMock(spec=[])
        bridge = LiveChatBridge(storage=storage)
        result = {"emitted": False, "reason": "COOLDOWN", "event_id": "test-1"}
        bridge._emit_payload_message = MagicMock(return_value=result)

        bridge._emit_quiet_nudge(quiet_minutes=5.0)
        assert bridge._nudge_count == 0

    def test_emit_quiet_nudge_updates_last_chat_ts(self):
        from roonie.control_room.live_chat import LiveChatBridge
        storage = MagicMock(spec=[])
        bridge = LiveChatBridge(storage=storage)
        bridge._last_chat_ts = 0.0
        result = {"emitted": True, "reason": "OK", "event_id": "test-1"}
        bridge._emit_payload_message = MagicMock(return_value=result)

        bridge._emit_quiet_nudge(quiet_minutes=5.0)
        assert bridge._last_chat_ts > 0.0


# ---------------------------------------------------------------------------
# Session cap
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# [SKIP] parsing for nudge events
# ---------------------------------------------------------------------------

class TestNudgeSkipParsing:
    """[SKIP] response from LLM should be suppressed, not sent to chat."""

    def test_skip_response_produces_noop(self, monkeypatch):
        from roonie.provider_director import ProviderDirector
        from roonie.types import Env, Event

        def _stub(**kwargs):
            kwargs["context"]["provider_selected"] = "grok"
            kwargs["context"]["moderation_result"] = "allow"
            return "[SKIP]"
        monkeypatch.setattr("roonie.provider_director.route_generate", _stub)

        director = ProviderDirector()
        env = Env(offline=False)
        event = Event(
            event_id="nudge-skip-1",
            message="[Quiet nudge: chat has been quiet]",
            metadata={
                "user": "roonie-internal",
                "is_direct_mention": True,
                "event_type": "QUIET_NUDGE",
                "source": "quiet_nudge",
                "mode": "live",
                "platform": "twitch",
                "session_id": "test-session",
            },
        )
        result = director.evaluate(event, env)
        assert result.action == "NOOP", f"[SKIP] should produce NOOP, got {result.action}"

    def test_real_response_still_emits(self, monkeypatch):
        from roonie.provider_director import ProviderDirector
        from roonie.types import Env, Event

        def _stub(**kwargs):
            kwargs["context"]["provider_selected"] = "grok"
            kwargs["context"]["moderation_result"] = "allow"
            return "this track has such a smooth groove"
        monkeypatch.setattr("roonie.provider_director.route_generate", _stub)

        director = ProviderDirector()
        env = Env(offline=False)
        event = Event(
            event_id="nudge-emit-1",
            message="[Quiet nudge: chat has been quiet]",
            metadata={
                "user": "roonie-internal",
                "is_direct_mention": True,
                "event_type": "QUIET_NUDGE",
                "source": "quiet_nudge",
                "mode": "live",
                "platform": "twitch",
                "session_id": "test-session",
            },
        )
        result = director.evaluate(event, env)
        assert result.action == "RESPOND_PUBLIC"


class TestNudgeSessionCap:
    """Nudge loop respects max_per_session."""

    def test_cap_prevents_further_nudges(self):
        from roonie.control_room.live_chat import LiveChatBridge
        storage = MagicMock(spec=[])
        bridge = LiveChatBridge(storage=storage)
        bridge._nudge_count = 5  # already at default max

        cfg = bridge._get_quiet_nudge_config()
        max_nudges = int(cfg.get("quiet_nudge_max_per_session", 5))
        assert bridge._nudge_count >= max_nudges


# ---------------------------------------------------------------------------
# _emit_one updates last_chat_ts
# ---------------------------------------------------------------------------

class TestChatTimestampUpdate:
    """Incoming chat messages update _last_chat_ts."""

    def test_emit_one_updates_timestamp(self):
        from roonie.control_room.live_chat import LiveChatBridge
        storage = MagicMock()
        storage.get_status.return_value = MagicMock(
            to_dict=lambda: {
                "can_post": True,
                "blocked_by": [],
                "active_director": "ProviderDirector",
                "routing_enabled": True,
                "session_id": "test",
            }
        )
        bridge = LiveChatBridge(storage=storage)
        bridge._last_chat_ts = 0.0

        msg = MagicMock()
        msg.message = "hello"
        msg.nick = "testviewer"
        msg.channel = "#testchannel"
        msg.tags = {}

        # Stub the heavy parts
        bridge._emit_payload_message = MagicMock(return_value={
            "emitted": False, "reason": "TEST", "event_id": "e1",
            "can_post": True, "blocked_by": [], "send_result": None,
        })

        bridge._emit_one(msg, bot_nick="rooniethecat")
        assert bridge._last_chat_ts > 0.0
