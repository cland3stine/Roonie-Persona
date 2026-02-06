from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.roonie.config import RoonieConfig


@dataclass(frozen=True)
class RooniePaths:
    base_dir: Path
    config_dir: Path
    data_dir: Path
    memory_db_path: Path


def resolve_paths(base_dir: Path | str, cfg: RoonieConfig) -> RooniePaths:
    """
    Phase 8B: Filesystem externalization (pure resolution).
    - No IO performed here (no directory creation).
    - Deterministic relative to provided base_dir.
    - Consumers must opt-in; no behavior changes by default.
    """
    base = Path(base_dir)
    config_dir = base / "config"
    data_dir = base / "data"

    memory_db = cfg.memory_db_path if cfg.memory_db_path is not None else (data_dir / "memory.sqlite")

    return RooniePaths(
        base_dir=base,
        config_dir=config_dir,
        data_dir=data_dir,
        memory_db_path=Path(memory_db),
    )
