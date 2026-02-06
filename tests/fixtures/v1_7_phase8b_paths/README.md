Phase 8B fixtures: filesystem path resolution contract.

Rules:
- Pure functions only (no IO required)
- Deterministic: paths resolved relative to provided base_dir
- No behavior changes: consumers must opt-in to using resolved paths
