import json
import hashlib
from typing import Any, Dict, Iterable


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _write_id(record: Dict[str, Any]) -> str:
    h = hashlib.sha256(_canonical_json(record).encode("utf-8")).hexdigest()
    return f"mw_{h}"


def persist_memory_write_intents(records: Iterable[Dict[str, Any]], store: Any) -> None:
    """
    Phase 7:
    - Consume ONLY action == "MEMORY_WRITE_INTENT"
    - No inference, no reads, no timestamps, no side effects besides persistence
    - Deterministic, replay-safe idempotency via write_id
    """
    intents = [r for r in records if r.get("action") == "MEMORY_WRITE_INTENT"]
    for rec in intents:
        trace = rec.get("trace") or {}
        mi = (trace.get("memory_intent") or {})
        # Minimal required fields (contract from v1_4 fixtures)
        required = ["scope", "user", "preference", "object", "ttl_days", "cue"]
        missing = [k for k in required if k not in mi]
        if missing:
            raise ValueError(f"MEMORY_WRITE_INTENT missing fields: {missing}")

        wid = _write_id(rec)
        raw = _canonical_json(rec)

        store.write_event(
            write_id=wid,
            case_id=rec.get("case_id", "") or "",
            event_id=rec.get("event_id", "") or "",
            subject_id=str(mi["user"]),
            memory_key=str(mi["object"]),
            ttl_days=int(mi["ttl_days"]),
            memory_intent_json=_canonical_json(mi),
            raw_intent_json=raw,
        )

        # Current-state upsert: key by (subject_id, memory_key)
        store.upsert_item(
            subject_id=str(mi["user"]),
            memory_key=str(mi["object"]),
            ttl_days=int(mi["ttl_days"]),
            memory_intent_json=_canonical_json(mi),
            last_write_id=wid,
        )
