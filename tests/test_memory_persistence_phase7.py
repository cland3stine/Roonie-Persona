import json
import sqlite3
import hashlib
from pathlib import Path

import pytest


def _canonical_json(obj) -> str:
    # Deterministic JSON encoding for hashing/idempotency
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _write_id(record: dict) -> str:
    h = hashlib.sha256(_canonical_json(record).encode("utf-8")).hexdigest()
    return f"mw_{h}"


def _load_fixture(name: str):
    p = Path("tests/fixtures/v1_5_phase7") / name
    return json.loads(p.read_text(encoding="utf-8"))


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _read_all(conn: sqlite3.Connection, sql: str, args=()):
    cur = conn.execute(sql, args)
    return [dict(r) for r in cur.fetchall()]


@pytest.fixture()
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "memory_phase7.sqlite"


def test_persist_writes_only_memory_write_intent(tmp_db):
    # Import here so tests fail until Phase 7 code exists
    from src.memory.persistence import persist_memory_write_intents
    from src.memory.stores.sqlite_store import SqliteMemoryWriteStore

    records = _load_fixture("intents_basic.json")

    store = SqliteMemoryWriteStore(tmp_db)
    persist_memory_write_intents(records, store)

    conn = _connect(tmp_db)

    events = _read_all(conn, "SELECT * FROM memory_write_events ORDER BY write_id")
    items = _read_all(conn, "SELECT * FROM memory_items ORDER BY subject_id, memory_key")

    # Only the MEMORY_WRITE_INTENT record should be persisted
    assert len(events) == 1
    assert len(items) == 1

    # Event contains canonical raw JSON for the intent record
    intent = [r for r in records if r.get("action") == "MEMORY_WRITE_INTENT"][0]
    assert events[0]["write_id"] == _write_id(intent)

    # Keying: subject=user, key=object
    mi = intent["trace"]["memory_intent"]
    assert items[0]["subject_id"] == mi["user"]
    assert items[0]["memory_key"] == mi["object"]

    # Value stored is the memory_intent payload (canonical JSON)
    assert json.loads(items[0]["memory_intent_json"]) == mi

    # TTL stored as emitted (no expires_at computation in Phase 7)
    assert items[0]["ttl_days"] == mi["ttl_days"]


def test_persist_is_idempotent_on_replay(tmp_db):
    from src.memory.persistence import persist_memory_write_intents
    from src.memory.stores.sqlite_store import SqliteMemoryWriteStore

    records = _load_fixture("intents_basic.json")

    store = SqliteMemoryWriteStore(tmp_db)
    persist_memory_write_intents(records, store)
    persist_memory_write_intents(records, store)  # replay

    conn = _connect(tmp_db)
    events = _read_all(conn, "SELECT * FROM memory_write_events")
    items = _read_all(conn, "SELECT * FROM memory_items")

    assert len(events) == 1
    assert len(items) == 1


def test_overwrite_updates_current_item_and_keeps_event_log(tmp_db):
    from src.memory.persistence import persist_memory_write_intents
    from src.memory.stores.sqlite_store import SqliteMemoryWriteStore

    records = _load_fixture("intents_overwrite.json")
    store = SqliteMemoryWriteStore(tmp_db)
    persist_memory_write_intents(records, store)

    conn = _connect(tmp_db)
    events = _read_all(conn, "SELECT * FROM memory_write_events ORDER BY rowid")
    items = _read_all(conn, "SELECT * FROM memory_items")

    # Both events are preserved
    assert len(events) == 2
    assert len(items) == 1

    first = records[0]
    second = records[1]

    # Current item should reflect the second (overwrite) intent
    mi2 = second["trace"]["memory_intent"]
    assert items[0]["subject_id"] == mi2["user"]
    assert items[0]["memory_key"] == mi2["object"]
    assert json.loads(items[0]["memory_intent_json"]) == mi2
    assert items[0]["ttl_days"] == mi2["ttl_days"]

    # Ensure overwrite marker is preserved
    assert mi2.get("reason") == "overwrite"
