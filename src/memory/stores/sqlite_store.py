import sqlite3
from pathlib import Path
from typing import Optional


_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_write_events (
  write_id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  memory_key TEXT NOT NULL,
  ttl_days INTEGER NOT NULL,
  memory_intent_json TEXT NOT NULL,
  raw_intent_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_items (
  subject_id TEXT NOT NULL,
  memory_key TEXT NOT NULL,
  ttl_days INTEGER NOT NULL,
  memory_intent_json TEXT NOT NULL,
  last_write_id TEXT NOT NULL,
  PRIMARY KEY (subject_id, memory_key)
);
"""


"""
Phase 7 Memory Persistence (WRITE-ONLY)

Tables:
- memory_write_events: append-only event log for MEMORY_WRITE_INTENT
- memory_items: current-state view keyed by (subject_id, memory_key)

Constraints:
- No reads required by caller
- No inference or timestamps
- Replay-safe via deterministic write_id
"""


class SqliteMemoryWriteStore:
    """
    Phase 7 local persistence store.
    - No reads required by caller
    - Idempotency handled by write_id primary key
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def write_event(
        self,
        *,
        write_id: str,
        case_id: str,
        event_id: str,
        subject_id: str,
        memory_key: str,
        ttl_days: int,
        memory_intent_json: str,
        raw_intent_json: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_write_events
                (write_id, case_id, event_id, subject_id, memory_key, ttl_days, memory_intent_json, raw_intent_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(write_id) DO NOTHING
                """,
                (write_id, case_id, event_id, subject_id, memory_key, ttl_days, memory_intent_json, raw_intent_json),
            )

    def upsert_item(
        self,
        *,
        subject_id: str,
        memory_key: str,
        ttl_days: int,
        memory_intent_json: str,
        last_write_id: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_items (subject_id, memory_key, ttl_days, memory_intent_json, last_write_id)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(subject_id, memory_key)
                DO UPDATE SET
                  ttl_days=excluded.ttl_days,
                  memory_intent_json=excluded.memory_intent_json,
                  last_write_id=excluded.last_write_id
                """,
                (subject_id, memory_key, ttl_days, memory_intent_json, last_write_id),
            )
