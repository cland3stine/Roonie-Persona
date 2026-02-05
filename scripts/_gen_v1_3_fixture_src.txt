from __future__ import annotations

import json
from pathlib import Path

from roonie.harness import run_case


def main() -> None:
    base = Path("tests/fixtures/v1_3")
    cases_dir = base / "cases"
    golden_dir = base / "golden"
    runs_dir = base / "runs"
    cases_dir.mkdir(parents=True, exist_ok=True)
    golden_dir.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)

    case_id = "P1-0301_v1_3_gear_question"
    case_path = cases_dir / f"{case_id}.json"
    case_obj = {
        "case_id": case_id,
        "events": [
            {
                "event_id": "evt-1",
                "message": "@roonie what camera are you using?",
                "metadata": {"is_direct_mention": True},
            }
        ],
    }
    case_path.write_text(json.dumps(case_obj, indent=2, sort_keys=False), encoding="utf-8")

    expected = run_case(str(case_path))
    expected_path = golden_dir / f"{case_id}.expected.json"
    expected_path.write_text(
        json.dumps(expected, indent=2, sort_keys=False), encoding="utf-8"
    )

    run_payload = {
        "schema_version": "run-v1",
        "session_id": "twitch-20260205T000000Z",
        "director_commit": "TEST",
        "started_at": "2026-02-05T00:00:00+00:00",
        "fixture_hint": case_id,
        "inputs": [
            {
                "event_id": "evt-1",
                "message": "@roonie what camera are you using?",
                "metadata": {"is_direct_mention": True},
            }
        ],
        "decisions": expected,
        "outputs": [
            {
                "event_id": "evt-1",
                "emitted": True,
                "sink": "stdout",
                "side_effects": {
                    "twitch": {
                        "attempted": True,
                        "sent": False,
                        "reason": "missing_credentials",
                    }
                },
            }
        ],
    }

    run_path = runs_dir / f"{case_id}.run.json"
    run_path.write_text(
        json.dumps(run_payload, indent=2, sort_keys=False), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
