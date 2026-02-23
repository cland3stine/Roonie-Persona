from __future__ import annotations

import json
import os
import socket
import threading
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

from roonie.control_room.preflight import resolve_runtime_paths, run_preflight
from roonie.dashboard_api.app import _arg_parser as _dashboard_api_arg_parser
from roonie.dashboard_api.app import create_server
from roonie.dashboard_api.storage import DashboardStorage
from roonie.run_control_room import (
    _apply_safe_start_defaults,
    _arg_parser as _control_room_arg_parser,
    _load_secrets_env_into_process,
    main as run_control_room_main,
    _runtime_entry_root,
    _pin_setup_gate_launch_default,
)


def _get_json(base: str, path: str) -> Dict[str, Any]:
    headers = _with_auto_cookie(base, path)
    request = urllib.request.Request(
        f"{base}{path}",
        method="GET",
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=2.0) as response:
        return json.loads(response.read().decode("utf-8"))


def _start_server(runs_dir: Path, readiness_state: Dict[str, Any]):
    os.environ.setdefault("ROONIE_DASHBOARD_ART_PASSWORD", "art-pass-123")
    os.environ.setdefault("ROONIE_DASHBOARD_JEN_PASSWORD", "jen-pass-123")
    _SESSION_COOKIE_CACHE.clear()
    server = create_server(host="127.0.0.1", port=0, runs_dir=runs_dir, readiness_state=readiness_state)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    return server, thread


_AUTO_AUTH_GET_PATHS = {
    "/api/status",
    "/api/system/readiness",
}

_SESSION_COOKIE_CACHE: Dict[str, str] = {}


def _path_only(path: str) -> str:
    return str(urlparse(str(path or "")).path or "")


def _login_cookie(base: str) -> str:
    cached = _SESSION_COOKIE_CACHE.get(base)
    if cached:
        return cached
    payload = json.dumps({"username": "jen", "password": "jen-pass-123"}).encode("utf-8")
    request = urllib.request.Request(
        f"{base}/api/auth/login",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=2.0) as response:
            raw = str(response.headers.get("Set-Cookie", "")).strip()
    except urllib.error.HTTPError:
        return ""
    cookie = raw.split(";", 1)[0].strip() if raw else ""
    if cookie:
        _SESSION_COOKIE_CACHE[base] = cookie
    return cookie


def _with_auto_cookie(base: str, path: str) -> Dict[str, str]:
    if _path_only(path) not in _AUTO_AUTH_GET_PATHS:
        return {}
    cookie = _login_cookie(base)
    return {"Cookie": cookie} if cookie else {}


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


def test_preflight_seeds_twitch_primary_channel_from_env(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _write_persona_policy(
        repo_root / "persona" / "persona_policy.yaml",
        "version: 1\npersona: roonie\nsenses:\n  enabled: false\n",
    )
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localapp"))
    monkeypatch.setenv("TWITCH_CHANNEL", "RuleOfRune")
    monkeypatch.delenv("ROONIE_DASHBOARD_DATA_DIR", raising=False)
    monkeypatch.delenv("ROONIE_DASHBOARD_LOGS_DIR", raising=False)
    monkeypatch.delenv("ROONIE_DASHBOARD_RUNS_DIR", raising=False)

    paths = resolve_runtime_paths(repo_root=repo_root, runs_dir="runs", log_dir="logs")
    result = run_preflight(paths)
    assert result["ready"] is True

    twitch_cfg = json.loads((paths.data_dir / "twitch_config.json").read_text(encoding="utf-8"))
    assert twitch_cfg.get("primary_channel") == "ruleofrune"


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
    repo_root = tmp_path / "Program Files" / "RoonieControlRoom"
    repo_root.mkdir(parents=True, exist_ok=True)
    localapp = tmp_path / "localapp"
    monkeypatch.setenv("LOCALAPPDATA", str(localapp))
    monkeypatch.delenv("ROONIE_DASHBOARD_DATA_DIR", raising=False)
    monkeypatch.delenv("ROONIE_DASHBOARD_LOGS_DIR", raising=False)
    monkeypatch.delenv("ROONIE_DASHBOARD_RUNS_DIR", raising=False)

    paths = resolve_runtime_paths(repo_root=repo_root, runs_dir="runs", log_dir="logs")
    expected_root = (localapp / "RoonieControlRoom").resolve()
    assert paths.runtime_root == expected_root
    assert paths.data_dir == expected_root / "data"
    assert paths.logs_dir == expected_root / "logs"
    assert paths.runs_dir == expected_root / "runs"


def test_runtime_path_resolver_defaults_to_repo_root_when_not_program_files(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("ROONIE_DASHBOARD_DATA_DIR", raising=False)
    monkeypatch.delenv("ROONIE_DASHBOARD_LOGS_DIR", raising=False)
    monkeypatch.delenv("ROONIE_DASHBOARD_RUNS_DIR", raising=False)

    paths = resolve_runtime_paths(repo_root=repo_root, runs_dir="runs", log_dir="logs")
    assert paths.runtime_root == repo_root.resolve()
    assert paths.data_dir == repo_root.resolve() / "data"
    assert paths.logs_dir == repo_root.resolve() / "logs"


def test_runtime_entry_root_uses_cwd_when_not_frozen(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_root)
    monkeypatch.delattr(sys, "frozen", raising=False)

    assert _runtime_entry_root() == repo_root.resolve()


def test_runtime_entry_root_uses_executable_parent_when_frozen(tmp_path: Path, monkeypatch) -> None:
    exe_dir = tmp_path / "Program Files" / "RoonieControlRoom"
    exe_dir.mkdir(parents=True, exist_ok=True)
    exe_path = exe_dir / "RoonieControlRoom.exe"
    exe_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_path), raising=False)

    assert _runtime_entry_root() == exe_dir.resolve()


