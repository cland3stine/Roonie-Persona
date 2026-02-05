from __future__ import annotations

import json
from pathlib import Path

import pytest

from roonie.harness import run_case


def _case_paths():
    bases = [
        Path("tests/fixtures/v1/cases"),
        Path("tests/fixtures/v1_1/cases"),
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

    assert actual == expected
