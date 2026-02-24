"""Tests for AudioInputBridge event creation and pipeline delegation."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest


# ── helpers ─────────────────────────────────────────────────


class _FakeLiveBridge:
    """Minimal stand-in for LiveChatBridge — captures _emit_payload_message calls."""

    def __init__(self) -> None:
        self.calls: list[Dict[str, Any]] = []

    def _emit_payload_message(
        self,
        *,
        actor: str,
        message: str,
        channel: str,
        is_direct_mention: bool,
        metadata_extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        call = {
            "actor": actor,
            "message": message,
            "channel": channel,
            "is_direct_mention": is_direct_mention,
            "metadata_extra": metadata_extra,
        }
        self.calls.append(call)
        return {"event_id": "test-001", "emitted": True, "reason": "TEST"}


class _FakeStorage:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir


# ── tests ───────────────────────────────────────────────────


def test_emit_voice_event_delegates_to_live_bridge():
    """_emit_voice_event should call LiveChatBridge._emit_payload_message with voice metadata."""
    from roonie.control_room.audio_bridge import AudioInputBridge

    fake_bridge = _FakeLiveBridge()
    fake_storage = _FakeStorage(Path("data"))
    bridge = AudioInputBridge(
        live_bridge=fake_bridge,
        storage=fake_storage,
    )
    bridge._emit_voice_event(
        user="Art",
        message="what song is this",
        raw_text="hey roonie what song is this",
        confidence=1.0,
    )
    assert len(fake_bridge.calls) == 1
    call = fake_bridge.calls[0]
    assert call["actor"] == "Art"
    assert call["message"] == "what song is this"
    assert call["channel"] == "voice"
    assert call["is_direct_mention"] is True
    meta = call["metadata_extra"]
    assert meta["platform"] == "voice"
    assert meta["source"] == "voice"
    assert meta["voice_confidence"] == 1.0
    assert meta["voice_raw_text"] == "hey roonie what song is this"


def test_emit_voice_event_handles_exception_gracefully():
    """If _emit_payload_message raises, the bridge should log but not crash."""
    from roonie.control_room.audio_bridge import AudioInputBridge

    def _exploding(**kwargs):
        raise RuntimeError("kaboom")

    fake_bridge = MagicMock()
    fake_bridge._emit_payload_message = _exploding
    fake_storage = _FakeStorage(Path("data"))
    bridge = AudioInputBridge(
        live_bridge=fake_bridge,
        storage=fake_storage,
    )
    # Should not raise.
    bridge._emit_voice_event(
        user="Art",
        message="test",
        raw_text="test",
        confidence=0.9,
    )


def test_bridge_stays_disabled_when_config_disabled(tmp_path):
    """When audio_config.json has enabled=false, _run should exit early."""
    import json
    from roonie.control_room.audio_bridge import AudioInputBridge

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "audio_config.json").write_text(
        json.dumps({"enabled": False}), encoding="utf-8",
    )
    fake_bridge = _FakeLiveBridge()
    fake_storage = _FakeStorage(data_dir)
    bridge = AudioInputBridge(
        live_bridge=fake_bridge,
        storage=fake_storage,
    )
    # Run directly (not in thread) — should return quickly.
    bridge._run()
    assert len(fake_bridge.calls) == 0


def test_load_audio_config_defaults(tmp_path):
    """_load_audio_config should return sane defaults for a missing file."""
    from roonie.control_room.audio_bridge import _load_audio_config

    config = _load_audio_config(tmp_path)
    assert config["enabled"] is False
    assert config["sample_rate"] == 16_000
    assert config["whisper_model"] == "base.en"
    assert config["wake_word_enabled"] is True
    assert config["voice_default_user"] == "Art"


def test_load_audio_config_override(tmp_path):
    """_load_audio_config should pick up values from audio_config.json."""
    import json

    cfg = {"enabled": True, "device_name": "Broadcast Stream Mix", "whisper_model": "small.en"}
    (tmp_path / "audio_config.json").write_text(json.dumps(cfg), encoding="utf-8")
    from roonie.control_room.audio_bridge import _load_audio_config

    config = _load_audio_config(tmp_path)
    assert config["enabled"] is True
    assert config["device_name"] == "Broadcast Stream Mix"
    assert config["whisper_model"] == "small.en"
    # Defaults still present for missing keys.
    assert config["sample_rate"] == 16_000


def test_voice_metadata_contains_required_fields():
    """Voice events must include platform, source, is_direct_mention, confidence, raw_text."""
    from roonie.control_room.audio_bridge import AudioInputBridge

    fake_bridge = _FakeLiveBridge()
    fake_storage = _FakeStorage(Path("data"))
    bridge = AudioInputBridge(
        live_bridge=fake_bridge,
        storage=fake_storage,
    )
    bridge._emit_voice_event(
        user="Jen",
        message="play something chill",
        raw_text="hey roonie play something chill",
        confidence=0.85,
    )
    meta = fake_bridge.calls[0]["metadata_extra"]
    required_keys = {"platform", "source", "is_direct_mention", "voice_confidence", "voice_raw_text"}
    assert required_keys.issubset(set(meta.keys()))
    assert meta["is_direct_mention"] is True


def test_bridge_start_stop():
    """start() and stop() should not raise even without audio hardware."""
    from roonie.control_room.audio_bridge import AudioInputBridge

    fake_bridge = _FakeLiveBridge()
    fake_storage = _FakeStorage(Path("data"))
    bridge = AudioInputBridge(
        live_bridge=fake_bridge,
        storage=fake_storage,
    )
    bridge.start()
    # Give thread a moment to start (it will exit quickly since config disabled).
    bridge.stop()
    bridge.join(timeout=2.0)
