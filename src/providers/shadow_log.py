from __future__ import annotations

import json
from dataclasses import dataclass

SCHEMA_MARKER = "[[SCHEMA]]"
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def _utc_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ShadowLogConfig:
    path: Path
    # Filter mode: log full text only for flagged events/errors
    log_full_text_filtered: bool = True
    # "Odd latency" threshold in ms
    odd_latency_ms: int = 2500


def should_log_full_text(*, flags: Dict[str, Any], error: Optional[str], latency_ms: int) -> bool:
    if error:
        return True
    if bool(flags.get("sarcasm")) or bool(flags.get("mention")) or bool(flags.get("test_marker")):
        return True
    if latency_ms >= int(flags.get("odd_latency_ms", 0) or 0):
        return True
    return False


def append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def log_shadow(
    *,
    cfg: ShadowLogConfig,
    provider: str,
    event: str,
    prompt: str,
    context_flags: Dict[str, Any],
    latency_ms: int,
    error: Optional[str],
    output_text: Optional[str],
) -> None:
    flags = dict(context_flags or {})
    flags.setdefault("odd_latency_ms", cfg.odd_latency_ms)

    full_text_ok = should_log_full_text(flags=flags, error=error, latency_ms=latency_ms)
    record = {
        "ts": _utc_ts(),
        "provider": provider,
        "mode": "shadow",
        "event": event,
        "latency_ms": int(latency_ms),
        "error": error,
        "note": ("no_text_returned" if (error is None and output_text is None) else None),
        "flags": flags,
        "output": {
            "length_chars": len(output_text) if output_text is not None else 0,
            "text": (output_text if full_text_ok else None),
        },
    }
    append_jsonl(cfg.path, record)
