from __future__ import annotations

import sys


def emit(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.write("\n")
    sys.stdout.flush()
