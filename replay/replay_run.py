from __future__ import annotations

import difflib
import json
import sys
import unicodedata
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

from roonie.offline_director import OfflineDirector
from roonie.types import Env, Event


def _diff_strings(label: str, expected: str, actual: str) -> str:
    expected_lines = expected.splitlines(keepends=True)
    actual_lines = actual.splitlines(keepends=True)
    diff = list(
        difflib.unified_diff(
            expected_lines,
            actual_lines,
            fromfile=f"expected:{label}",
            tofile=f"actual:{label}",
            lineterm="",
        )
    )
    if not diff:
        return ""
    if len(diff) > 30:
        diff = diff[:30] + ["... (diff truncated)"]
    return "\n".join(diff)


def norm_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    for ch in ("\u200b", "\u200c", "\u200d", "\ufeff"):
        s = s.replace(ch, "")
    s = s.replace("—", "-").replace("–", "-")
    return s


def assert_decisions_equal(expected, actual) -> None:
    if len(expected) != len(actual):
        raise AssertionError(
            f"Decision count mismatch: expected {len(expected)}, actual {len(actual)}"
        )

    for idx, (e, a) in enumerate(zip(expected, actual)):
        e = {k: v for k, v in e.items() if k != "case_id"}
        a = {k: v for k, v in a.items() if k != "case_id"}
        diffs = []
        all_keys = sorted(set(e.keys()) | set(a.keys()))
        for key in all_keys:
            e_val = e.get(key)
            a_val = a.get(key)
            if key == "response_text" and isinstance(e_val, str) and isinstance(a_val, str):
                if norm_text(e_val) != norm_text(a_val):
                    diffs.append(key)
            else:
                if e_val != a_val:
                    diffs.append(key)

        trace_keys = ["gates", "policy", "routing"]
        trace_diffs = {}
        for section in trace_keys:
            e_sec = e.get("trace", {}).get(section, {})
            a_sec = a.get("trace", {}).get(section, {})
            sec_keys = sorted(set(e_sec.keys()) | set(a_sec.keys()))
            sec_diffs = [k for k in sec_keys if e_sec.get(k) != a_sec.get(k)]
            if sec_diffs:
                trace_diffs[section] = sec_diffs

        if diffs or trace_diffs:
            parts = [f"Mismatch at index {idx}"]
            if diffs:
                parts.append(f"Top-level differing fields: {diffs}")
            if trace_diffs:
                for section, keys in trace_diffs.items():
                    parts.append(f"trace.{section} differing fields: {keys}")

            for key in diffs:
                e_val = e.get(key)
                a_val = a.get(key)
                parts.append(f"{key} expected={e_val!r} actual={a_val!r}")
                if isinstance(e_val, str) and isinstance(a_val, str):
                    if key == "response_text":
                        e_cmp = norm_text(e_val)
                        a_cmp = norm_text(a_val)
                        diff = _diff_strings(key, e_cmp, a_cmp)
                    else:
                        diff = _diff_strings(key, e_val, a_val)
                    if diff:
                        parts.append(diff)

            for section, keys in trace_diffs.items():
                e_sec = e.get("trace", {}).get(section, {})
                a_sec = a.get("trace", {}).get(section, {})
                for key in keys:
                    e_val = e_sec.get(key)
                    a_val = a_sec.get(key)
                    parts.append(
                        f"trace.{section}.{key} expected={e_val!r} actual={a_val!r}"
                    )
                    if isinstance(e_val, str) and isinstance(a_val, str):
                        diff = _diff_strings(f"trace.{section}.{key}", e_val, a_val)
                        if diff:
                            parts.append(diff)

            raise AssertionError("\n".join(parts))


