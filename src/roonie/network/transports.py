from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from src.roonie.network.types import HttpResponse


@dataclass
class FakeTransport:
    """
    Fixture-backed transport for Phase 8C.
    Deterministic: loads responses from a known fixtures directory.
    """
    fixtures_dir: Path

    def get_json(self, url: str, *, fixture_name: Optional[str] = None) -> HttpResponse:
        if not fixture_name:
            raise ValueError("FakeTransport requires fixture_name for deterministic responses")

        p = self.fixtures_dir / fixture_name
        data = json.loads(p.read_text(encoding="utf-8-sig"))

        return HttpResponse(
            status=int(data["status"]),
            headers={k: str(v) for k, v in (data.get("headers") or {}).items()},
            body=data.get("body"),
        )

    def post_json(
        self,
        url: str,
        *,
        payload: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
        fixture_name: Optional[str] = None,
    ) -> HttpResponse:
        # Deterministic fixture behavior: request payload/headers are ignored in test mode.
        if not fixture_name:
            raise ValueError("FakeTransport requires fixture_name for deterministic responses")

        p = self.fixtures_dir / f"{fixture_name}.json"
        data = json.loads(p.read_text(encoding="utf-8-sig"))

        return HttpResponse(
            status=int(data["status"]),
            headers={k: str(v) for k, v in (data.get("headers") or {}).items()},
            body=data.get("body"),
        )
