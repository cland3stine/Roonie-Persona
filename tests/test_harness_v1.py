from __future__ import annotations

import json
from pathlib import Path
import difflib
import unicodedata

import pytest

from roonie.harness import run_case

CONTEXT_DEFAULTS = {
    "context_active": False,
    "context_turns_used": 0,
}


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
        diffs = []
        expected_keys = sorted(k for k in e.keys() if k != "trace")
        for key in expected_keys:
            e_val = e.get(key, CONTEXT_DEFAULTS.get(key))
            a_val = a.get(key, CONTEXT_DEFAULTS.get(key))
            if key == "response_text" and isinstance(e_val, str) and isinstance(a_val, str):
                if norm_text(e_val) != norm_text(a_val):
                    diffs.append(key)
            else:
                if e_val != a_val:
                    diffs.append(key)

        trace_keys = ["gates", "policy", "routing"]
        trace_diffs = {}
        e_trace = e.get("trace", {})
        a_trace = a.get("trace", {})
        if not isinstance(e_trace, dict):
            e_trace = {}
        if not isinstance(a_trace, dict):
            a_trace = {}
        for section in trace_keys:
            e_sec = e_trace.get(section, {})
            a_sec = a_trace.get(section, {})
            if not isinstance(e_sec, dict):
                e_sec = {}
            if not isinstance(a_sec, dict):
                a_sec = {}
            sec_keys = sorted(e_sec.keys())
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
                e_sec = e_trace.get(section, {})
                a_sec = a_trace.get(section, {})
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


def _case_paths():
    bases = [
        Path("tests/fixtures/v1/cases"),
        Path("tests/fixtures/v1_1/cases"),
        Path("tests/fixtures/v1_2/cases"),
        Path("tests/fixtures/v1_4/cases"),
    ]
    paths = []
    for base in bases:
        paths.extend(base.glob("*.json"))
    return sorted(paths)


@pytest.mark.parametrize("case_path", _case_paths())
def test_cases(case_path: Path):
    data = json.loads(case_path.read_text(encoding="utf-8-sig"))
    case_id = data["case_id"]
    golden_dir = case_path.parents[1] / "golden"
    golden_path = golden_dir / f"{case_id}.expected.json"

    actual = run_case(str(case_path))
    expected = json.loads(golden_path.read_text(encoding="utf-8-sig"))

    assert_decisions_equal(expected, actual)
