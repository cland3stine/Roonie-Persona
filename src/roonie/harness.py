from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from .offline_director import OfflineDirector
from memory.intent_evaluator import evaluate_memory_intents
from .types import DecisionRecord, Env, Event


SCORABLE_FIELDS = {
    "case_id",
    "event_id",
    "action",
    "route",
    "response_text",
    "trace",
}


def _normalize_decision(decision: DecisionRecord) -> Dict[str, Any]:
    return {
        "case_id": decision.case_id,
        "event_id": decision.event_id,
        "action": decision.action,
        "route": decision.route,
        "response_text": decision.response_text,
        "trace": decision.trace,
    }


def run_case(case_json_path: str) -> List[Dict[str, Any]]:
    path = Path(case_json_path)
    data = json.loads(path.read_text(encoding="utf-8-sig"))

    case_id = data["case_id"]
    env = Env(offline=True)
    director = OfflineDirector()

    decisions: List[Dict[str, Any]] = []
    for item in data["events"]:
        event = Event(
            event_id=item["event_id"],
            message=item["message"],
            actor=item.get("actor", "viewer"),
            metadata={"case_id": case_id, **item.get("metadata", {})},
        )
        decision = director.evaluate(event, env)
        decisions.append(_normalize_decision(decision))
        memory_intents = evaluate_memory_intents(
            {
                "case_id": case_id,
                "event_id": event.event_id,
                "message": event.message,
                "metadata": event.metadata,
            }
        )
        decisions.extend(memory_intents)

    return decisions


def compare_to_golden(case_json_path: str, golden_json_path: str) -> Dict[str, Any]:
    actual = run_case(case_json_path)
    expected = json.loads(Path(golden_json_path).read_text(encoding="utf-8-sig"))
    return {"actual": actual, "expected": expected, "match": actual == expected}
