from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlencode, quote

from roonie.network import NetworkClient


def build_search_url(*, query: str, token: Optional[str], per_page: int = 5, page: int = 1) -> str:
    base = "https://api.discogs.com/database/search"
    params = {
        "q": query,
        "per_page": int(per_page),
        "page": int(page),
    }
    if token:
        params["token"] = token
    return f"{base}?{urlencode(params, quote_via=quote)}"


@dataclass
class DiscogsClient:
    """
    Phase 9B: Discogs API client (URL builder + network boundary call).
    - No live IO in tests (fixture_name required for FakeTransport)
    - Token appended as query param (keeps Transport interface unchanged)
    """
    net: NetworkClient
    token: Optional[str] = None

    def search(self, *, query: str, fixture_name: Optional[str] = None, per_page: int = 5, page: int = 1) -> Dict[str, Any]:
        url = build_search_url(query=query, token=self.token, per_page=per_page, page=page)
        body = self.net.get_json(url, fixture_name=fixture_name)
        return body if isinstance(body, dict) else {}
