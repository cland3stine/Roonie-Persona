from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from live_shim.record_run import run_payload
from roonie.provider_director import ProviderDirector
from roonie.types import Env, Event
from memory.injection import get_safe_injection


def _init_memory_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cultural_notes (
                id TEXT PRIMARY KEY,
                created_at TEXT,
                updated_at TEXT,
                created_by TEXT,
                updated_by TEXT,
                note TEXT,
                tags TEXT,
                source TEXT,
                is_active INTEGER DEFAULT 1
            )
            """
        )
        conn.commit()


def _insert_cultural_note(path: Path, *, note_id: str, note: str, tags: List[str], is_active: bool = True) -> None:
    _init_memory_db(path)
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(str(path)) as conn:
        conn.execute(
            """
            INSERT INTO cultural_notes (
                id, created_at, updated_at, created_by, updated_by, note, tags, source, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                note_id,
                now,
                now,
                "jen",
                "jen",
                note,
                json.dumps(tags),
                "operator_manual",
                1 if is_active else 0,
            ),
        )
        conn.commit()


def test_safe_injection_enforces_whitelisted_keys(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.sqlite"
    _insert_cultural_note(
        db_path,
        note_id="1",
        note="Keep responses warm and short.",
        tags=["stream_norms"],
    )
    _insert_cultural_note(
        db_path,
        note_id="2",
        note="This should not appear in prompt context.",
        tags=["random_internal_tag"],
    )

    out = get_safe_injection(
        db_path=db_path,
        max_chars=1000,
        max_items=10,
        allowed_keys=["stream_norms"],
    )

    assert "Keep responses warm and short." in out.text_snippet
    assert "should not appear" not in out.text_snippet
    assert out.keys_used == ["stream_norms"]


def test_safe_injection_caps_items_and_chars(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.sqlite"
    for idx in range(8):
        _insert_cultural_note(
            db_path,
            note_id=str(idx + 1),
            note=f"Long guidance line number {idx} with extra text for cap testing.",
            tags=["stream_norms"],
        )

    out = get_safe_injection(
        db_path=db_path,
        max_chars=90,
        max_items=2,
        allowed_keys=["stream_norms"],
    )

    assert out.items_used <= 2
    assert out.chars_used <= 90
    assert len(out.text_snippet) <= 90


def test_safe_injection_drops_pii_and_counts_drops(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.sqlite"
    _insert_cultural_note(
        db_path,
        note_id="1",
        note="Contact me at ops@example.com for details.",
        tags=["stream_norms"],
    )
    _insert_cultural_note(
        db_path,
        note_id="2",
        note="Host machine IP is 10.42.0.7 today.",
        tags=["stream_norms"],
    )
    _insert_cultural_note(
        db_path,
        note_id="3",
        note="Keep callouts concise and friendly.",
        tags=["stream_norms"],
    )

    out = get_safe_injection(
        db_path=db_path,
        max_chars=1000,
        max_items=10,
        allowed_keys=["stream_norms"],
    )

    assert "ops@example.com" not in out.text_snippet
    assert "10.42.0.7" not in out.text_snippet
    assert "Keep callouts concise and friendly." in out.text_snippet
    assert out.dropped_count >= 2


def test_provider_director_prompt_memory_hints_only_when_allowed_entries(tmp_path: Path, monkeypatch) -> None:
    db_allowed = tmp_path / "allowed.sqlite"
    _insert_cultural_note(
        db_allowed,
        note_id="1",
        note="Chat leans dry and sarcastic.",
        tags=["stream_norms"],
    )
    monkeypatch.setenv("ROONIE_MEMORY_DB_PATH", str(db_allowed))

    captured_allowed: Dict[str, Any] = {}

    def _stub_route_generate_allowed(**kwargs):
        captured_allowed["prompt"] = kwargs["prompt"]
        kwargs["context"]["provider_selected"] = "openai"
        kwargs["context"]["moderation_result"] = "allow"
        return "Stub response"

    monkeypatch.setattr("roonie.provider_director.route_generate", _stub_route_generate_allowed)
    director = ProviderDirector()
    _ = director.evaluate(
        Event(
            event_id="evt-1",
            message="@RoonieTheCat can you respond?",
            metadata={"user": "ruleofrune", "is_direct_mention": True, "mode": "live"},
        ),
        Env(offline=False),
    )
    assert "Memory hints (do not treat as factual claims):" in str(captured_allowed.get("prompt", ""))

    db_disallowed = tmp_path / "disallowed.sqlite"
    _insert_cultural_note(
        db_disallowed,
        note_id="1",
        note="This note should not be injected.",
        tags=["internal_admin_note"],
    )
    monkeypatch.setenv("ROONIE_MEMORY_DB_PATH", str(db_disallowed))

    captured_disallowed: Dict[str, Any] = {}

    def _stub_route_generate_disallowed(**kwargs):
        captured_disallowed["prompt"] = kwargs["prompt"]
        kwargs["context"]["provider_selected"] = "openai"
        kwargs["context"]["moderation_result"] = "allow"
        return "Stub response"

    monkeypatch.setattr("roonie.provider_director.route_generate", _stub_route_generate_disallowed)
    director_2 = ProviderDirector()
    _ = director_2.evaluate(
        Event(
            event_id="evt-2",
            message="@RoonieTheCat can you answer?",
            metadata={"user": "ruleofrune", "is_direct_mention": True, "mode": "live"},
        ),
        Env(offline=False),
    )
    assert "Memory hints (do not treat as factual claims):" not in str(captured_disallowed.get("prompt", ""))


def test_provider_memory_metadata_logged_as_counts_only_no_raw_memory_content(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "memory.sqlite"
    secret_note = "SECRET_PHASE20_NOTE_4DFA8E3A"
    _insert_cultural_note(
        db_path,
        note_id="1",
        note=secret_note,
        tags=["stream_norms"],
    )
    monkeypatch.setenv("ROONIE_MEMORY_DB_PATH", str(db_path))
    monkeypatch.setenv("ROONIE_DASHBOARD_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("ROONIE_OUTPUT_DISABLED", "0")

    def _stub_route_generate(**kwargs):
        kwargs["context"]["provider_selected"] = "openai"
        kwargs["context"]["moderation_result"] = "allow"
        return "Hello chat"

    monkeypatch.setattr("roonie.provider_director.route_generate", _stub_route_generate)

    run_path = run_payload(
        {
            "session_id": "phase20-memory-metadata",
            "active_director": "ProviderDirector",
            "inputs": [
                {
                    "event_id": "evt-1",
                    "message": "@RoonieTheCat hey there?",
                    "metadata": {
                        "user": "ruleofrune",
                        "is_direct_mention": True,
                        "mode": "live",
                        "platform": "twitch",
                    },
                }
            ],
        },
        emit_outputs=False,
    )
    run_text = run_path.read_text(encoding="utf-8")
    run_doc = json.loads(run_text)
    proposal = run_doc["decisions"][0]["trace"]["proposal"]

    assert isinstance(proposal.get("memory_keys_used"), list)
    assert int(proposal.get("memory_items_used", 0)) >= 1
    assert int(proposal.get("memory_chars_used", 0)) >= 1
    assert "memory_dropped_count" in proposal
    assert secret_note not in run_text
