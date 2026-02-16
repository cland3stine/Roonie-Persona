from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from roonie.config import RoonieConfig
from roonie.network.types import Transport


class NetworkDisabledError(RuntimeError):
    pass


@dataclass
class NetworkClient:
    """
    Phase 8C network boundary scaffold.

    - No real network calls here.
    - Transport must be injected (fake/replay).
    - Hard-gated by cfg.network_enabled.
    """
    cfg: RoonieConfig
    transport: Transport

    def get_json(self, url: str, *, fixture_name: Optional[str] = None):
        if not self.cfg.network_enabled:
            raise NetworkDisabledError("Network is disabled by configuration")
        resp = self.transport.get_json(url, fixture_name=fixture_name)
        return resp.body
