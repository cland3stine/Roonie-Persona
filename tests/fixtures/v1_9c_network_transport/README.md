Phase 9C: urllib transport scaffold.

Rules:
- No live network calls in tests
- urllib transport must not accept fixture_name (prevents accidental use under tests)
- Real transport is only for manual runs with cfg.network_enabled=true
