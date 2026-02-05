from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live_shim import record_run


def main() -> int:
    parser = argparse.ArgumentParser(description="Record a run from NDJSON stdin")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--fixture-hint", required=False)
    args = parser.parse_args()

    events = []
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        events.append(json.loads(line))

    payload = {
        "session_id": args.session_id,
        "inputs": events,
    }
    if args.fixture_hint:
        payload["fixture_hint"] = args.fixture_hint

    record_run.run_payload(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
