from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest
from _pytest.tmpdir import TempPathFactory

ROOT = str(Path(__file__).resolve().parents[1])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Sandbox-safe temp root for pytest fixtures (tmp_path/tmpdir).
_TMP_ROOT = Path(ROOT) / ".tmp_pytest"
_TMP_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["TMPDIR"] = str(_TMP_ROOT)
os.environ["TEMP"] = str(_TMP_ROOT)
os.environ["TMP"] = str(_TMP_ROOT)
tempfile.tempdir = str(_TMP_ROOT)
_SENSITIVE_TMP_FILENAMES = {"secrets.env"}


def _safe_getbasetemp(self: TempPathFactory) -> Path:
    if self._basetemp is not None:
        return self._basetemp
    run = _TMP_ROOT / f"run-{os.getpid()}"
    run.mkdir(parents=True, exist_ok=True)
    self._basetemp = run.resolve()
    return self._basetemp


def _safe_mktemp(self: TempPathFactory, basename: str, numbered: bool = True) -> Path:
    basename = self._ensure_relative_to_basetemp(basename)
    root = self.getbasetemp()
    if not numbered:
        p = root / basename
        p.mkdir()
        return p
    i = 0
    while True:
        p = root / f"{basename}{i}"
        try:
            p.mkdir()
            return p
        except FileExistsError:
            i += 1


def _cleanup_sensitive_tmp_files(root: Path) -> int:
    if not isinstance(root, Path):
        root = Path(root)
    if not root.exists():
        return 0
    removed = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.name.lower() not in _SENSITIVE_TMP_FILENAMES:
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def pytest_configure(config) -> None:
    TempPathFactory.getbasetemp = _safe_getbasetemp
    TempPathFactory.mktemp = _safe_mktemp
    _cleanup_sensitive_tmp_files(_TMP_ROOT)


def pytest_sessionfinish(session, exitstatus) -> None:
    _cleanup_sensitive_tmp_files(_TMP_ROOT)


@pytest.fixture(autouse=True)
def _reset_provider_failover_state():
    """Reset circuit breaker and stub cooldown state between tests to prevent bleed."""
    from providers import router
    with router._CIRCUIT_LOCK:
        router._CIRCUIT_STATE.clear()
    router._STUB_LAST_SENT = 0.0
    yield
    with router._CIRCUIT_LOCK:
        router._CIRCUIT_STATE.clear()
    router._STUB_LAST_SENT = 0.0


_ENV_VARS_TO_ISOLATE = [
    "ROONIE_ENFORCE_SETUP_GATE",
    "ROONIE_ARMED",
    "ROONIE_ARM",
    "ROONIE_OUTPUT_DISABLED",
]


@pytest.fixture(autouse=True)
def _clean_leaked_env_vars():
    """Prevent env var leaks between tests.

    DashboardStorage.__init__ and _pin_setup_gate_launch_default() set env
    vars via os.environ directly (bypassing monkeypatch tracking). This
    fixture captures and restores them regardless of test outcome.
    """
    saved = {k: os.environ.get(k) for k in _ENV_VARS_TO_ISOLATE}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
