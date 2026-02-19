from __future__ import annotations

from pathlib import Path

import conftest


def test_cleanup_sensitive_tmp_files_removes_secrets_env(tmp_path: Path) -> None:
    root = tmp_path / "tmp-root"
    nested = root / "nested"
    nested.mkdir(parents=True, exist_ok=True)
    secrets_file = nested / "secrets.env"
    keep_file = nested / "note.txt"
    secrets_file.write_text("TWITCH_OAUTH_TOKEN=oauth:abc", encoding="utf-8")
    keep_file.write_text("keep", encoding="utf-8")

    removed = conftest._cleanup_sensitive_tmp_files(root)

    assert removed == 1
    assert not secrets_file.exists()
    assert keep_file.exists()


def test_cleanup_sensitive_tmp_files_handles_missing_root() -> None:
    removed = conftest._cleanup_sensitive_tmp_files(Path("D:/ROONIE/.tmp_pytest/does-not-exist-phase22"))
    assert removed == 0