def test_pin_setup_gate_launch_default_sets_enforced_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("ROONIE_ENFORCE_SETUP_GATE", raising=False)
    monkeypatch.delenv("ROONIE_REQUIRE_SETUP_WIZARD", raising=False)

    result = _pin_setup_gate_launch_default()

    assert os.getenv("ROONIE_ENFORCE_SETUP_GATE") == "1"
    assert result == {"value": "1", "source": "launch_default"}
    os.environ.pop("ROONIE_ENFORCE_SETUP_GATE", None)


def test_pin_setup_gate_launch_default_respects_explicit_env(monkeypatch) -> None:
    monkeypatch.setenv("ROONIE_ENFORCE_SETUP_GATE", "0")
    monkeypatch.setenv("ROONIE_REQUIRE_SETUP_WIZARD", "1")

    result = _pin_setup_gate_launch_default()

    assert os.getenv("ROONIE_ENFORCE_SETUP_GATE") == "0"
    assert result == {"value": "0", "source": "explicit"}
    os.environ.pop("ROONIE_ENFORCE_SETUP_GATE", None)


def test_pin_setup_gate_launch_default_promotes_legacy_alias(monkeypatch) -> None:
    monkeypatch.delenv("ROONIE_ENFORCE_SETUP_GATE", raising=False)
    monkeypatch.setenv("ROONIE_REQUIRE_SETUP_WIZARD", "0")

    result = _pin_setup_gate_launch_default()

    assert os.getenv("ROONIE_ENFORCE_SETUP_GATE") == "0"
    assert result == {"value": "0", "source": "legacy_alias"}
    os.environ.pop("ROONIE_ENFORCE_SETUP_GATE", None)


