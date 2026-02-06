from pathlib import Path
import os

def test_default_config_loads_without_files(tmp_path, monkeypatch):
    from src.roonie.config import load_config

    # Ensure env is clean for deterministic behavior
    monkeypatch.delenv("ROONIE_MEMORY_DB_PATH", raising=False)
    monkeypatch.delenv("DISCOGS_TOKEN", raising=False)
    monkeypatch.delenv("BEATPORT_KEY", raising=False)

    cfg = load_config(base_dir=tmp_path)

    assert cfg.network_enabled is False
    assert cfg.memory_db_path is None
    assert cfg.discogs_token is None
    assert cfg.beatport_key is None


def test_toml_overrides_non_secret_values(tmp_path, monkeypatch):
    from src.roonie.config import load_config

    monkeypatch.delenv("ROONIE_MEMORY_DB_PATH", raising=False)

    toml = tmp_path / "config" / "roonie.toml"
    toml.parent.mkdir(parents=True, exist_ok=True)
    toml.write_text(
        '[memory]\n'
        'db_path="data/memory.sqlite"\n'
        '\n'
        '[network]\n'
        'enabled=false\n',
        encoding="utf-8",
    )

    cfg = load_config(base_dir=tmp_path)

    assert cfg.memory_db_path == (tmp_path / "data" / "memory.sqlite")
    assert cfg.network_enabled is False


def test_secrets_env_loaded_but_not_exposed_in_repr(tmp_path, monkeypatch):
    from src.roonie.config import load_config

    secrets = tmp_path / "config" / "secrets.env"
    secrets.parent.mkdir(parents=True, exist_ok=True)
    secrets.write_text(
        "DISCOGS_TOKEN=supersecret\nBEATPORT_KEY=alsosecret\n",
        encoding="utf-8",
    )

    cfg = load_config(base_dir=tmp_path)

    assert cfg.discogs_token == "supersecret"
    assert cfg.beatport_key == "alsosecret"

    # Must not leak in repr / str
    s = repr(cfg)
    assert "supersecret" not in s
    assert "alsosecret" not in s


def test_env_overrides_work_only_through_loader(tmp_path, monkeypatch):
    from src.roonie.config import load_config

    monkeypatch.setenv("ROONIE_MEMORY_DB_PATH", "data/override.sqlite")
    cfg = load_config(base_dir=tmp_path)

    assert cfg.memory_db_path == (tmp_path / "data" / "override.sqlite")
