from __future__ import annotations

from pathlib import Path

from roonie.dashboard_api.storage import DashboardStorage


def test_get_status_does_not_call_run_scan_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROONIE_DASHBOARD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ROONIE_DASHBOARD_LOGS_DIR", str(tmp_path / "logs"))

    storage = DashboardStorage(runs_dir=tmp_path / "runs")

    def _boom():
        raise AssertionError("_load_latest_run should not be called by get_status")

    monkeypatch.setattr(storage, "_load_latest_run", _boom)

    status = storage.get_status()
    payload = status.to_dict()

    assert "kill_switch_on" in payload
    assert "armed" in payload
    assert "mode" in payload
    assert "twitch_connected" in payload
    assert "last_heartbeat_at" in payload
    assert "active_provider" in payload
    assert "version" in payload
    assert "policy_loaded_at" in payload
    assert "policy_version" in payload
    assert "context_last_active" in payload
    assert "context_last_turns_used" in payload
