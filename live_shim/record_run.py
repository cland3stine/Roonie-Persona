from __future__ import annotations

import json
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from roonie.offline_director import OfflineDirector
from roonie.types import Env, Event
from responders.output_gate import maybe_emit
from responders.stdout_responder import emit


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


def run_payload(payload: dict) -> Path:
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
        decisions.append(asdict(decision))

    outputs = maybe_emit(decisions)
    for output, decision in zip(outputs, decisions):
        if output.get("emitted") and decision.get("response_text"):
            emit(decision["response_text"])

    output = {
        "schema_version": "run-v1",
        "session_id": session_id,
        "director_commit": _git_head_sha(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "inputs": inputs,
        "decisions": decisions,
        "outputs": outputs,
    }
    if fixture_hint:
        output["fixture_hint"] = fixture_hint

    out_path = Path("runs") / f"{session_id}.json"
    out_path.write_text(json.dumps(output, indent=2, sort_keys=False), encoding="utf-8")
    return out_path


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python live_shim/record_run.py <input_json_path>")
        return 1

    input_path = Path(sys.argv[1])
    data = json.loads(input_path.read_text(encoding="utf-8-sig"))

    run_payload(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
