from __future__ import annotations

import copy
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from roonie.offline_director import OfflineDirector
from roonie.provider_director import ProviderDirector
from roonie.types import Env, Event
from responders.output_gate import maybe_emit
from responders.stdout_responder import emit
from responders.typing_delay import compute_typing_delay
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
        return ROOT / "runs"
    path = Path(configured)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    return path


def _normalize_director_name(value: str) -> str:
    text = str(value or "").strip().lower()
    if text in {"providerdirector", "provider", "live"}:
        return "ProviderDirector"
    if text in {"offlinedirector", "offline"}:
        return "OfflineDirector"
    return "ProviderDirector"


def _is_live_payload(payload: dict) -> bool:
    inputs = payload.get("inputs", [])
    if not isinstance(inputs, list) or not inputs:
        return False
    first = inputs[0] if isinstance(inputs[0], dict) else {}
    metadata = first.get("metadata", {}) if isinstance(first, dict) else {}
    if not isinstance(metadata, dict):
        metadata = {}
    mode = str(metadata.get("mode", "")).strip().lower()
    platform = str(metadata.get("platform", "")).strip().lower()
    return mode == "live" or platform == "twitch"


def _selected_director(payload: dict) -> str:
    direct = str(payload.get("active_director", "")).strip()
    if direct:
        return _normalize_director_name(direct)
    inputs = payload.get("inputs", [])
    if isinstance(inputs, list) and inputs:
        first = inputs[0] if isinstance(inputs[0], dict) else {}
        metadata = first.get("metadata", {}) if isinstance(first, dict) else {}
        if isinstance(metadata, dict):
            hinted = str(metadata.get("active_director", "")).strip()
            if hinted:
                return _normalize_director_name(hinted)
    is_live = _is_live_payload(payload)
    if is_live:
        # Canon default for live payloads is ProviderDirector unless explicitly selected.
        return "ProviderDirector"
    env_name = str(os.getenv("ROONIE_ACTIVE_DIRECTOR", "")).strip()
    if env_name:
        return _normalize_director_name(env_name)
    return "OfflineDirector"


def _director_name_for_instance(director: Any) -> str:
    if isinstance(director, ProviderDirector):
        return "ProviderDirector"
    if isinstance(director, OfflineDirector):
        return "OfflineDirector"
    return ""


def _strip_model_metadata_from_decisions(decisions: list[dict]) -> list[dict]:
    # SEC-018: Remove model identifiers from persisted run artifacts.
    sanitized = copy.deepcopy(decisions)
    for decision in sanitized:
        if not isinstance(decision, dict):
            continue
        decision.pop("model_used", None)
        decision.pop("model_selected", None)
        decision.pop("provider_model", None)
        trace = decision.get("trace")
        if not isinstance(trace, dict):
            continue
        proposal = trace.get("proposal")
        if isinstance(proposal, dict):
            proposal.pop("model", None)
            proposal.pop("model_used", None)
            proposal.pop("active_model", None)
            proposal.pop("moderation_model_used", None)
        routing = trace.get("routing")
        if isinstance(routing, dict):
            routing.pop("model", None)
            routing.pop("model_selected", None)
            routing.pop("active_model", None)
            routing.pop("moderation_model_used", None)
    return sanitized


def _apply_output_feedback_to_director(*, director: Any, decisions: list[dict], outputs: list[dict]) -> None:
    if not hasattr(director, "apply_output_feedback"):
        return
    output_by_event: Dict[str, Dict[str, Any]] = {}
    for item in outputs:
        if not isinstance(item, dict):
            continue
        event_id = str(item.get("event_id", "")).strip()
        if event_id:
            output_by_event[event_id] = item
    for decision in decisions:
        if not isinstance(decision, dict):
            continue
        event_id = str(decision.get("event_id", "")).strip()
        if not event_id:
            continue
        output_item = output_by_event.get(event_id)
        emitted = bool(output_item.get("emitted", False)) if isinstance(output_item, dict) else False
        send_result = output_item.get("send_result") if isinstance(output_item, dict) else None
        try:
            director.apply_output_feedback(
                event_id=event_id,
                emitted=emitted,
                send_result=send_result if isinstance(send_result, dict) else None,
            )
        except Exception:
            pass


