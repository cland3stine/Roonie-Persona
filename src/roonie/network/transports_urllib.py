from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.roonie.network.types import HttpResponse


@dataclass
class UrllibJsonTransport:
    """
    Phase 9C: real HTTP transport (manual-use only).
    - Standard library only (urllib)
    - Tests must never call live network: fixture_name is rejected
    """
    user_agent: str
    timeout_seconds: int = 10

    def get_json(self, url: str, *, fixture_name: Optional[str] = None) -> HttpResponse:
        if fixture_name is not None:
            raise ValueError("UrllibJsonTransport does not support fixture_name; use FakeTransport in tests")

        req = Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json",
            },
            method="GET",
        )

        try:
            with urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                try:
                    body: Any = json.loads(raw) if raw else None
                except json.JSONDecodeError:
                    body = raw

                # urllib headers object -> plain dict
                headers: Dict[str, str] = {k.lower(): v for k, v in resp.headers.items()}
                return HttpResponse(status=int(resp.status), headers=headers, body=body)

        except HTTPError as e:
            raw = ""
            try:
                raw = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            body = None
            try:
                body = json.loads(raw) if raw else None
            except Exception:
                body = raw or None
            headers = {k.lower(): v for k, v in getattr(e, "headers", {}).items()} if getattr(e, "headers", None) else {}
            return HttpResponse(status=int(getattr(e, "code", 0) or 0), headers=headers, body=body)

        except URLError as e:
            return HttpResponse(status=0, headers={}, body={"error": "urlerror", "reason": str(e)})

    def post_json(
        self,
        url: str,
        *,
        payload: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
        fixture_name: Optional[str] = None,
    ) -> HttpResponse:
        if fixture_name is not None:
            raise ValueError("UrllibJsonTransport does not support fixture_name; use FakeTransport in tests")

        body_bytes = json.dumps(payload).encode("utf-8")
        req_headers: Dict[str, str] = {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if headers:
            for key, value in headers.items():
                req_headers[str(key)] = str(value)

        req = Request(url, headers=req_headers, data=body_bytes, method="POST")

        try:
            with urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                try:
                    body: Any = json.loads(raw) if raw else None
                except json.JSONDecodeError:
                    body = raw

                headers_out: Dict[str, str] = {k.lower(): v for k, v in resp.headers.items()}
                return HttpResponse(status=int(resp.status), headers=headers_out, body=body)

        except HTTPError as e:
            raw = ""
            try:
                raw = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass

            body = None
            try:
                body = json.loads(raw) if raw else None
            except Exception:
                body = raw or None

            headers_out = {k.lower(): v for k, v in getattr(e, "headers", {}).items()} if getattr(e, "headers", None) else {}
            return HttpResponse(status=int(getattr(e, "code", 0) or 0), headers=headers_out, body=body)

        except URLError as e:
            return HttpResponse(status=0, headers={}, body={"error": "urlerror", "reason": str(e)})
