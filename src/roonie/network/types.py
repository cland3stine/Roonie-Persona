from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Protocol, Optional


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Dict[str, str]
    body: Any  # json-like


class Transport(Protocol):
    def get_json(self, url: str, *, fixture_name: Optional[str] = None) -> HttpResponse:
        ...
