"""Tests for audio config storage and senses policy integration."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from roonie.dashboard_api.storage import DashboardStorage


def _make_storage(tmp_path: Path, monkeypatch) -> DashboardStorage:
    monkeypatch.setenv("ROONIE_DASHBOARD_LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("ROONIE_DASHBOARD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(tmp_path / "data" / "providers_config.json"))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(tmp_path / "data" / "routing_config.json"))
    return DashboardStorage(runs_dir=tmp_path / "runs")


# ── audio_config storage ────────────────────────────────────


def test_get_audio_config_creates_default(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    config = storage.get_audio_config()
    assert isinstance(config, dict)
    assert config["enabled"] is False
    assert config["sample_rate"] == 16_000
    assert config["whisper_model"] == "base.en"
    assert config["whisper_device"] == "cuda"
    assert config["wake_word_enabled"] is True
    assert config["transcription_interval_seconds"] == 3.0
    assert config["voice_default_user"] == "Art"


def test_get_audio_config_returns_deepcopy(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    a = storage.get_audio_config()
    b = storage.get_audio_config()
    # Ignore updated_at since it's set on each read-write cycle.
    a.pop("updated_at", None)
    b.pop("updated_at", None)
    assert a == b
    a["enabled"] = True
    c = storage.get_audio_config()
    assert c["enabled"] is False  # original unchanged


def test_update_audio_config_put(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    _ = storage.get_audio_config()
    new_cfg, audit = storage.update_audio_config(
        {"enabled": True, "device_name": "Broadcast Stream Mix"},
        actor="Art",
        patch=False,
    )
    assert new_cfg["enabled"] is True
    assert new_cfg["device_name"] == "Broadcast Stream Mix"
    assert "enabled" in audit["changed_keys"]


def test_update_audio_config_patch(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    _ = storage.get_audio_config()
    new_cfg, audit = storage.update_audio_config(
        {"whisper_model": "small.en"},
        actor="Art",
        patch=True,
    )
    assert new_cfg["whisper_model"] == "small.en"
    # Other defaults preserved.
    assert new_cfg["sample_rate"] == 16_000
    assert new_cfg["enabled"] is False


def test_update_audio_config_invalid_sample_rate(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    new_cfg, _ = storage.update_audio_config(
        {"sample_rate": 99999},
        actor="Art",
        patch=True,
    )
    assert new_cfg["sample_rate"] == 16_000  # reset to default


def test_update_audio_config_interval_clamped(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    new_cfg, _ = storage.update_audio_config(
        {"transcription_interval_seconds": 0.1},
        actor="Art",
        patch=True,
    )
    assert new_cfg["transcription_interval_seconds"] == 3.0  # reset to default (below 1.0)


def test_audio_config_persisted_to_disk(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    storage.update_audio_config(
        {"enabled": True, "device_name": "Test Device"},
        actor="Art",
    )
    raw = json.loads((tmp_path / "data" / "audio_config.json").read_text(encoding="utf-8"))
    assert raw["enabled"] is True
    assert raw["device_name"] == "Test Device"


# ── senses integration ──────────────────────────────────────


def test_senses_status_honors_enabled_field(tmp_path, monkeypatch):
    """Senses should no longer be hard-disabled — it should respect the config."""
    storage = _make_storage(tmp_path, monkeypatch)
    status = storage.get_senses_status()
    # Default: enabled=False → live_hard_disabled=True.
    assert status["enabled"] is False
    assert status["live_hard_disabled"] is True

    # Write enabled=True to the senses config and verify.
    senses_path = tmp_path / "data" / "senses_config.json"
    cfg = json.loads(senses_path.read_text(encoding="utf-8"))
    cfg["enabled"] = True
    senses_path.write_text(json.dumps(cfg), encoding="utf-8")

    status2 = storage.get_senses_status()
    assert status2["enabled"] is True
    assert status2["live_hard_disabled"] is False
    assert status2["reason"] == ""


def test_senses_defaults_still_safe(tmp_path, monkeypatch):
    """Senses defaults should still be restrictive (never_initiate, no_viewer_recognition, etc.)."""
    storage = _make_storage(tmp_path, monkeypatch)
    status = storage.get_senses_status()
    assert status["local_only"] is True
    assert status["never_initiate"] is True
    assert status["never_publicly_reference_detection"] is True
    assert status["no_viewer_recognition"] is True
    assert "Art" in status["whitelist"]
    assert "Jen" in status["whitelist"]
