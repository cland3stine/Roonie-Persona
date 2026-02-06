from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import os

try:
    import tomllib  # py3.11+
except Exception:  # pragma: no cover
    tomllib = None


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


@dataclass(repr=False)
class RoonieConfig:
    # Non-secret config
    memory_db_path: Optional[Path] = None
    network_enabled: bool = False

    # Secrets (placeholders; not used in Phase 8A)
    discogs_token: Optional[str] = field(default=None, repr=False)
    beatport_key: Optional[str] = field(default=None, repr=False)

    def __repr__(self) -> str:
        # Redact secrets explicitly
        return (
            "RoonieConfig("
            f"memory_db_path={self.memory_db_path!r}, "
            f"network_enabled={self.network_enabled!r}, "
            "discogs_token=<redacted>, "
            "beatport_key=<redacted>"
            ")"
        )


def load_config(base_dir: Path | str) -> RoonieConfig:
    """
    Phase 8A: Safe config/secrets boundary.
    Deterministic merge order:
      defaults < config/roonie.toml < config/secrets.env < environment
    """
    base = Path(base_dir)

    cfg = RoonieConfig()

    # 1) roonie.toml (non-secret)
    toml_path = base / "config" / "roonie.toml"
    if toml_path.exists():
        if tomllib is None:
            raise RuntimeError("tomllib not available; requires Python 3.11+")
        data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        mem = data.get("memory", {}) if isinstance(data, dict) else {}
        net = data.get("network", {}) if isinstance(data, dict) else {}

        dbp = mem.get("db_path")
        if isinstance(dbp, str) and dbp.strip():
            cfg.memory_db_path = (base / dbp).resolve()

        enabled = net.get("enabled")
        if isinstance(enabled, bool):
            cfg.network_enabled = enabled

    # 2) secrets.env (secret)
    secrets_path = base / "config" / "secrets.env"
    env_data = _parse_env_file(secrets_path)
    cfg.discogs_token = env_data.get("DISCOGS_TOKEN", cfg.discogs_token)
    cfg.beatport_key = env_data.get("BEATPORT_KEY", cfg.beatport_key)

    # 3) environment overrides (only via loader)
    mem_override = os.getenv("ROONIE_MEMORY_DB_PATH")
    if mem_override:
        cfg.memory_db_path = (base / mem_override).resolve()

    return cfg