def test_safe_start_defaults_force_disarmed_output_disabled(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    data_dir = tmp_path / "data"
    logs_dir = tmp_path / "logs"
    monkeypatch.setenv("ROONIE_DASHBOARD_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ROONIE_DASHBOARD_LOGS_DIR", str(logs_dir))
    monkeypatch.setenv("ROONIE_KILL_SWITCH", "0")
    monkeypatch.setenv("ROONIE_ENFORCE_SETUP_GATE", "0")

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


def test_twitch_output_enabled_tracks_active_state(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    data_dir = tmp_path / "data"
    logs_dir = tmp_path / "logs"
    monkeypatch.setenv("ROONIE_DASHBOARD_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ROONIE_DASHBOARD_LOGS_DIR", str(logs_dir))
    monkeypatch.setenv("ROONIE_KILL_SWITCH", "0")
    monkeypatch.setenv("ROONIE_ENFORCE_SETUP_GATE", "0")

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
        _apply_safe_start_defaults(storage)
        assert os.getenv("TWITCH_OUTPUT_ENABLED") == "0"
        state_armed = storage.set_armed(True)
        assert state_armed["armed"] is True
        assert os.getenv("TWITCH_OUTPUT_ENABLED") == "1"
        state_disarmed = storage.set_armed(False)
        assert state_disarmed["armed"] is False
        assert os.getenv("TWITCH_OUTPUT_ENABLED") == "0"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def test_run_control_room_refuses_start_when_port_already_in_use(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _write_persona_policy(
        repo_root / "persona" / "persona_policy.yaml",
        "version: 1\npersona: roonie\nsenses:\n  enabled: false\n",
    )

    monkeypatch.chdir(repo_root)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localapp"))
    monkeypatch.delenv("ROONIE_DASHBOARD_DATA_DIR", raising=False)
    monkeypatch.delenv("ROONIE_DASHBOARD_LOGS_DIR", raising=False)
    monkeypatch.delenv("ROONIE_DASHBOARD_RUNS_DIR", raising=False)
    monkeypatch.delenv("ROONIE_PROVIDERS_CONFIG_PATH", raising=False)
    monkeypatch.delenv("ROONIE_ROUTING_CONFIG_PATH", raising=False)

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = int(listener.getsockname()[1])
    try:
        rc = run_control_room_main(
            [
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--runs-dir",
                "runs",
                "--log-dir",
                "logs",
            ]
        )
    finally:
        listener.close()
    assert rc == 3


def test_dashboard_host_defaults_to_all_interfaces(monkeypatch) -> None:
    monkeypatch.delenv("ROONIE_DASHBOARD_HOST", raising=False)
    control_args = _control_room_arg_parser().parse_args([])
    dashboard_args = _dashboard_api_arg_parser().parse_args([])
    assert str(control_args.host) == "0.0.0.0"
    assert str(dashboard_args.host) == "0.0.0.0"


def test_load_secrets_env_into_process_sets_missing_values(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / "secrets.env"
    env_file.write_text(
        "\n".join(
            [
                "# comment",
                "OPENAI_MODEL=gpt-5.2",
                'ROONIE_DIRECTOR_MODEL="gpt-5.2"',
                "GROK_MODEL='grok-4-1-fast-reasoning'",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("ROONIE_DIRECTOR_MODEL", raising=False)
    monkeypatch.delenv("GROK_MODEL", raising=False)

    stats = _load_secrets_env_into_process(env_file, override_existing=False)

    assert stats["exists"] is True
    assert stats["loaded"] == 3
    assert stats["set"] == 3
    assert os.getenv("OPENAI_MODEL") == "gpt-5.2"
    assert os.getenv("ROONIE_DIRECTOR_MODEL") == "gpt-5.2"
    assert os.getenv("GROK_MODEL") == "grok-4-1-fast-reasoning"


def test_load_secrets_env_into_process_does_not_override_existing(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / "secrets.env"
    env_file.write_text(
        "\n".join(
            [
                "OPENAI_MODEL=gpt-5.2",
                "ROONIE_DIRECTOR_MODEL=gpt-5.2",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_MODEL", "custom-openai-model")
    monkeypatch.delenv("ROONIE_DIRECTOR_MODEL", raising=False)

    stats = _load_secrets_env_into_process(env_file, override_existing=False)

    assert stats["loaded"] == 2
    assert stats["set"] == 1
    assert stats["skipped_existing"] == 1
    assert os.getenv("OPENAI_MODEL") == "custom-openai-model"
    assert os.getenv("ROONIE_DIRECTOR_MODEL") == "gpt-5.2"


def test_load_secrets_env_force_keys_override_existing(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / "secrets.env"
    env_file.write_text(
        "\n".join(
            [
                "GROK_MODEL=grok-4-1-fast-reasoning",
                "OPENAI_MODEL=gpt-5.2",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GROK_MODEL", "grok-4-1-fast-non-reasoning")
    monkeypatch.setenv("OPENAI_MODEL", "other-model")

    stats = _load_secrets_env_into_process(
        env_file,
        override_existing=False,
        force_keys={"GROK_MODEL"},
    )

    assert stats["loaded"] == 2
    assert stats["set"] == 1
    assert stats["forced_override"] == 1
    assert stats["skipped_existing"] == 1
    assert os.getenv("GROK_MODEL") == "grok-4-1-fast-reasoning"
    assert os.getenv("OPENAI_MODEL") == "other-model"


def test_no_bare_python_run_control_room_spawn_in_source() -> None:
    root = Path(__file__).resolve().parents[1]
    scan_roots = [root / "src", root / "scripts"]
    banned = (
        "python -m roonie.run_control_room",
        "python.exe -m roonie.run_control_room",
    )
    for scan_root in scan_roots:
        if not scan_root.exists():
            continue
        for path in scan_root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in {".py", ".ps1", ".bat", ".cmd"}:
                continue
            text = path.read_text(encoding="utf-8-sig")
            lowered = text.lower()
            for needle in banned:
                assert needle not in lowered, f"Found bare python launcher in {path}: {needle}"
