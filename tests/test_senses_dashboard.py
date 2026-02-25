"""Tests for senses dashboard: audio capture level, runtime state storage,
device listing, API endpoints, and bridge state push."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

from roonie.dashboard_api.storage import DashboardStorage


def _make_storage(tmp_path: Path, monkeypatch) -> DashboardStorage:
    monkeypatch.setenv("ROONIE_DASHBOARD_LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("ROONIE_DASHBOARD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(tmp_path / "data" / "providers_config.json"))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(tmp_path / "data" / "routing_config.json"))
    return DashboardStorage(runs_dir=tmp_path / "runs")


# ── AudioCapture level tracking ──────────────────────────────


@pytest.mark.skipif(not HAS_NUMPY, reason="numpy not installed")
class TestAudioCaptureLevel:
    def test_initial_level_is_zero(self):
        from audio.capture import AudioCapture
        cap = AudioCapture(device=None, sample_rate=16000)
        assert cap.get_level() == 0.0

    def test_level_after_callback(self):
        from audio.capture import AudioCapture
        cap = AudioCapture(device=None, sample_rate=16000)
        samples = np.array([[0.5], [0.5], [0.5], [0.5]], dtype=np.float32)
        cap._callback(samples, 4, None, None)
        level = cap.get_level()
        assert level == pytest.approx(0.5, abs=0.01)

    def test_level_with_silence(self):
        from audio.capture import AudioCapture
        cap = AudioCapture(device=None, sample_rate=16000)
        samples = np.zeros((100, 1), dtype=np.float32)
        cap._callback(samples, 100, None, None)
        assert cap.get_level() == 0.0

    def test_level_updates_on_each_callback(self):
        from audio.capture import AudioCapture
        cap = AudioCapture(device=None, sample_rate=16000)
        loud = np.ones((100, 1), dtype=np.float32) * 0.8
        cap._callback(loud, 100, None, None)
        level1 = cap.get_level()
        quiet = np.ones((100, 1), dtype=np.float32) * 0.1
        cap._callback(quiet, 100, None, None)
        level2 = cap.get_level()
        assert level1 > level2

    def test_level_with_1d_input(self):
        from audio.capture import AudioCapture
        cap = AudioCapture(device=None, sample_rate=16000)
        samples = np.array([0.3, 0.3, 0.3, 0.3], dtype=np.float32)
        cap._callback(samples, 4, None, None)
        level = cap.get_level()
        assert level == pytest.approx(0.3, abs=0.01)


# ── Storage: audio runtime state ─────────────────────────────


class TestAudioRuntimeState:
    def test_initial_state_is_empty(self, tmp_path, monkeypatch):
        storage = _make_storage(tmp_path, monkeypatch)
        assert storage.get_audio_runtime_state() == {}

    def test_set_and_get_roundtrip(self, tmp_path, monkeypatch):
        storage = _make_storage(tmp_path, monkeypatch)
        state = {
            "running": True,
            "device": "Test Device",
            "level_rms": 0.42,
            "chunks_processed": 10,
            "wake_words_detected": 2,
            "events_emitted": 1,
        }
        storage.set_audio_runtime_state(state)
        result = storage.get_audio_runtime_state()
        assert result == state

    def test_get_returns_copy(self, tmp_path, monkeypatch):
        storage = _make_storage(tmp_path, monkeypatch)
        storage.set_audio_runtime_state({"running": True})
        a = storage.get_audio_runtime_state()
        a["running"] = False
        b = storage.get_audio_runtime_state()
        assert b["running"] is True

    def test_set_replaces_entirely(self, tmp_path, monkeypatch):
        storage = _make_storage(tmp_path, monkeypatch)
        storage.set_audio_runtime_state({"running": True, "device": "A"})
        storage.set_audio_runtime_state({"running": False})
        result = storage.get_audio_runtime_state()
        assert result == {"running": False}
        assert "device" not in result


# ── Storage: list_audio_devices ──────────────────────────────


class TestListAudioDevices:
    def test_returns_empty_when_sounddevice_unavailable(self, monkeypatch):
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "sounddevice":
                raise ImportError("no sounddevice")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        result = DashboardStorage.list_audio_devices()
        assert result == []

    def test_returns_input_devices_only(self):
        fake_devices = [
            {"name": "Mic In", "max_input_channels": 2, "max_output_channels": 0, "default_samplerate": 44100.0},
            {"name": "Speakers", "max_input_channels": 0, "max_output_channels": 2, "default_samplerate": 48000.0},
            {"name": "Loopback", "max_input_channels": 2, "max_output_channels": 2, "default_samplerate": 48000.0},
        ]
        fake_sd = MagicMock()
        fake_sd.query_devices = MagicMock(return_value=fake_devices)
        with patch.dict("sys.modules", {"sounddevice": fake_sd}):
            result = DashboardStorage.list_audio_devices()
        assert len(result) == 2
        names = [d["name"] for d in result]
        assert "Mic In" in names
        assert "Loopback" in names
        assert "Speakers" not in names
        for d in result:
            assert "index" in d
            assert "name" in d
            assert "max_input_channels" in d
            assert "default_samplerate" in d


# ── API endpoints ────────────────────────────────────────────


class TestAudioStatusEndpoint:
    def test_audio_status_returns_runtime_state(self, tmp_path, monkeypatch):
        storage = _make_storage(tmp_path, monkeypatch)
        state = {"running": True, "level_rms": 0.35, "chunks_processed": 5}
        storage.set_audio_runtime_state(state)
        result = storage.get_audio_runtime_state()
        assert result["running"] is True
        assert result["level_rms"] == 0.35

    def test_audio_status_empty_when_no_bridge(self, tmp_path, monkeypatch):
        storage = _make_storage(tmp_path, monkeypatch)
        result = storage.get_audio_runtime_state()
        assert result == {}


# ── AudioInputBridge state push ──────────────────────────────


class _FakeStorageWithAudioState:
    """Storage stand-in that records set_audio_runtime_state calls."""
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.audio_states: list[Dict[str, Any]] = []

    def set_audio_runtime_state(self, state: Dict[str, Any]) -> None:
        self.audio_states.append(dict(state))


class _FakeLiveBridge:
    def __init__(self) -> None:
        self.calls: list[Dict[str, Any]] = []

    def _emit_payload_message(self, **kwargs) -> Dict[str, Any]:
        self.calls.append(kwargs)
        return {"event_id": "test-001", "emitted": True, "reason": "TEST"}


class TestAudioBridgeStatePush:
    def test_bridge_pushes_state_to_storage(self, tmp_path):
        """AudioInputBridge should call set_audio_runtime_state during its run loop."""
        from roonie.control_room.audio_bridge import AudioInputBridge

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        config = {
            "enabled": True,
            "device_name": "",
            "sample_rate": 16000,
            "whisper_model": "base.en",
            "whisper_device": "cpu",
            "wake_word_enabled": False,
            "transcription_interval_seconds": 0.1,
            "voice_default_user": "TestUser",
        }
        (data_dir / "audio_config.json").write_text(json.dumps(config))

        storage = _FakeStorageWithAudioState(data_dir)
        bridge_obj = _FakeLiveBridge()

        bridge = AudioInputBridge(
            live_bridge=bridge_obj,
            storage=storage,
        )

        fake_capture = MagicMock()
        fake_capture.start = MagicMock()
        fake_capture.stop = MagicMock()
        fake_capture.join = MagicMock()
        fake_capture.get_level = MagicMock(return_value=0.42)

        call_count = 0
        fake_chunk = MagicMock()  # stands in for np.ndarray
        fake_chunk.__len__ = lambda self: 1600

        def fake_get_chunk():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return fake_chunk
            bridge._stop.set()
            return None

        fake_capture.get_chunk = fake_get_chunk

        fake_transcriber = MagicMock()
        fake_transcriber.transcribe_text = MagicMock(return_value="hello test")
        fake_detector = MagicMock()

        with patch.dict("sys.modules", {
            "audio.capture": MagicMock(AudioCapture=lambda **kw: fake_capture),
            "audio.transcriber": MagicMock(WhisperTranscriber=lambda **kw: fake_transcriber),
            "audio.wake_word": MagicMock(WakeWordDetector=lambda: fake_detector),
        }):
            bridge.start()
            bridge._thread.join(timeout=5.0)

        assert len(storage.audio_states) >= 1
        last = storage.audio_states[-1]
        assert last["running"] is False

    def test_bridge_no_state_push_when_disabled(self, tmp_path):
        """When audio is disabled in config, bridge should exit without pushing state."""
        from roonie.control_room.audio_bridge import AudioInputBridge

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        config = {"enabled": False}
        (data_dir / "audio_config.json").write_text(json.dumps(config))

        storage = _FakeStorageWithAudioState(data_dir)
        bridge_obj = _FakeLiveBridge()

        bridge = AudioInputBridge(
            live_bridge=bridge_obj,
            storage=storage,
        )
        bridge.start()
        bridge._thread.join(timeout=3.0)
        assert len(storage.audio_states) == 0
