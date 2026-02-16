from __future__ import annotations

import os
import sys
from pathlib import Path


def _ensure_src_on_path() -> None:
    # Running this file directly should not shadow stdlib modules (e.g. types),
    # so we add repo `src/` to sys.path instead of invoking `-m ...`.
    repo_root = Path(__file__).resolve().parent
    src_dir = (repo_root / "src").resolve()
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    os.environ.setdefault("PYTHONPATH", str(src_dir))


def main(argv: list[str] | None = None) -> int:
    _ensure_src_on_path()
    from roonie.run_control_room import main as _main

    return int(_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())

