Phase 7 fixtures: persistence of MEMORY_WRITE_INTENT only.

Rules:
- Consume only records where action == "MEMORY_WRITE_INTENT"
- No memory reads, no inference, no side effects besides writing the store
- Deterministic + replay-safe idempotent writes
- TTL is stored as ttl_days exactly as emitted (no expires_at computation; no timestamps exist in Phase 6 payload)
