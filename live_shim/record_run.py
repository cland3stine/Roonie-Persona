from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from roonie.offline_director import OfflineDirector
from roonie.types import Env, Event
from responders.output_gate import maybe_emit
from responders.stdout_responder import emit
from memory.intent_evaluator import evaluate_memory_intents
from adapters.twitch_output import TwitchOutputAdapter


def _git_head_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def _runs_output_dir() -> Path:
    configured = (
        (os.getenv("ROONIE_DASHBOARD_RUNS_DIR") or "").strip()
        or (os.getenv("ROONIE_RUNS_DIR") or "").strip()
    )
    if not configured:
        return Path("runs")
    path = Path(configured)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    return path


def run_payload(payload: dict, emit_outputs: bool = False) -> Path:
    session_id = payload["session_id"]
    inputs = payload["inputs"]
    fixture_hint = payload.get("fixture_hint")

    director = OfflineDirector()
    env = Env(offline=True)

    decisions = []
    for item in inputs:
        event = Event(
            event_id=item.get("event_id", ""),
            message=item.get("message", ""),
            actor=item.get("actor", "viewer"),
            metadata=item.get("metadata", {}),
        )
        decision = director.evaluate(event, env)
        decisions.append(decision.to_dict(exclude_defaults=True))
        decisions.extend(
            evaluate_memory_intents(
                {
                    "event_id": event.event_id,
                    "message": event.message,
                    "metadata": event.metadata,
                }
            )
        )

    output = {
        "schema_version": "run-v1",
        "session_id": session_id,
        "director_commit": _git_head_sha(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "inputs": inputs,
        "decisions": decisions,
    }
    if emit_outputs:
        outputs = maybe_emit(decisions)
        twitch_adapter = TwitchOutputAdapter()
        for output_rec, decision in zip(outputs, decisions):
            if output_rec.get("emitted") and decision.get("response_text"):
                emit(decision["response_text"])
                twitch_adapter.handle_output(
                    {
                        "type": decision.get("action"),
                        "event_id": decision.get("event_id"),
                        "response_text": decision.get("response_text"),
                    },
                    {"mode": "live"},
                )
        output["outputs"] = outputs
    if fixture_hint:
        output["fixture_hint"] = fixture_hint

    runs_dir = _runs_output_dir()
    runs_dir.mkdir(parents=True, exist_ok=True)
    out_path = runs_dir / f"{session_id}.json"
    out_path.write_text(json.dumps(output, indent=2, sort_keys=False), encoding="utf-8")
    return out_path


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python live_shim/record_run.py <input_json_path>")
        return 1

    input_path = Path(sys.argv[1])
    data = json.loads(input_path.read_text(encoding="utf-8-sig"))

    run_payload(data, emit_outputs=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
