from __future__ import annotations

import json
from pathlib import Path

from roonie.dashboard_api.app import create_server
from roonie.dashboard_api.storage import DashboardStorage, hash_password, verify_password


def _set_dashboard_paths(monkeypatch, tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("ROONIE_DASHBOARD_LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("ROONIE_DASHBOARD_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(data_dir / "providers_config.json"))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(data_dir / "routing_config.json"))
    return data_dir


def test_password_hash_verify_roundtrip() -> None:
    stored = hash_password("jen-pass-123")
    assert verify_password("jen-pass-123", stored) is True
    assert verify_password("wrong-pass", stored) is False


def test_legacy_auth_users_format_reseeds_automatically(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    data_dir = _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_DASHBOARD_ART_PASSWORD", "art-pass-123")
    monkeypatch.setenv("ROONIE_DASHBOARD_JEN_PASSWORD", "jen-pass-123")

    legacy_payload = {
        "version": 1,
        "users": [
            {
                "username": "art",
                "role": "director",
                "password_hash": "pbkdf2_sha256$200000$oldsalt$oldhash",
            },
            {
                "username": "jen",
                "role": "operator",
                "password_hash": "pbkdf2_sha256$200000$oldsalt$oldhash",
            },
        ],
    }
    data_dir.mkdir(parents=True, exist_ok=True)
    auth_path = data_dir / "auth_users.json"
    auth_path.write_text(json.dumps(legacy_payload), encoding="utf-8")

    server = create_server(host="127.0.0.1", port=0, runs_dir=runs_dir)
    try:
        storage = getattr(server, "_roonie_storage")
        payload = json.loads(auth_path.read_text(encoding="utf-8"))
        users = payload.get("users", [])
        assert isinstance(users, list)
        assert all("$" not in str(item.get("password_hash", "")) for item in users if isinstance(item, dict))
        assert storage.login_dashboard_user("art", "art-pass-123") is not None
        assert storage.login_dashboard_user("jen", "jen-pass-123") is not None
        assert storage.login_dashboard_user("art", "wrong-pass") is None
    finally:
        server.server_close()


def test_login_uses_resolved_storage_data_dir(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    data_dir = _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_DASHBOARD_ART_PASSWORD", "roonie")
    monkeypatch.setenv("ROONIE_DASHBOARD_JEN_PASSWORD", "jen-pass-123")

    # Guard against accidentally preferring a different runtime location.
    localapp = tmp_path / "localapp"
    monkeypatch.setenv("LOCALAPPDATA", str(localapp))
    wrong_auth_path = localapp / "RoonieControlRoom" / "data" / "auth_users.json"
    wrong_auth_path.parent.mkdir(parents=True, exist_ok=True)
    wrong_auth_path.write_text(
        json.dumps(
            {
                "version": 1,
                "users": [
                    {"username": "art", "role": "director", "password_hash": "invalid"},
                    {"username": "jen", "role": "operator", "password_hash": "invalid"},
                ],
            }
        ),
        encoding="utf-8",
    )

    storage = DashboardStorage(runs_dir=runs_dir)
    expected_auth_path = (data_dir / "auth_users.json").resolve()
    assert storage.auth_users_path == expected_auth_path
    assert expected_auth_path.exists()
    assert storage.login_dashboard_user("art", "roonie") is not None
    assert storage.login_dashboard_user("jen", "jen-pass-123") is not None
    assert storage.login_dashboard_user("art", "wrong-pass") is None


def test_validate_operator_key_accepts_valid_value(monkeypatch) -> None:
    monkeypatch.setenv("ROONIE_OPERATOR_KEY", "op-key-123")
    ok, msg = DashboardStorage.validate_operator_key("op-key-123")
    assert ok is True
    assert msg == "ok"


def test_validate_operator_key_rejects_invalid_value(monkeypatch) -> None:
    monkeypatch.setenv("ROONIE_OPERATOR_KEY", "op-key-123")
    ok, msg = DashboardStorage.validate_operator_key("wrong-key")
    assert ok is False
    assert msg == "Forbidden: invalid X-ROONIE-OP-KEY."


def test_validate_operator_key_rejects_empty_or_none(monkeypatch) -> None:
    monkeypatch.setenv("ROONIE_OPERATOR_KEY", "op-key-123")
    ok_empty, msg_empty = DashboardStorage.validate_operator_key("")
    ok_none, msg_none = DashboardStorage.validate_operator_key(None)
    assert ok_empty is False
    assert ok_none is False
    assert msg_empty == "Forbidden: invalid X-ROONIE-OP-KEY."
    assert msg_none == "Forbidden: invalid X-ROONIE-OP-KEY."


def test_validate_operator_key_read_only_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("ROONIE_OPERATOR_KEY", raising=False)
    ok, msg = DashboardStorage.validate_operator_key("anything")
    assert ok is False
    assert msg == "API is READ-ONLY: set ROONIE_OPERATOR_KEY to enable write actions."