def _load_golden(version: str, case_id: str) -> List[Dict[str, Any]]:
    path = Path("tests/fixtures") / version / "golden" / f"{case_id}.expected.json"
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _match_golden_by_event_id_index(
    version: str, inputs: List[Dict[str, Any]]
) -> Tuple[str, List[Dict[str, Any]]]:
    base = Path("tests/fixtures") / version / "golden"
    matches: List[Tuple[str, List[Dict[str, Any]]]] = []
    for path in sorted(base.glob("*.expected.json")):
        expected = json.loads(path.read_text(encoding="utf-8-sig"))
        if len(expected) != len(inputs):
            continue
        if all(
            exp.get("event_id") == inp.get("event_id")
            for exp, inp in zip(expected, inputs)
        ):
            case_id = path.stem.replace(".expected", "")
            matches.append((case_id, expected))

    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError("No matching golden for run inputs by event_id/index")
    raise ValueError(
        f"Multiple matching goldens for run inputs: {[m[0] for m in matches]}"
    )


def _validate_event_id_index(expected: List[Dict[str, Any]], inputs: List[Dict[str, Any]]) -> None:
    if len(expected) != len(inputs):
        raise ValueError(
            f"Golden/input length mismatch: expected {len(expected)}, inputs {len(inputs)}"
        )
    for idx, (exp, inp) in enumerate(zip(expected, inputs)):
        if exp.get("event_id") != inp.get("event_id"):
            raise ValueError(
                f"Golden/input event_id mismatch at index {idx}: "
                f"expected {exp.get('event_id')!r}, inputs {inp.get('event_id')!r}"
            )


def _run_director(inputs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    director = OfflineDirector()
    env = Env(offline=True)
    results = []
    for item in inputs:
        event = Event(
            event_id=item.get("event_id", ""),
            message=item.get("message", ""),
            actor=item.get("actor", "viewer"),
            metadata=item.get("metadata", {}),
        )
        decision = director.evaluate(event, env)
        results.append(asdict(decision))
    return results


def _extract_twitch_text(output: Dict[str, Any]) -> str | None:
    side_effects = output.get("side_effects", {})
    twitch = side_effects.get("twitch")
    if twitch is None:
        return None
    if isinstance(twitch, str):
        return twitch
    if isinstance(twitch, dict):
        for key in ("text", "message", "content"):
            val = twitch.get(key)
            if isinstance(val, str):
                return val
    return None


def _validate_replay_outputs(run_data: Dict[str, Any], expected: List[Dict[str, Any]]) -> None:
    outputs = run_data.get("outputs", [])
    if not outputs:
        return

    expected_by_event = {d.get("event_id"): d for d in expected}

    for output in outputs:
        twitch_text = _extract_twitch_text(output)
        if twitch_text is None:
            continue
        event_id = output.get("event_id")
        if event_id not in expected_by_event:
            raise AssertionError(f"Output event_id not found in expected: {event_id!r}")
        expected_text = expected_by_event[event_id].get("response_text")
        if isinstance(expected_text, str):
            if norm_text(expected_text) != norm_text(twitch_text):
                raise AssertionError(
                    f"Twitch output mismatch for {event_id!r}: "
                    f"expected {expected_text!r}, got {twitch_text!r}"
                )
        else:
            raise AssertionError(
                f"Twitch output present for {event_id!r} but expected response_text is {expected_text!r}"
            )


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: python replay/replay_run.py <run_file.json> <fixture_version>")
        return 1

    run_path = Path(sys.argv[1])
    version = sys.argv[2]

    run_data = json.loads(run_path.read_text(encoding="utf-8-sig"))
    if run_data.get("schema_version") != "run-v1":
        raise AssertionError("Unsupported run schema version")

    inputs = run_data.get("inputs", [])
    fixture_hint = run_data.get("fixture_hint")

    if fixture_hint:
        expected = _load_golden(version, fixture_hint)
        _validate_event_id_index(expected, inputs)
    else:
        _, expected = _match_golden_by_event_id_index(version, inputs)

    actual = _run_director(inputs)

    try:
        assert_decisions_equal(expected, actual)
        _validate_replay_outputs(run_data, expected)
    except AssertionError as exc:
        print("FAIL")
        print(exc)
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
