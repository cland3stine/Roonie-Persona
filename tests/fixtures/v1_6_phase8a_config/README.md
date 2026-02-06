Phase 8A fixtures: configuration + secrets boundary.

Rules:
- No behavior changes
- No network usage
- Secrets may be loaded but must never be logged or returned in traces
- Deterministic merge order: defaults < roonie.toml < secrets.env < environment
