from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "http://127.0.0.1:8787"


@dataclass
class CheckResult:
    check_id: str
    ok: bool
    detail: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.check_id,
            "ok": bool(self.ok),
            "detail": str(self.detail),
        }


@dataclass
class HttpResult:
    ok: bool
    status: int
    body: Dict[str, Any]
    set_cookie: str
    error: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_base_url(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return DEFAULT_BASE_URL
    return text.rstrip("/")


def _request_json(
    url: str,
    *,
    method: str = "GET",
    payload: Optional[Dict[str, Any]] = None,
    cookie: str = "",
    timeout_seconds: float = 5.0,
) -> HttpResult:
    headers = {"Accept": "application/json"}
    data_bytes = None
    if payload is not None:
        data_bytes = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if cookie:
        headers["Cookie"] = cookie

    req = Request(url, method=method.upper(), headers=headers, data=data_bytes)
    try:
        with urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw) if raw.strip() else {}
            return HttpResult(
                ok=True,
                status=int(response.status),
                body=parsed if isinstance(parsed, dict) else {"value": parsed},
                set_cookie=str(response.headers.get("Set-Cookie", "") or ""),
                error="",
            )
    except HTTPError as exc:
        body: Dict[str, Any] = {}
        try:
            raw = exc.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw) if raw.strip() else {}
            body = parsed if isinstance(parsed, dict) else {"value": parsed}
        except Exception:
            body = {}
        return HttpResult(
            ok=False,
            status=int(getattr(exc, "code", 0) or 0),
            body=body,
            set_cookie="",
            error=str(exc),
        )
    except URLError as exc:
        return HttpResult(ok=False, status=0, body={}, set_cookie="", error=str(exc))
    except Exception as exc:
        return HttpResult(ok=False, status=0, body={}, set_cookie="", error=str(exc))


def _cookie_header_from_set_cookie(raw_set_cookie: str) -> str:
    text = str(raw_set_cookie or "").strip()
    if not text:
        return ""
    cookie = SimpleCookie()
    cookie.load(text)
    pairs: List[str] = []
    for morsel in cookie.values():
        pairs.append(f"{morsel.key}={morsel.value}")
    return "; ".join(pairs)


def _bool_arg_default_false(parser: argparse.ArgumentParser, flag: str, help_text: str) -> None:
    parser.add_argument(flag, action="store_true", help=help_text)


