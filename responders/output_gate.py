from __future__ import annotations

import os
import time
from typing import Any, Dict, List

_LAST_EMIT_TS = 0.0


def maybe_emit(decisions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    global _LAST_EMIT_TS

    outputs: List[Dict[str, Any]] = []
    if os.getenv("ROONIE_OUTPUT_DISABLED") == "1":
        for d in decisions:
            outputs.append(
                {
                    "event_id": d.get("event_id"),
                    "emitted": False,
                    "reason": "OUTPUT_DISABLED",
                    "sink": "stdout",
                }
            )
        return outputs

    now = time.time()
    allow_emit = (now - _LAST_EMIT_TS) >= 30.0

    for d in decisions:
        if d.get("action") != "RESPOND_PUBLIC":
            outputs.append(
                {
                    "event_id": d.get("event_id"),
                    "emitted": False,
                    "reason": "ACTION_NOT_ALLOWED",
                    "sink": "stdout",
                }
            )
            continue

        if not allow_emit:
            outputs.append(
                {
                    "event_id": d.get("event_id"),
                    "emitted": False,
                    "reason": "RATE_LIMIT",
                    "sink": "stdout",
                }
            )
            continue

        outputs.append(
            {
                "event_id": d.get("event_id"),
                "emitted": True,
                "reason": "EMITTED",
                "sink": "stdout",
            }
        )
        _LAST_EMIT_TS = now
        allow_emit = False

    return outputs
