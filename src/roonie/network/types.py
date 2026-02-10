from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Dict[str, str]
    body: Any  # json-like


class Transport(Protocol):
    def get_json(self, url: str, *, fixture_name: Optional[str] = None) -> HttpResponse:
        ...

    def post_json(
        self,
        url: str,
        *,
        payload: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
        fixture_name: Optional[str] = None,
    ) -> HttpResponse:
        ...