def _arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="packaged_clean_machine_validation")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Dashboard base URL (default: http://127.0.0.1:8787)")
    parser.add_argument("--username", default="", help="Optional dashboard username for session-auth endpoints")
    parser.add_argument("--password", default="", help="Optional dashboard password")
    parser.add_argument("--output", default="", help="Optional path to write JSON report")
    parser.add_argument("--expect-auth-flow", default="device_code", help="Expected twitch auth flow (default: device_code)")
    _bool_arg_default_false(parser, "--require-setup-complete", "Fail if setup.complete is not true")
    _bool_arg_default_false(parser, "--require-gate-enforced", "Fail if setup.enforced is not true")
    _bool_arg_default_false(parser, "--require-connected", "Fail if bot and broadcaster are not both connected")
    _bool_arg_default_false(parser, "--require-readiness-ready", "Fail if /api/system/readiness ready is not true")
    return parser


def _append(checks: List[CheckResult], check_id: str, ok: bool, detail: str) -> None:
    checks.append(CheckResult(check_id=check_id, ok=ok, detail=detail))


def main(argv: Optional[List[str]] = None) -> int:
    args = _arg_parser().parse_args(argv)
    base_url = _normalize_base_url(args.base_url)
    checks: List[CheckResult] = []
    cookie_header = ""

    username = str(args.username or "").strip()
    password = str(args.password or "").strip()
    if username and password:
        login = _request_json(
            f"{base_url}/api/auth/login",
            method="POST",
            payload={"username": username, "password": password},
        )
        login_ok = bool(login.ok and login.body.get("authenticated") is True)
        _append(checks, "auth_login", login_ok, f"status={login.status} error={login.error or 'none'}")
        cookie_header = _cookie_header_from_set_cookie(login.set_cookie)
    elif username or password:
        _append(checks, "auth_login", False, "Both --username and --password are required when authenticating")

    twitch_status = _request_json(f"{base_url}/api/twitch/status", cookie=cookie_header)
    readiness_status = _request_json(f"{base_url}/api/system/readiness", cookie=cookie_header)
    status_snapshot = _request_json(f"{base_url}/api/status", cookie=cookie_header)

    _append(
        checks,
        "twitch_status_endpoint",
        twitch_status.ok,
        f"status={twitch_status.status} error={twitch_status.error or 'none'}",
    )
    _append(
        checks,
        "readiness_endpoint",
        readiness_status.ok,
        f"status={readiness_status.status} error={readiness_status.error or 'none'}",
    )
    _append(
        checks,
        "status_endpoint",
        status_snapshot.ok,
        f"status={status_snapshot.status} error={status_snapshot.error or 'none'}",
    )

    twitch_payload = twitch_status.body if twitch_status.ok else {}
    readiness_payload = readiness_status.body if readiness_status.ok else {}
    status_payload = status_snapshot.body if status_snapshot.ok else {}

    auth_flow = str(twitch_payload.get("auth_flow") or "").strip().lower()
    expected_flow = str(args.expect_auth_flow or "").strip().lower()
    flow_ok = bool(auth_flow and expected_flow and auth_flow == expected_flow)
    _append(
        checks,
        "auth_flow",
        flow_ok,
        f"actual={auth_flow or '<missing>'} expected={expected_flow or '<missing>'}",
    )

    setup = twitch_payload.get("setup", {}) if isinstance(twitch_payload, dict) else {}
    setup_ok = isinstance(setup, dict) and isinstance(setup.get("steps", []), list)
    setup_blockers = setup.get("blockers", []) if isinstance(setup, dict) else []
    if not isinstance(setup_blockers, list):
        setup_blockers = []
    _append(
        checks,
        "setup_payload",
        setup_ok,
        f"complete={bool(setup.get('complete', False)) if isinstance(setup, dict) else False} blockers={setup_blockers}",
    )

    missing_fields = twitch_payload.get("missing_config_fields", []) if isinstance(twitch_payload, dict) else []
    if not isinstance(missing_fields, list):
        missing_fields = []
    client_secret_not_required = not any(str(item).strip().upper() == "TWITCH_CLIENT_SECRET" for item in missing_fields)
    _append(
        checks,
        "device_code_client_secret_posture",
        client_secret_not_required,
        f"missing_config_fields={missing_fields}",
    )

    if args.require_setup_complete:
        setup_complete = bool(setup.get("complete", False)) if isinstance(setup, dict) else False
        _append(checks, "require_setup_complete", setup_complete, f"setup_complete={setup_complete}")

    if args.require_gate_enforced:
        gate_enforced = bool(setup.get("enforced", False)) if isinstance(setup, dict) else False
        _append(checks, "require_gate_enforced", gate_enforced, f"setup_enforced={gate_enforced}")

    if args.require_connected:
        accounts = twitch_payload.get("accounts", {}) if isinstance(twitch_payload, dict) else {}
        if not isinstance(accounts, dict):
            accounts = {}
        bot_connected = bool((accounts.get("bot") or {}).get("connected", False))
        broadcaster_connected = bool((accounts.get("broadcaster") or {}).get("connected", False))
        both_connected = bool(bot_connected and broadcaster_connected)
        _append(
            checks,
            "require_connected",
            both_connected,
            f"bot_connected={bot_connected} broadcaster_connected={broadcaster_connected}",
        )

    if args.require_readiness_ready:
        readiness_ready = bool(readiness_payload.get("ready", False)) if isinstance(readiness_payload, dict) else False
        _append(
            checks,
            "require_readiness_ready",
            readiness_ready,
            f"readiness_ready={readiness_ready}",
        )

    blocked_by = status_payload.get("blocked_by", []) if isinstance(status_payload, dict) else []
    if not isinstance(blocked_by, list):
        blocked_by = []

    report = {
        "checked_at": _utc_now_iso(),
        "base_url": base_url,
        "pass": all(item.ok for item in checks),
        "checks": [item.to_dict() for item in checks],
        "snapshot": {
            "auth_flow": auth_flow,
            "setup": {
                "enforced": bool(setup.get("enforced", False)) if isinstance(setup, dict) else False,
                "complete": bool(setup.get("complete", False)) if isinstance(setup, dict) else False,
                "blockers": setup_blockers,
            },
            "missing_config_fields": missing_fields,
            "status_blocked_by": blocked_by,
            "readiness_ready": bool(readiness_payload.get("ready", False)) if isinstance(readiness_payload, dict) else False,
        },
    }

    output_path = str(args.output or "").strip()
    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if bool(report.get("pass", False)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
