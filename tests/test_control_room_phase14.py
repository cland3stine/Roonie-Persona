from __future__ import annotations

import json
import os
import threading
import urllib.request
from pathlib import Path
from typing import Any, Dict

from roonie.control_room.preflight import resolve_runtime_paths, run_preflight
from roonie.dashboard_api.app import create_server
from roonie.dashboard_api.storage import DashboardStorage
from roonie.run_control_room import _apply_safe_start_defaults


def _get_json(base: str, path: str) -> Dict[str, Any]:
    with urllib.request.urlopen(f"{base}{path}", timeout=2.0) as response:
        return json.loads(response.read().decode("utf-8"))


def _start_server(runs_dir: Path, readiness_state: Dict[str, Any]):
    server = create_server(host="127.0.0.1", port=0, runs_dir=runs_dir, readiness_state=readiness_state)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    return server, thread


def _write_persona_policy(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_preflight_passes_and_seeds_configs(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _write_persona_policy(
        repo_root / "persona" / "persona_policy.yaml",
        "version: 1\npersona: roonie\nsenses:\n  enabled: false\n",
    )

    monkeypatch.delenv("ROONIE_DASHBOARD_DATA_DIR", raising=False)
    monkeypatch.delenv("ROONIE_DASHBOARD_LOGS_DIR", raising=False)
    monkeypatch.delenv("ROONIE_DASHBOARD_RUNS_DIR", raising=False)
    monkeypatch.delenv("ROONIE_PROVIDERS_CONFIG_PATH", raising=False)
    monkeypatch.delenv("ROONIE_ROUTING_CONFIG_PATH", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localapp"))

    paths = resolve_runtime_paths(repo_root=repo_root, runs_dir="runs", log_dir="logs")
    result = run_preflight(paths)

    assert result["ready"] is True
    assert result["blocking_reasons"] == []
    assert (paths.data_dir / "providers_config.json").exists()
    assert (paths.data_dir / "routing_config.json").exists()
    assert (paths.data_dir / "senses_config.json").exists()
    assert (paths.data_dir / "studio_profile.json").exists()
    assert (paths.data_dir / "twitch_config.json").exists()
    assert (paths.data_dir / "memory.sqlite").exists()


def test_preflight_fails_on_invalid_or_missing_persona_policy(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("ROONIE_DASHBOARD_DATA_DIR", raising=False)
    monkeypatch.delenv("ROONIE_DASHBOARD_LOGS_DIR", raising=False)
    monkeypatch.delenv("ROONIE_DASHBOARD_RUNS_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localapp"))

    # invalid content
    policy_path = repo_root / "persona" / "persona_policy.yaml"
    _write_persona_policy(policy_path, "this is not yaml")
    paths = resolve_runtime_paths(repo_root=repo_root, runs_dir="runs", log_dir="logs")
    result = run_preflight(paths)
    assert result["ready"] is False
    assert any(str(reason).startswith("persona_policy:") for reason in result["blocking_reasons"])

    # missing file
    policy_path.unlink()
    missing_result = run_preflight(paths)
    assert missing_result["ready"] is False
    assert any(str(reason).startswith("persona_policy:") for reason in missing_result["blocking_reasons"])



def test_readiness_endpoint_returns_structure(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ROONIE_DASHBOARD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ROONIE_DASHBOARD_LOGS_DIR", str(tmp_path / "logs"))
    readiness_state = {
        "ready": True,
        "checked_at": "2026-02-13T00:00:00+00:00",
        "items": [{"name": "preflight", "ok": True, "detail": "ok"}],
        "blocking_reasons": [],
    }

    server, thread = _start_server(runs_dir, readiness_state)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        body = _get_json(base, "/api/system/readiness")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert set(body.keys()) == {"ready", "checked_at", "items", "blocking_reasons"}
    assert body["ready"] is True
    assert isinstance(body["items"], list)
    assert isinstance(body["blocking_reasons"], list)


def test_runtime_path_resolver_prefers_localappdata_on_windows(tmp_path: Path, monkeypatch) -> None:
    if os.name != "nt":
        return
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localapp"))
    monkeypatch.delenv("ROONIE_DASHBOARD_DATA_DIR", raising=False)
    monkeypatch.delenv("ROONIE_DASHBOARD_LOGS_DIR", raising=False)
    monkeypatch.delenv("ROONIE_DASHBOARD_RUNS_DIR", raising=False)

    paths = resolve_runtime_paths(repo_root=repo_root, runs_dir="runs", log_dir="logs")
    assert str(paths.runtime_root).startswith(str(tmp_path / "localapp"))
    assert paths.data_dir == paths.runtime_root / "data"
    assert paths.logs_dir == paths.runtime_root / "logs"


def test_safe_start_defaults_force_disarmed_output_disabled(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    data_dir = tmp_path / "data"
    logs_dir = tmp_path / "logs"
    monkeypatch.setenv("ROONIE_DASHBOARD_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ROONIE_DASHBOARD_LOGS_DIR", str(logs_dir))
    monkeypatch.setenv("ROONIE_KILL_SWITCH", "0")

    readiness_state = {
        "ready": True,
        "checked_at": "2026-02-13T00:00:00+00:00",
        "items": [{"name": "preflight", "ok": True, "detail": "ok"}],
        "blocking_reasons": [],
    }
    server, thread = _start_server(runs_dir, readiness_state)
    try:
        storage = getattr(server, "_roonie_storage")
        assert isinstance(storage, DashboardStorage)
        storage.set_armed(True)
        storage.silence_now(ttl_seconds=120)
        _apply_safe_start_defaults(storage)

        control_path = data_dir / "control_state.json"
        assert control_path.exists()
        payload = json.loads(control_path.read_text(encoding="utf-8"))
        assert payload.get("armed") is False
        assert payload.get("output_disabled") is True
        assert payload.get("silence_until") is None

        base = f"http://127.0.0.1:{server.server_address[1]}"
        status = _get_json(base, "/api/status")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert status["armed"] is False
    assert status["can_post"] is False
