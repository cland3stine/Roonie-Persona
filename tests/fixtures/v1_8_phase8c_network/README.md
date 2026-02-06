Phase 8C fixtures: controlled network boundary scaffold.

Rules:
- No real network calls in tests
- Standard library only (no requests/httpx/aiohttp)
- Network is disabled by default and must be explicitly enabled via config
- Transport is injected (fake/replay-safe)