def _apply_default_feedback_to_director(*, director: Any, decisions: list[dict]) -> None:
    if not hasattr(director, "apply_output_feedback"):
        return
    for decision in decisions:
        if not isinstance(decision, dict):
            continue
        event_id = str(decision.get("event_id", "")).strip()
        if not event_id:
            continue
        action = str(decision.get("action", "")).strip().upper()
        emitted = action == "RESPOND_PUBLIC"
        try:
            director.apply_output_feedback(
                event_id=event_id,
                emitted=emitted,
                send_result={"sent": emitted},
            )
        except Exception:
            pass


def run_payload(
    payload: dict,
    emit_outputs: bool = False,
    *,
    director_instance: Any = None,
    env_instance: Env | None = None,
) -> Path:
    session_id = payload["session_id"]
    inputs = payload["inputs"]
    fixture_hint = payload.get("fixture_hint")

    selected_active_director = _selected_director(payload)
    if director_instance is None:
        director = (
            ProviderDirector()
            if selected_active_director == "ProviderDirector"
            else OfflineDirector()
        )
        active_director = selected_active_director
    else:
        director = director_instance
        active_director = _director_name_for_instance(director) or selected_active_director

    env = env_instance if env_instance is not None else Env(offline=(not _is_live_payload(payload)))

    decisions = []
    for item in inputs:
        metadata = item.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        metadata = dict(metadata)
        metadata.setdefault("session_id", session_id)
        event = Event(
            event_id=item.get("event_id", ""),
            message=item.get("message", ""),
            actor=item.get("actor", "viewer"),
            metadata=metadata,
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

    # Strip inner_circle from persisted inputs to avoid identity leakage (SEC-006).
    sanitized_inputs = copy.deepcopy(inputs)
    for inp in sanitized_inputs:
        if isinstance(inp, dict):
            meta = inp.get("metadata")
            if isinstance(meta, dict):
                meta.pop("inner_circle", None)
    sanitized_decisions = _strip_model_metadata_from_decisions(decisions)

    output = {
        "schema_version": "run-v1",
        "session_id": session_id,
        "director_commit": _git_head_sha(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "active_director": active_director,
        "inputs": sanitized_inputs,
        "decisions": sanitized_decisions,
    }
    if emit_outputs:
        outputs = maybe_emit(decisions)
        twitch_adapter = TwitchOutputAdapter()
        for output_rec, decision in zip(outputs, decisions):
            trace = decision.get("trace", {}) if isinstance(decision, dict) else {}
            proposal = trace.get("proposal", {}) if isinstance(trace, dict) else {}
            proposal_session = proposal.get("session_id") if isinstance(proposal, dict) else None
            output_rec["session_id"] = str(proposal_session or session_id).strip() or session_id
            if output_rec.get("emitted") and decision.get("response_text"):
                delay = compute_typing_delay(decision["response_text"])
                if delay > 0:
                    time.sleep(delay)
                output_rec["typing_delay_seconds"] = round(delay, 2)
                emit(decision["response_text"])
                send_result = twitch_adapter.handle_output(
                    {
                        "type": decision.get("action"),
                        "event_id": decision.get("event_id"),
                        "response_text": decision.get("response_text"),
                    },
                    {"mode": "live"},
                )
                output_rec["send_result"] = send_result
        output["outputs"] = outputs
        _apply_output_feedback_to_director(director=director, decisions=decisions, outputs=outputs)
    else:
        _apply_default_feedback_to_director(director=director, decisions=decisions)
    if fixture_hint:
        output["fixture_hint"] = fixture_hint

    runs_dir = _runs_output_dir()
    runs_dir.mkdir(parents=True, exist_ok=True)
    if _is_live_payload(payload) and inputs:
        first_event_id = str(inputs[0].get("event_id", "")).strip()
        if first_event_id:
            safe_id = first_event_id.replace(":", "-")
            out_path = runs_dir / f"{session_id}_{safe_id}.json"
        else:
            out_path = runs_dir / f"{session_id}_{int(time.time() * 1000)}.json"
    else:
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
