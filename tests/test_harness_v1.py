from __future__ import annotations

import json
from pathlib import Path

import pytest

from roonie.harness import run_case


def _case_paths():
    base = Path("tests/fixtures/v1/cases")
    return sorted(base.glob("*.json"))


@pytest.mark.parametrize("case_path", _case_paths())
def test_cases(case_path: Path):
    data = json.loads(case_path.read_text(encoding="utf-8-sig"))
    case_id = data["case_id"]
    golden_path = Path("tests/fixtures/v1/golden") / f"{case_id}.expected.json"

    actual = run_case(str(case_path))
    expected = json.loads(golden_path.read_text(encoding="utf-8-sig"))

    assert actual == expected
