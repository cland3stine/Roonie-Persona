from __future__ import annotations

import argparse
import cgi
import json
import mimetypes
import os
from http.cookies import SimpleCookie
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from .models import serialize_many
from .storage import DashboardStorage


def _cors_origin(handler: BaseHTTPRequestHandler) -> str:
    origin = handler.headers.get("Origin")
    if isinstance(origin, str) and origin.strip():
        return origin.strip()
    return "*"


def _json_response(
    handler: BaseHTTPRequestHandler,
    payload: Any,
    status: int = 200,
    extra_headers: Optional[Dict[str, str]] = None,
) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", _cors_origin(handler))
    handler.send_header("Vary", "Origin")
    handler.send_header("Access-Control-Allow-Credentials", "true")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
    handler.send_header(
        "Access-Control-Allow-Headers",
        "Content-Type, X-ROONIE-OP-KEY, X-ROONIE-ACTOR, X-ROONIE-OP-ACTOR",
    )
    for key, value in (extra_headers or {}).items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(body)


def _bytes_response(
    handler: BaseHTTPRequestHandler,
    payload: bytes,
    *,
    content_type: str,
    status: int = 200,
    extra_headers: Optional[Dict[str, str]] = None,
) -> None:
    body = payload if isinstance(payload, (bytes, bytearray)) else bytes(payload)
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", _cors_origin(handler))
    handler.send_header("Vary", "Origin")
    handler.send_header("Access-Control-Allow-Credentials", "true")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
    handler.send_header(
        "Access-Control-Allow-Headers",
        "Content-Type, X-ROONIE-OP-KEY, X-ROONIE-ACTOR, X-ROONIE-OP-ACTOR",
    )
    for key, value in (extra_headers or {}).items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(body)


def _html_response(
    handler: BaseHTTPRequestHandler,
    html: str,
    *,
    status: int = 200,
    extra_headers: Optional[Dict[str, str]] = None,
) -> None:
    body = html.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", _cors_origin(handler))
    handler.send_header("Vary", "Origin")
    handler.send_header("Access-Control-Allow-Credentials", "true")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
    handler.send_header(
        "Access-Control-Allow-Headers",
        "Content-Type, X-ROONIE-OP-KEY, X-ROONIE-ACTOR, X-ROONIE-OP-ACTOR",
    )
    for key, value in (extra_headers or {}).items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(body)


def _prefers_html(handler: BaseHTTPRequestHandler) -> bool:
    accept = str(handler.headers.get("Accept", "")).lower()
    return "text/html" in accept


def _resolve_dist_path() -> Path:
    raw = os.getenv("ROONIE_DASHBOARD_DIST_DIR", "dist")
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def _parse_limit(query: Dict[str, Any], default: int = 5) -> int:
    raw = query.get("limit", [default])
    value = raw[0] if isinstance(raw, list) and raw else default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, 100))


def _parse_limit_bounded(query: Dict[str, Any], *, default: int, max_value: int) -> int:
    raw = query.get("limit", [default])
    value = raw[0] if isinstance(raw, list) and raw else default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, max_value))


def _parse_offset(query: Dict[str, Any], default: int = 0) -> int:
    raw = query.get("offset", [default])
    value = raw[0] if isinstance(raw, list) and raw else default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0, parsed)


def _parse_active_only(query: Dict[str, Any], default: bool = True) -> bool:
    raw = query.get("active_only", [("1" if default else "0")])
    value = raw[0] if isinstance(raw, list) and raw else ("1" if default else "0")
    text = str(value).strip().lower()
    if text in {"0", "false", "no", "off"}:
        return False
    if text in {"1", "true", "yes", "on"}:
        return True
    return bool(default)


def _query_opt(query: Dict[str, Any], key: str) -> str | None:
    raw = query.get(key, [None])
    value = raw[0] if isinstance(raw, list) and raw else None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def build_handler(storage: DashboardStorage) -> type[BaseHTTPRequestHandler]:
    class DashboardHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            # Quiet by default for local polling.
            return

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Access-Control-Allow-Origin", _cors_origin(self))
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
            self.send_header(
                "Access-Control-Allow-Headers",
                "Content-Type, X-ROONIE-OP-KEY, X-ROONIE-ACTOR, X-ROONIE-OP-ACTOR",
            )
            self.end_headers()

        def _read_json_body(self) -> tuple[bool, Dict[str, Any]]:
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except (TypeError, ValueError):
                content_length = 0
            if content_length <= 0:
                return True, {}
            raw = self.rfile.read(content_length)
            if not raw:
                return True, {}
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return False, {}
            if not isinstance(parsed, dict):
                return False, {}
            return True, parsed

        def _serve_static_file(self, file_path: Path) -> bool:
            try:
                body = file_path.read_bytes()
            except OSError:
                return False
            content_type, _ = mimetypes.guess_type(str(file_path))
            if not content_type:
                content_type = "application/octet-stream"
            _bytes_response(self, body, content_type=content_type)
            return True

        def _read_multipart_upload(self) -> Tuple[bool, Optional[bytes], str]:
            ctype = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in ctype.lower():
                return False, None, "Expected multipart/form-data upload."
            environ = {
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": ctype,
                "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
            }
            try:
                form = cgi.FieldStorage(
                    fp=self.rfile,
                    headers=self.headers,
                    environ=environ,
                    keep_blank_values=False,
                )
            except Exception:
                return False, None, "Failed to parse multipart form data."

            file_item = None
            if "file" in form:
                file_item = form["file"]
            else:
                for key in form.keys():
                    maybe = form[key]
                    if getattr(maybe, "filename", None):
                        file_item = maybe
                        break
            if file_item is None or not getattr(file_item, "file", None):
                return False, None, "Missing file part."
            try:
                content = file_item.file.read()
            except Exception:
                return False, None, "Failed reading uploaded file."
            if not isinstance(content, (bytes, bytearray)) or not content:
                return False, None, "Uploaded file is empty."
            return True, bytes(content), ""

        def _operator_from_payload(self, payload: Dict[str, Any]) -> str:
            operator = payload.get("operator")
            if isinstance(operator, str) and operator.strip():
                return operator.strip()
            legacy = self.headers.get("X-ROONIE-OP-ACTOR")
            if isinstance(legacy, str) and legacy.strip():
                return legacy.strip()
            return "Operator"

        def _actor_from_header(self) -> str:
            actor_header = self.headers.get("X-ROONIE-ACTOR")
            if actor_header is None:
                actor_header = self.headers.get("X-ROONIE-OP-ACTOR")
            return storage.normalize_actor(actor_header)

        @staticmethod
        def _session_cookie_name() -> str:
            return "roonie_session"

        @staticmethod
        def _secure_cookies_enabled() -> bool:
            raw = os.getenv("ROONIE_DASHBOARD_SECURE_COOKIES", "0")
            return str(raw).strip().lower() in {"1", "true", "yes", "on"}

        def _build_session_cookie(self, value: str, *, max_age: int) -> str:
            cookie = (
                f"{self._session_cookie_name()}={value}; "
                "Path=/; HttpOnly; SameSite=Lax; "
                f"Max-Age={max(0, int(max_age))}"
            )
            if self._secure_cookies_enabled():
                cookie += "; Secure"
            return cookie

        def _session_id_from_cookie(self) -> Optional[str]:
            raw_cookie = self.headers.get("Cookie")
            if not raw_cookie:
                return None
            try:
                cookie = SimpleCookie()
                cookie.load(raw_cookie)
            except Exception:
                return None
            morsel = cookie.get(self._session_cookie_name())
            if morsel is None:
                return None
            value = morsel.value
            value = str(value).strip()
            return value or None

        def _session_identity(self) -> Optional[Dict[str, str]]:
            return storage.get_session_user(self._session_id_from_cookie())

        def _identity_from_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
            session_user = self._session_identity()
            if session_user:
                username = str(session_user.get("username", "")).strip().lower()
                role = storage.normalize_role(session_user.get("role"))
                return {
                    "authenticated": True,
                    "username": username,
                    "role": role,
                    "actor": username,
                    "operator": (self._operator_from_payload(payload) or username),
                    "auth_mode": "session",
                }
            actor = self._actor_from_header()
            return {
                "authenticated": False,
                "username": None,
                "role": None,
                "actor": actor,
                "operator": self._operator_from_payload(payload),
                "auth_mode": None,
            }

        def _authorize_write(
            self,
            *,
            action: str,
            payload: Dict[str, Any],
            required_role: str = "operator",
        ) -> Optional[Dict[str, Any]]:
            identity = self._identity_from_request(payload)
            if identity.get("authenticated"):
                role = storage.normalize_role(identity.get("role"))
                if storage.role_allows(role, required_role):
                    return identity
                msg = f"Forbidden: {required_role} role required."
                storage.record_operator_action(
                    operator=identity["operator"],
                    action=action,
                    payload=payload,
                    result=f"DENIED: {msg}",
                    actor=identity["actor"],
                    username=identity.get("username"),
                    role=identity.get("role"),
                    auth_mode=identity.get("auth_mode"),
                )
                _json_response(
                    self,
                    {"ok": False, "error": "forbidden", "detail": msg},
                    status=HTTPStatus.FORBIDDEN,
                )
                return None

            # Backward compatibility: operator key fallback if no authenticated session.
            key = self.headers.get("X-ROONIE-OP-KEY")
            ok, msg = storage.validate_operator_key(key)
            if ok:
                identity["auth_mode"] = "legacy_key"
                return identity
            storage.record_operator_action(
                operator=identity["operator"],
                action=action,
                payload=payload,
                result=f"DENIED: {msg}",
                actor=identity["actor"],
                username=identity.get("username"),
                role=identity.get("role"),
                auth_mode=identity.get("auth_mode"),
            )
            _json_response(
                self,
                {"ok": False, "error": "forbidden", "detail": msg},
                status=HTTPStatus.FORBIDDEN,
            )
            return None

        def _require_authenticated_session(self, *, required_role: str = "operator") -> Optional[Dict[str, Any]]:
            identity = self._identity_from_request({})
            if not identity.get("authenticated"):
                _json_response(
                    self,
                    {"ok": False, "error": "forbidden", "detail": "Authenticated session required."},
                    status=HTTPStatus.FORBIDDEN,
                )
                return None
            role = storage.normalize_role(identity.get("role"))
            if not storage.role_allows(role, required_role):
                _json_response(
                    self,
                    {"ok": False, "error": "forbidden", "detail": f"{required_role.title()} role required."},
                    status=HTTPStatus.FORBIDDEN,
                )
                return None
            return identity

        @staticmethod
        def _identity_memory_username(identity: Dict[str, Any]) -> str:
            username = str(identity.get("username") or "").strip().lower()
            if username:
                return username
            actor = str(identity.get("actor") or "").strip().lower()
            return actor or "unknown"

        def _handle_studio_profile_write(self, *, patch: bool) -> None:
            ok_body, payload = self._read_json_body()
            if not ok_body:
                _json_response(
                    self,
                    {"ok": False, "error": "bad_request", "detail": "Invalid JSON body."},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            identity = self._authorize_write(
                action="STUDIO_PROFILE_UPDATE",
                payload=payload,
                required_role="operator",
            )
            if identity is None:
                return
            try:
                profile, diff_payload = storage.update_studio_profile(
                    payload,
                    actor=(identity.get("username") or identity.get("actor")),
                    patch=patch,
                )
            except ValueError as exc:
                _json_response(
                    self,
                    {"ok": False, "error": "bad_request", "detail": str(exc)},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            audit = storage.record_operator_action(
                operator=identity["operator"],
                action="STUDIO_PROFILE_UPDATE",
                payload=diff_payload,
                result="OK",
                actor=identity["actor"],
                username=identity.get("username"),
                role=identity.get("role"),
                auth_mode=identity.get("auth_mode"),
            )
            _json_response(self, {"ok": True, "profile": profile, "audit": audit.to_dict()})

        def _handle_inner_circle_write(self, *, patch: bool) -> None:
            ok_body, payload = self._read_json_body()
            if not ok_body:
                _json_response(
                    self,
                    {"ok": False, "error": "bad_request", "detail": "Invalid JSON body."},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            identity = self._authorize_write(
                action="INNER_CIRCLE_UPDATE",
                payload=payload,
                required_role="operator",
            )
            if identity is None:
                return
            try:
                circle, diff_payload = storage.update_inner_circle(
                    payload,
                    actor=(identity.get("username") or identity.get("actor")),
                    patch=patch,
                )
            except ValueError as exc:
                _json_response(
                    self,
                    {"ok": False, "error": "bad_request", "detail": str(exc)},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            audit = storage.record_operator_action(
                operator=identity["operator"],
                action="INNER_CIRCLE_UPDATE",
                payload=diff_payload,
                result="OK",
                actor=identity["actor"],
                username=identity.get("username"),
                role=identity.get("role"),
                auth_mode=identity.get("auth_mode"),
            )
            _json_response(self, {"ok": True, "inner_circle": circle, "audit": audit.to_dict()})

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            path = parsed.path

            if path == "/api/twitch/callback":
                code = _query_opt(query, "code")
                state_token = _query_opt(query, "state")
                result = storage.twitch_connect_finish(
                    code=str(code or ""),
                    state_token=str(state_token or ""),
                )
                if _prefers_html(self):
                    ok = bool(result.get("ok"))
                    account = str(result.get("account") or "").strip()
                    detail = str(result.get("detail") or result.get("error") or "OAuth callback completed.")
                    app_url = str(
                        os.getenv("ROONIE_DASHBOARD_APP_URL")
                        or os.getenv("ROONIE_DASHBOARD_PUBLIC_URL")
                        or "/"
                    ).strip() or "/"
                    if not (app_url.startswith("http://") or app_url.startswith("https://") or app_url.startswith("/")):
                        app_url = "/"
                    status_text = "Connected." if ok else "Connection failed."
                    safe_detail = detail.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    safe_status = status_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    safe_account = account.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") or "twitch"
                    html = (
                        "<!doctype html><html><head><meta charset='utf-8'>"
                        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
                        "<title>Roonie Twitch Auth</title></head>"
                        "<body style='font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;"
                        "background:#111;color:#ddd;padding:24px'>"
                        f"<h2 style='margin:0 0 8px 0'>{safe_status}</h2>"
                        f"<div style='opacity:0.9'>Account: {safe_account}</div>"
                        f"<div style='opacity:0.8;margin-top:6px'>{safe_detail}</div>"
                        f"<div style='opacity:0.7;margin-top:12px'>Returning to dashboard: {app_url}</div>"
                        "<script>"
                        "var __hasOpener = false;"
                        "try { __hasOpener = !!(window.opener && !window.opener.closed); } catch (e) { __hasOpener = false; }"
                        "if (__hasOpener) {"
                        f"  try {{ window.opener.postMessage({json.dumps({'type': 'ROONIE_TWITCH_AUTH_COMPLETE', 'ok': ok, 'account': account})}, '*'); }} catch (e) {{}}"
                        "  try { window.opener.focus(); } catch (e) {}"
                        "  setTimeout(function(){ try { window.close(); } catch (e) {} }, 250);"
                        "} else {"
                        f"  setTimeout(function(){{ window.location.replace({json.dumps(app_url)}); }}, 1200);"
                        "}"
                        "</script>"
                        "</body></html>"
                    )
                    _html_response(self, html, status=HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST)
                    return
                status = HTTPStatus.OK if bool(result.get("ok")) else HTTPStatus.BAD_REQUEST
                _json_response(self, result, status=status)
                return
            if path == "/api/status":
                _json_response(self, storage.get_status().to_dict())
                return
            if path == "/api/events":
                limit = _parse_limit(query, default=5)
                events = storage.get_events(limit=limit)
                _json_response(self, serialize_many(events))
                return
            if path == "/api/suppressions":
                limit = _parse_limit(query, default=5)
                suppressions = storage.get_suppressions(limit=limit)
                _json_response(self, serialize_many(suppressions))
                return
            if path == "/api/operator_log":
                limit = _parse_limit(query, default=5)
                ops = storage.get_operator_log(limit=limit)
                _json_response(self, serialize_many(ops))
                return
            if path == "/api/queue":
                limit = _parse_limit(query, default=25)
                _json_response(self, storage.get_queue(limit=limit))
                return
            if path == "/api/twitch/channel_emotes":
                _json_response(self, storage.fetch_channel_emotes())
                return
            if path == "/api/studio_profile":
                _json_response(self, storage.get_studio_profile())
                return
            if path == "/api/inner_circle":
                _json_response(self, storage.get_inner_circle())
                return
            if path == "/api/providers/status":
                _json_response(self, storage.get_providers_status())
                return
            if path == "/api/system/readiness":
                _json_response(self, storage.get_readiness_state())
                return
            if path == "/api/system/health":
                identity = self._require_authenticated_session(required_role="operator")
                if identity is None:
                    return
                _json_response(self, storage.get_system_health())
                return
            if path == "/api/system/export":
                identity = self._require_authenticated_session(required_role="director")
                if identity is None:
                    return
                blob = storage.build_system_export_zip()
                audit = storage.record_operator_action(
                    operator=identity["operator"],
                    action="SYSTEM_EXPORT",
                    payload={"files": [
                        "data/providers_config.json",
                        "data/routing_config.json",
                        "data/studio_profile.json",
                        "data/senses_config.json",
                        "data/memory.sqlite",
                    ]},
                    result="OK",
                    actor=identity["actor"],
                    username=identity.get("username"),
                    role=identity.get("role"),
                    auth_mode=identity.get("auth_mode"),
                )
                _ = audit
                _bytes_response(
                    self,
                    blob,
                    content_type="application/zip",
                    extra_headers={"Content-Disposition": 'attachment; filename="roonie_system_export.zip"'},
                )
                return
            if path == "/api/routing/status":
                identity = self._authorize_write(
                    action="ROUTING_STATUS_READ",
                    payload={},
                    required_role="operator",
                )
                if identity is None:
                    return
                _json_response(self, storage.get_routing_status())
                return
            if path == "/api/senses/status":
                _json_response(self, storage.get_senses_status())
                return
            if path == "/api/memory/cultural":
                identity = self._authorize_write(
                    action="MEMORY_CULTURAL_READ",
                    payload={},
                    required_role="operator",
                )
                if identity is None:
                    return
                limit = _parse_limit_bounded(query, default=100, max_value=500)
                offset = _parse_offset(query, default=0)
                q = _query_opt(query, "q")
                active_only = _parse_active_only(query, default=True)
                items, total_count = storage.query_memory_cultural(
                    limit=limit,
                    offset=offset,
                    q=q,
                    active_only=active_only,
                )
                _json_response(self, {"items": items, "total_count": total_count})
                return
            if path == "/api/memory/viewers":
                identity = self._authorize_write(
                    action="MEMORY_VIEWER_READ",
                    payload={},
                    required_role="operator",
                )
                if identity is None:
                    return
                limit = _parse_limit_bounded(query, default=100, max_value=500)
                offset = _parse_offset(query, default=0)
                q = _query_opt(query, "q")
                viewer_handle = _query_opt(query, "viewer_handle")
                active_only = _parse_active_only(query, default=True)
                items, total_count = storage.query_memory_viewers(
                    viewer_handle=viewer_handle,
                    limit=limit,
                    offset=offset,
                    q=q,
                    active_only=active_only,
                )
                _json_response(self, {"items": items, "total_count": total_count})
                return
            if path == "/api/memory/pending":
                identity = self._authorize_write(
                    action="MEMORY_PENDING_READ",
                    payload={},
                    required_role="operator",
                )
                if identity is None:
                    return
                limit = _parse_limit_bounded(query, default=100, max_value=500)
                offset = _parse_offset(query, default=0)
                q = _query_opt(query, "q")
                items, total_count = storage.query_memory_pending(
                    limit=limit,
                    offset=offset,
                    q=q,
                )
                _json_response(self, {"items": items, "total_count": total_count})
                return
            if path == "/api/auth/me":
                identity = self._session_identity()
                if identity:
                    _json_response(
                        self,
                        {
                            "authenticated": True,
                            "username": identity.get("username"),
                            "role": identity.get("role"),
                        },
                    )
                else:
                    _json_response(
                        self,
                        {
                            "authenticated": False,
                            "username": None,
                            "role": None,
                        },
                    )
                return
            if path == "/api/auth/twitch_status":
                _json_response(self, storage.get_twitch_status())
                return
            if path == "/api/twitch/status":
                _json_response(self, storage.get_twitch_status())
                return
            if path == "/api/library_index/status":
                _json_response(self, storage.get_library_status())
                return
            if path == "/api/library_index/search":
                q_raw = query.get("q", [""])
                q = str(q_raw[0] if isinstance(q_raw, list) and q_raw else "")
                limit = _parse_limit(query, default=25)
                _json_response(self, storage.search_library_index(q=q, limit=limit))
                return
            if path == "/api/logs/events":
                limit = _parse_limit_bounded(query, default=100, max_value=500)
                offset = _parse_offset(query, default=0)
                items, total_count = storage.query_events(
                    limit=limit,
                    offset=offset,
                    q=_query_opt(query, "q"),
                    decision_type=_query_opt(query, "decision_type"),
                    decision=_query_opt(query, "decision"),
                    suppression_reason=_query_opt(query, "suppression_reason"),
                    since_ts=_query_opt(query, "since_ts"),
                    until_ts=_query_opt(query, "until_ts"),
                    suppressed_only=False,
                )
                _json_response(self, {"items": serialize_many(items), "total_count": total_count})
                return
            if path == "/api/logs/suppressions":
                limit = _parse_limit_bounded(query, default=100, max_value=500)
                offset = _parse_offset(query, default=0)
                items, total_count = storage.query_events(
                    limit=limit,
                    offset=offset,
                    q=_query_opt(query, "q"),
                    decision_type=_query_opt(query, "decision_type"),
                    decision=_query_opt(query, "decision"),
                    suppression_reason=_query_opt(query, "suppression_reason"),
                    since_ts=_query_opt(query, "since_ts"),
                    until_ts=_query_opt(query, "until_ts"),
                    suppressed_only=True,
                )
                _json_response(self, {"items": serialize_many(items), "total_count": total_count})
                return
            if path == "/api/logs/operator":
                limit = _parse_limit_bounded(query, default=100, max_value=500)
                offset = _parse_offset(query, default=0)
                items, total_count = storage.query_operator_log(
                    limit=limit,
                    offset=offset,
                    actor=_query_opt(query, "actor"),
                    action=_query_opt(query, "action"),
                    since_ts=_query_opt(query, "since_ts"),
                    until_ts=_query_opt(query, "until_ts"),
                )
                _json_response(self, {"items": serialize_many(items), "total_count": total_count})
                return
            if path == "/healthz":
                _json_response(
                    self,
                    {
                        "service": "roonie-dashboard-api",
                        "status": "ok",
                    },
                )
                return

            dist_path = _resolve_dist_path()
            if dist_path.exists() and dist_path.is_dir():
                if path.startswith("/api/"):
                    _json_response(
                        self,
                        {"error": "not_found", "path": path},
                        status=HTTPStatus.NOT_FOUND,
                    )
                    return

                requested = "index.html" if path == "/" else path.lstrip("/")
                candidate = (dist_path / requested).resolve()
                try:
                    candidate.relative_to(dist_path.resolve())
                except ValueError:
                    candidate = dist_path / "index.html"

                if candidate.exists() and candidate.is_file() and self._serve_static_file(candidate):
                    return

                index_file = dist_path / "index.html"
                if index_file.exists() and index_file.is_file() and self._serve_static_file(index_file):
                    return

            if path == "/":
                _json_response(
                    self,
                    {
                        "service": "roonie-dashboard-api",
                        "status": "ok",
                    },
                )
                return

            _json_response(
                self,
                {"error": "not_found", "path": path},
                status=HTTPStatus.NOT_FOUND,
            )

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            path = parsed.path

            if path == "/api/auth/login":
                ok_body, payload = self._read_json_body()
                if not ok_body:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": "Invalid JSON body."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                username = str(payload.get("username", "")).strip().lower()
                password = str(payload.get("password", ""))
                auth = storage.login_dashboard_user(username, password)
                if not auth:
                    _json_response(
                        self,
                        {"ok": False, "error": "unauthorized", "detail": "Invalid username or password."},
                        status=HTTPStatus.UNAUTHORIZED,
                    )
                    return
                cookie_value = self._build_session_cookie(
                    auth["session_id"],
                    max_age=storage.session_ttl_seconds(),
                )
                _json_response(
                    self,
                    {
                        "ok": True,
                        "authenticated": True,
                        "username": auth.get("username"),
                        "role": auth.get("role"),
                    },
                    extra_headers={"Set-Cookie": cookie_value},
                )
                return

            if path == "/api/auth/logout":
                sid = self._session_id_from_cookie()
                storage.logout_session(sid)
                _json_response(
                    self,
                    {"ok": True, "authenticated": False},
                    extra_headers={
                        "Set-Cookie": self._build_session_cookie("", max_age=0),
                    },
                )
                return

            if path == "/api/auth/twitch_reconnect":
                ok_body, payload = self._read_json_body()
                if not ok_body:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": "Invalid JSON body."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                identity = self._authorize_write(
                    action="TWITCH_RECONNECT",
                    payload=payload,
                    required_role="operator",
                )
                if identity is None:
                    return
                account = (
                    str(_query_opt(query, "account") or payload.get("account", "bot"))
                    .strip()
                    .lower()
                    or "bot"
                )
                try:
                    result = storage.twitch_connect_start(account)
                except ValueError as exc:
                    storage.record_operator_action(
                        operator=identity["operator"],
                        action="TWITCH_RECONNECT",
                        payload=payload,
                        result=f"INVALID_PAYLOAD: {exc}",
                        actor=identity["actor"],
                        username=identity.get("username"),
                        role=identity.get("role"),
                        auth_mode=identity.get("auth_mode"),
                    )
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": str(exc)},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                storage.record_operator_action(
                    operator=identity["operator"],
                    action="TWITCH_RECONNECT",
                    payload={"account": account},
                    result=("OK" if result.get("ok") else "NOT_AVAILABLE"),
                    actor=identity["actor"],
                    username=identity.get("username"),
                    role=identity.get("role"),
                    auth_mode=identity.get("auth_mode"),
                )
                _json_response(self, result)
                return

            if path == "/api/twitch/connect_start":
                ok_body, payload = self._read_json_body()
                if not ok_body:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": "Invalid JSON body."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                identity = self._authorize_write(
                    action="TWITCH_CONNECT_START",
                    payload=payload,
                    required_role="operator",
                )
                if identity is None:
                    return
                account = (
                    str(_query_opt(query, "account") or payload.get("account", "bot"))
                    .strip()
                    .lower()
                    or "bot"
                )
                try:
                    result = storage.twitch_connect_start(account)
                except ValueError as exc:
                    storage.record_operator_action(
                        operator=identity["operator"],
                        action="TWITCH_CONNECT_START",
                        payload=payload,
                        result=f"INVALID_PAYLOAD: {exc}",
                        actor=identity["actor"],
                        username=identity.get("username"),
                        role=identity.get("role"),
                        auth_mode=identity.get("auth_mode"),
                    )
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": str(exc)},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                storage.record_operator_action(
                    operator=identity["operator"],
                    action="TWITCH_CONNECT_START",
                    payload={"account": account},
                    result=("OK" if result.get("ok") else "NOT_AVAILABLE"),
                    actor=identity["actor"],
                    username=identity.get("username"),
                    role=identity.get("role"),
                    auth_mode=identity.get("auth_mode"),
                )
                _json_response(self, result)
                return

            if path == "/api/twitch/disconnect":
                ok_body, payload = self._read_json_body()
                if not ok_body:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": "Invalid JSON body."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                identity = self._authorize_write(
                    action="TWITCH_DISCONNECT",
                    payload=payload,
                    required_role="operator",
                )
                if identity is None:
                    return
                account = (
                    str(_query_opt(query, "account") or payload.get("account", "bot"))
                    .strip()
                    .lower()
                    or "bot"
                )
                try:
                    status_payload = storage.twitch_disconnect(account)
                except ValueError as exc:
                    storage.record_operator_action(
                        operator=identity["operator"],
                        action="TWITCH_DISCONNECT",
                        payload=payload,
                        result=f"INVALID_PAYLOAD: {exc}",
                        actor=identity["actor"],
                        username=identity.get("username"),
                        role=identity.get("role"),
                        auth_mode=identity.get("auth_mode"),
                    )
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": str(exc)},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                storage.record_operator_action(
                    operator=identity["operator"],
                    action="TWITCH_DISCONNECT",
                    payload={"account": account},
                    result="OK",
                    actor=identity["actor"],
                    username=identity.get("username"),
                    role=identity.get("role"),
                    auth_mode=identity.get("auth_mode"),
                )
                _json_response(self, {"ok": True, "status": status_payload, "account": account})
                return

            if path == "/api/senses/enable":
                ok_body, payload = self._read_json_body()
                if not ok_body:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": "Invalid JSON body."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                identity = self._identity_from_request(payload)
                storage.record_operator_action(
                    operator=identity.get("operator") or "Operator",
                    action="SENSES_ENABLE_ATTEMPT",
                    payload=payload,
                    result="DENIED: Senses are disabled by Canon in this build.",
                    actor=identity.get("actor"),
                    username=identity.get("username"),
                    role=identity.get("role"),
                    auth_mode=identity.get("auth_mode"),
                )
                _json_response(
                    self,
                    {
                        "ok": False,
                        "error": "forbidden",
                        "detail": "Senses are disabled by Canon in this build.",
                    },
                    status=HTTPStatus.FORBIDDEN,
                )
                return

            if path == "/api/memory/cultural":
                ok_body, payload = self._read_json_body()
                if not ok_body:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": "Invalid JSON body."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                identity = self._authorize_write(
                    action="MEMORY_CULTURAL_CREATE",
                    payload=payload,
                    required_role="operator",
                )
                if identity is None:
                    return
                try:
                    item = storage.create_memory_cultural(
                        payload,
                        username=self._identity_memory_username(identity),
                        role=identity.get("role"),
                        auth_mode=identity.get("auth_mode"),
                    )
                except ValueError as exc:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": str(exc)},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                audit = storage.record_operator_action(
                    operator=identity["operator"],
                    action="MEMORY_CULTURAL_CREATE",
                    payload={"id": item.get("id"), "note": item.get("note"), "tags": item.get("tags", [])},
                    result="OK",
                    actor=identity["actor"],
                    username=identity.get("username"),
                    role=identity.get("role"),
                    auth_mode=identity.get("auth_mode"),
                )
                _json_response(self, {"ok": True, "item": item, "audit": audit.to_dict()})
                return

            if path == "/api/memory/viewer":
                ok_body, payload = self._read_json_body()
                if not ok_body:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": "Invalid JSON body."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                identity = self._authorize_write(
                    action="MEMORY_VIEWER_CREATE",
                    payload=payload,
                    required_role="operator",
                )
                if identity is None:
                    return
                try:
                    item = storage.create_memory_viewer(
                        payload,
                        username=self._identity_memory_username(identity),
                        role=identity.get("role"),
                        auth_mode=identity.get("auth_mode"),
                    )
                except ValueError as exc:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": str(exc)},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                audit = storage.record_operator_action(
                    operator=identity["operator"],
                    action="MEMORY_VIEWER_CREATE",
                    payload={
                        "id": item.get("id"),
                        "viewer_handle": item.get("viewer_handle"),
                        "note": item.get("note"),
                        "tags": item.get("tags", []),
                    },
                    result="OK",
                    actor=identity["actor"],
                    username=identity.get("username"),
                    role=identity.get("role"),
                    auth_mode=identity.get("auth_mode"),
                )
                _json_response(self, {"ok": True, "item": item, "audit": audit.to_dict()})
                return
            if parsed.path.startswith("/api/memory/pending/"):
                suffix = parsed.path[len("/api/memory/pending/") :].strip("/")
                if suffix.endswith("/approve"):
                    candidate_id = suffix[: -len("/approve")].strip("/")
                    if not candidate_id:
                        _json_response(
                            self,
                            {"ok": False, "error": "bad_request", "detail": "Missing pending memory id."},
                            status=HTTPStatus.BAD_REQUEST,
                        )
                        return
                    ok_body, payload = self._read_json_body()
                    if not ok_body:
                        _json_response(
                            self,
                            {"ok": False, "error": "bad_request", "detail": "Invalid JSON body."},
                            status=HTTPStatus.BAD_REQUEST,
                        )
                        return
                    identity = self._authorize_write(
                        action="MEMORY_PENDING_APPROVE",
                        payload={"id": candidate_id, **payload},
                        required_role="operator",
                    )
                    if identity is None:
                        return
                    try:
                        result = storage.approve_memory_pending(
                            candidate_id,
                            username=self._identity_memory_username(identity),
                            role=identity.get("role"),
                            auth_mode=identity.get("auth_mode"),
                        )
                    except ValueError as exc:
                        _json_response(
                            self,
                            {"ok": False, "error": "bad_request", "detail": str(exc)},
                            status=HTTPStatus.BAD_REQUEST,
                        )
                        return
                    except KeyError:
                        _json_response(
                            self,
                            {"ok": False, "error": "not_found", "detail": "Pending memory candidate not found."},
                            status=HTTPStatus.NOT_FOUND,
                        )
                        return
                    audit = storage.record_operator_action(
                        operator=identity["operator"],
                        action="MEMORY_PENDING_APPROVE",
                        payload={"id": candidate_id},
                        result="OK",
                        actor=identity["actor"],
                        username=identity.get("username"),
                        role=identity.get("role"),
                        auth_mode=identity.get("auth_mode"),
                    )
                    _json_response(self, {"ok": True, "result": result, "audit": audit.to_dict()})
                    return
                if suffix.endswith("/deny"):
                    candidate_id = suffix[: -len("/deny")].strip("/")
                    if not candidate_id:
                        _json_response(
                            self,
                            {"ok": False, "error": "bad_request", "detail": "Missing pending memory id."},
                            status=HTTPStatus.BAD_REQUEST,
                        )
                        return
                    ok_body, payload = self._read_json_body()
                    if not ok_body:
                        _json_response(
                            self,
                            {"ok": False, "error": "bad_request", "detail": "Invalid JSON body."},
                            status=HTTPStatus.BAD_REQUEST,
                        )
                        return
                    identity = self._authorize_write(
                        action="MEMORY_PENDING_DENY",
                        payload={"id": candidate_id, **payload},
                        required_role="operator",
                    )
                    if identity is None:
                        return
                    try:
                        result = storage.deny_memory_pending(
                            candidate_id,
                            username=self._identity_memory_username(identity),
                            role=identity.get("role"),
                            auth_mode=identity.get("auth_mode"),
                            reason=payload.get("reason") if isinstance(payload, dict) else None,
                        )
                    except ValueError as exc:
                        _json_response(
                            self,
                            {"ok": False, "error": "bad_request", "detail": str(exc)},
                            status=HTTPStatus.BAD_REQUEST,
                        )
                        return
                    except KeyError:
                        _json_response(
                            self,
                            {"ok": False, "error": "not_found", "detail": "Pending memory candidate not found."},
                            status=HTTPStatus.NOT_FOUND,
                        )
                        return
                    audit = storage.record_operator_action(
                        operator=identity["operator"],
                        action="MEMORY_PENDING_DENY",
                        payload={"id": candidate_id, "reason": payload.get("reason") if isinstance(payload, dict) else None},
                        result="OK",
                        actor=identity["actor"],
                        username=identity.get("username"),
                        role=identity.get("role"),
                        auth_mode=identity.get("auth_mode"),
                    )
                    _json_response(self, {"ok": True, "item": result, "audit": audit.to_dict()})
                    return

            if path == "/api/live/arm":
                ok_body, payload = self._read_json_body()
                if not ok_body:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": "Invalid JSON body."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                identity = self._authorize_write(
                    action="CONTROL_ARM_SET", payload=payload, required_role="operator"
                )
                if identity is None:
                    return
                state = storage.set_armed(True)
                previous_armed = bool(state.get("previous_armed", False))
                session_id = str(state.get("session_id", "")).strip() or None
                audit = storage.record_operator_action(
                    operator=identity["operator"],
                    action="CONTROL_ARM_SET",
                    payload={
                        "previous_armed": previous_armed,
                        "new_armed": bool(state.get("armed", True)),
                        "session_id": session_id,
                    },
                    result="OK",
                    actor=identity["actor"],
                    username=identity.get("username"),
                    role=identity.get("role"),
                    auth_mode=identity.get("auth_mode"),
                )
                _json_response(self, {"ok": True, "state": state, "audit": audit.to_dict()})
                return

            if path == "/api/live/disarm":
                ok_body, payload = self._read_json_body()
                if not ok_body:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": "Invalid JSON body."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                identity = self._authorize_write(
                    action="CONTROL_DISARM_SET", payload=payload, required_role="operator"
                )
                if identity is None:
                    return
                state = storage.set_armed(False)
                previous_armed = bool(state.get("previous_armed", True))
                session_id = str(state.get("session_id", "")).strip() or None
                audit = storage.record_operator_action(
                    operator=identity["operator"],
                    action="CONTROL_DISARM_SET",
                    payload={
                        "previous_armed": previous_armed,
                        "new_armed": bool(state.get("armed", False)),
                        "session_id": session_id,
                    },
                    result="OK",
                    actor=identity["actor"],
                    username=identity.get("username"),
                    role=identity.get("role"),
                    auth_mode=identity.get("auth_mode"),
                )
                _json_response(self, {"ok": True, "state": state, "audit": audit.to_dict()})
                return

            if path == "/api/live/emergency_stop":
                ok_body, payload = self._read_json_body()
                if not ok_body:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": "Invalid JSON body."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                identity = self._authorize_write(
                    action="EMERGENCY_STOP", payload=payload, required_role="operator"
                )
                if identity is None:
                    return
                state = storage.set_kill_switch(True)
                audit = storage.record_operator_action(
                    operator=identity["operator"],
                    action="EMERGENCY_STOP",
                    payload={"kill_switch_on": True},
                    result="OK",
                    actor=identity["actor"],
                    username=identity.get("username"),
                    role=identity.get("role"),
                    auth_mode=identity.get("auth_mode"),
                )
                _json_response(self, {"ok": True, "state": state, "audit": audit.to_dict()})
                return

            if path == "/api/live/kill_switch_release":
                ok_body, payload = self._read_json_body()
                if not ok_body:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": "Invalid JSON body."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                identity = self._authorize_write(
                    action="KILL_SWITCH_RELEASE", payload=payload, required_role="operator"
                )
                if identity is None:
                    return
                state = storage.set_kill_switch(False)
                audit = storage.record_operator_action(
                    operator=identity["operator"],
                    action="KILL_SWITCH_RELEASE",
                    payload={"kill_switch_on": False},
                    result="OK",
                    actor=identity["actor"],
                    username=identity.get("username"),
                    role=identity.get("role"),
                    auth_mode=identity.get("auth_mode"),
                )
                _json_response(self, {"ok": True, "state": state, "audit": audit.to_dict()})
                return

            if path == "/api/live/silence_now":
                ok_body, payload = self._read_json_body()
                if not ok_body:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": "Invalid JSON body."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                identity = self._authorize_write(action="SILENCE_NOW", payload=payload, required_role="operator")
                if identity is None:
                    return
                ttl = payload.get("ttl_seconds")
                state = storage.silence_now(ttl_seconds=ttl if ttl is not None else None)
                audit = storage.record_operator_action(
                    operator=identity["operator"],
                    action="SILENCE_NOW",
                    payload={"ttl_seconds": ttl if ttl is not None else storage._default_silence_ttl_seconds()},
                    result="OK",
                    actor=identity["actor"],
                    username=identity.get("username"),
                    role=identity.get("role"),
                    auth_mode=identity.get("auth_mode"),
                )
                _json_response(self, {"ok": True, "state": state, "audit": audit.to_dict()})
                return

            if path == "/api/queue/cancel":
                ok_body, payload = self._read_json_body()
                if not ok_body:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": "Invalid JSON body."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                identity = self._authorize_write(action="QUEUE_CANCEL", payload=payload, required_role="operator")
                if identity is None:
                    return
                queue_id = str(payload.get("id", "")).strip()
                if not queue_id:
                    storage.record_operator_action(
                        operator=identity["operator"],
                        action="QUEUE_CANCEL",
                        payload=payload,
                        result="INVALID_PAYLOAD: missing id",
                        actor=identity["actor"],
                        username=identity.get("username"),
                        role=identity.get("role"),
                        auth_mode=identity.get("auth_mode"),
                    )
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": "Missing queue id."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                canceled = storage.cancel_queue_item(queue_id)
                result = "OK" if canceled else "NOT_FOUND"
                audit = storage.record_operator_action(
                    operator=identity["operator"],
                    action="QUEUE_CANCEL",
                    payload={"id": queue_id},
                    result=result,
                    actor=identity["actor"],
                    username=identity.get("username"),
                    role=identity.get("role"),
                    auth_mode=identity.get("auth_mode"),
                )
                _json_response(
                    self,
                    {"ok": canceled, "id": queue_id, "result": result, "audit": audit.to_dict()},
                    status=HTTPStatus.OK,
                )
                return

            if path == "/api/library_index/upload_xml":
                payload: Dict[str, Any] = {}
                identity = self._authorize_write(
                    action="LIBRARY_XML_UPLOAD",
                    payload=payload,
                    required_role="operator",
                )
                if identity is None:
                    return
                ok_upload, xml_bytes, detail = self._read_multipart_upload()
                if not ok_upload or xml_bytes is None:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": detail or "Invalid upload."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                try:
                    upload_meta = storage.save_library_xml(xml_bytes)
                except ValueError as exc:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": str(exc)},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                audit = storage.record_operator_action(
                    operator=identity["operator"],
                    action="LIBRARY_XML_UPLOAD",
                    payload=upload_meta,
                    result="OK",
                    actor=identity["actor"],
                    username=identity.get("username"),
                    role=identity.get("role"),
                    auth_mode=identity.get("auth_mode"),
                )
                _json_response(self, {"ok": True, "upload": upload_meta, "audit": audit.to_dict()})
                return

            if path == "/api/library_index/rebuild":
                ok_body, payload = self._read_json_body()
                if not ok_body:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": "Invalid JSON body."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                identity = self._authorize_write(
                    action="LIBRARY_INDEX_REBUILD",
                    payload=payload,
                    required_role="operator",
                )
                if identity is None:
                    return
                try:
                    rebuild_meta = storage.rebuild_library_index()
                except ValueError as exc:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": str(exc)},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                audit = storage.record_operator_action(
                    operator=identity["operator"],
                    action="LIBRARY_INDEX_REBUILD",
                    payload=rebuild_meta,
                    result="OK",
                    actor=identity["actor"],
                    username=identity.get("username"),
                    role=identity.get("role"),
                    auth_mode=identity.get("auth_mode"),
                )
                _json_response(self, {"ok": True, "status": rebuild_meta, "audit": audit.to_dict()})
                return

            if path == "/api/providers/set_active":
                ok_body, payload = self._read_json_body()
                if not ok_body:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": "Invalid JSON body."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                identity = self._authorize_write(
                    action="PROVIDER_SET_ACTIVE",
                    payload=payload,
                    required_role="operator",
                )
                if identity is None:
                    return
                provider = str(payload.get("provider", "")).strip().lower()
                if not provider:
                    storage.record_operator_action(
                        operator=identity["operator"],
                        action="PROVIDER_SET_ACTIVE",
                        payload=payload,
                        result="INVALID_PAYLOAD: missing provider",
                        actor=identity["actor"],
                        username=identity.get("username"),
                        role=identity.get("role"),
                        auth_mode=identity.get("auth_mode"),
                    )
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": "Missing provider."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                try:
                    diff_payload = storage.set_active_provider(provider)
                except ValueError as exc:
                    storage.record_operator_action(
                        operator=identity["operator"],
                        action="PROVIDER_SET_ACTIVE",
                        payload={"provider": provider},
                        result=f"INVALID_PAYLOAD: {exc}",
                        actor=identity["actor"],
                        username=identity.get("username"),
                        role=identity.get("role"),
                        auth_mode=identity.get("auth_mode"),
                    )
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": str(exc)},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                audit = storage.record_operator_action(
                    operator=identity["operator"],
                    action="PROVIDER_SET_ACTIVE",
                    payload={"provider": provider, **diff_payload},
                    result="OK",
                    actor=identity["actor"],
                    username=identity.get("username"),
                    role=identity.get("role"),
                    auth_mode=identity.get("auth_mode"),
                )
                _json_response(
                    self,
                    {"ok": True, "status": storage.get_providers_status(), "audit": audit.to_dict()},
                )
                return

            if path in {"/control/routing", "/api/control/routing"}:
                ok_body, payload = self._read_json_body()
                if not ok_body:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": "Invalid JSON body."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                identity = self._authorize_write(
                    action="CONTROL_ROUTING_SET",
                    payload=payload,
                    required_role="operator",
                )
                if identity is None:
                    return
                if "enabled" not in payload:
                    storage.record_operator_action(
                        operator=identity["operator"],
                        action="CONTROL_ROUTING_SET",
                        payload=payload,
                        result="INVALID_PAYLOAD: missing enabled",
                        actor=identity["actor"],
                        username=identity.get("username"),
                        role=identity.get("role"),
                        auth_mode=identity.get("auth_mode"),
                    )
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": "Missing enabled boolean."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                enabled = bool(payload.get("enabled"))
                diff_payload = storage.set_routing_config({"enabled": enabled})
                audit = storage.record_operator_action(
                    operator=identity["operator"],
                    action="CONTROL_ROUTING_SET",
                    payload={"enabled": enabled, **diff_payload},
                    result="OK",
                    actor=identity["actor"],
                    username=identity.get("username"),
                    role=identity.get("role"),
                    auth_mode=identity.get("auth_mode"),
                )
                _json_response(
                    self,
                    {"ok": True, "status": storage.get_routing_status(), "audit": audit.to_dict()},
                )
                return

            if path in {"/control/director", "/api/control/director"}:
                ok_body, payload = self._read_json_body()
                if not ok_body:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": "Invalid JSON body."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                identity = self._authorize_write(
                    action="CONTROL_DIRECTOR_SET",
                    payload=payload,
                    required_role="operator",
                )
                if identity is None:
                    return
                active = str(payload.get("active", "")).strip()
                if not active:
                    storage.record_operator_action(
                        operator=identity["operator"],
                        action="CONTROL_DIRECTOR_SET",
                        payload=payload,
                        result="INVALID_PAYLOAD: missing active",
                        actor=identity["actor"],
                        username=identity.get("username"),
                        role=identity.get("role"),
                        auth_mode=identity.get("auth_mode"),
                    )
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": "Missing active director."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                try:
                    diff_payload = storage.set_active_director(active)
                except ValueError as exc:
                    storage.record_operator_action(
                        operator=identity["operator"],
                        action="CONTROL_DIRECTOR_SET",
                        payload=payload,
                        result=f"INVALID_PAYLOAD: {exc}",
                        actor=identity["actor"],
                        username=identity.get("username"),
                        role=identity.get("role"),
                        auth_mode=identity.get("auth_mode"),
                    )
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": str(exc)},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                audit = storage.record_operator_action(
                    operator=identity["operator"],
                    action="CONTROL_DIRECTOR_SET",
                    payload={"active": diff_payload.get("new"), **diff_payload},
                    result="OK",
                    actor=identity["actor"],
                    username=identity.get("username"),
                    role=identity.get("role"),
                    auth_mode=identity.get("auth_mode"),
                )
                _json_response(
                    self,
                    {"ok": True, "status": storage.get_status().to_dict(), "audit": audit.to_dict()},
                )
                return

            if path in {"/control/dry_run", "/api/control/dry_run"}:
                ok_body, payload = self._read_json_body()
                if not ok_body:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": "Invalid JSON body."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                identity = self._authorize_write(
                    action="CONTROL_DRY_RUN_SET",
                    payload=payload,
                    required_role="operator",
                )
                if identity is None:
                    return
                if "enabled" not in payload:
                    storage.record_operator_action(
                        operator=identity["operator"],
                        action="CONTROL_DRY_RUN_SET",
                        payload=payload,
                        result="INVALID_PAYLOAD: missing enabled",
                        actor=identity["actor"],
                        username=identity.get("username"),
                        role=identity.get("role"),
                        auth_mode=identity.get("auth_mode"),
                    )
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": "Missing enabled boolean."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                enabled = bool(payload.get("enabled"))
                diff_payload = storage.set_dry_run(enabled)
                audit = storage.record_operator_action(
                    operator=identity["operator"],
                    action="CONTROL_DRY_RUN_SET",
                    payload={"enabled": enabled, **diff_payload},
                    result="OK",
                    actor=identity["actor"],
                    username=identity.get("username"),
                    role=identity.get("role"),
                    auth_mode=identity.get("auth_mode"),
                )
                _json_response(
                    self,
                    {"ok": True, "status": storage.get_status().to_dict(), "audit": audit.to_dict()},
                )
                return

            _json_response(
                self,
                {"ok": False, "error": "not_found", "path": path},
                status=HTTPStatus.NOT_FOUND,
            )

        def do_PUT(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/studio_profile":
                self._handle_studio_profile_write(patch=False)
                return
            if parsed.path == "/api/inner_circle":
                self._handle_inner_circle_write(patch=False)
                return
            _json_response(
                self,
                {"ok": False, "error": "not_found", "path": parsed.path},
                status=HTTPStatus.NOT_FOUND,
            )

        def do_PATCH(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/studio_profile":
                self._handle_studio_profile_write(patch=True)
                return
            if parsed.path == "/api/inner_circle":
                self._handle_inner_circle_write(patch=True)
                return
            if parsed.path.startswith("/api/memory/cultural/"):
                ok_body, payload = self._read_json_body()
                if not ok_body:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": "Invalid JSON body."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                note_id = parsed.path[len("/api/memory/cultural/") :].strip()
                if not note_id:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": "Missing cultural note id."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                identity = self._authorize_write(
                    action="MEMORY_CULTURAL_UPDATE",
                    payload={**payload, "id": note_id},
                    required_role="operator",
                )
                if identity is None:
                    return
                try:
                    item = storage.update_memory_cultural(
                        note_id,
                        payload,
                        username=self._identity_memory_username(identity),
                        role=identity.get("role"),
                        auth_mode=identity.get("auth_mode"),
                    )
                except ValueError as exc:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": str(exc)},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                except KeyError:
                    _json_response(
                        self,
                        {"ok": False, "error": "not_found", "detail": "Cultural note not found."},
                        status=HTTPStatus.NOT_FOUND,
                    )
                    return
                audit = storage.record_operator_action(
                    operator=identity["operator"],
                    action="MEMORY_CULTURAL_UPDATE",
                    payload={"id": note_id, **payload},
                    result="OK",
                    actor=identity["actor"],
                    username=identity.get("username"),
                    role=identity.get("role"),
                    auth_mode=identity.get("auth_mode"),
                )
                _json_response(self, {"ok": True, "item": item, "audit": audit.to_dict()})
                return
            if parsed.path.startswith("/api/memory/viewer/"):
                ok_body, payload = self._read_json_body()
                if not ok_body:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": "Invalid JSON body."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                note_id = parsed.path[len("/api/memory/viewer/") :].strip()
                if not note_id:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": "Missing viewer note id."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                identity = self._authorize_write(
                    action="MEMORY_VIEWER_UPDATE",
                    payload={**payload, "id": note_id},
                    required_role="operator",
                )
                if identity is None:
                    return
                try:
                    item = storage.update_memory_viewer(
                        note_id,
                        payload,
                        username=self._identity_memory_username(identity),
                        role=identity.get("role"),
                        auth_mode=identity.get("auth_mode"),
                    )
                except ValueError as exc:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": str(exc)},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                except KeyError:
                    _json_response(
                        self,
                        {"ok": False, "error": "not_found", "detail": "Viewer note not found."},
                        status=HTTPStatus.NOT_FOUND,
                    )
                    return
                audit = storage.record_operator_action(
                    operator=identity["operator"],
                    action="MEMORY_VIEWER_UPDATE",
                    payload={"id": note_id, **payload},
                    result="OK",
                    actor=identity["actor"],
                    username=identity.get("username"),
                    role=identity.get("role"),
                    auth_mode=identity.get("auth_mode"),
                )
                _json_response(self, {"ok": True, "item": item, "audit": audit.to_dict()})
                return
            if parsed.path == "/api/providers/caps":
                ok_body, payload = self._read_json_body()
                if not ok_body:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": "Invalid JSON body."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                identity = self._authorize_write(
                    action="PROVIDER_SET_CAPS",
                    payload=payload,
                    required_role="operator",
                )
                if identity is None:
                    return
                role_for_caps = identity.get("role")
                if not role_for_caps:
                    role_for_caps = "director" if str(identity.get("actor", "")).strip().lower() == "art" else "operator"
                try:
                    diff_payload = storage.set_provider_caps(
                        payload,
                        role=role_for_caps,
                    )
                except PermissionError as exc:
                    storage.record_operator_action(
                        operator=identity["operator"],
                        action="PROVIDER_SET_CAPS",
                        payload=payload,
                        result=f"DENIED: {exc}",
                        actor=identity["actor"],
                        username=identity.get("username"),
                        role=identity.get("role"),
                        auth_mode=identity.get("auth_mode"),
                    )
                    _json_response(
                        self,
                        {"ok": False, "error": "forbidden", "detail": str(exc)},
                        status=HTTPStatus.FORBIDDEN,
                    )
                    return
                except ValueError as exc:
                    storage.record_operator_action(
                        operator=identity["operator"],
                        action="PROVIDER_SET_CAPS",
                        payload=payload,
                        result=f"INVALID_PAYLOAD: {exc}",
                        actor=identity["actor"],
                        username=identity.get("username"),
                        role=identity.get("role"),
                        auth_mode=identity.get("auth_mode"),
                    )
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": str(exc)},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                audit = storage.record_operator_action(
                    operator=identity["operator"],
                    action="PROVIDER_SET_CAPS",
                    payload=diff_payload,
                    result="OK",
                    actor=identity["actor"],
                    username=identity.get("username"),
                    role=identity.get("role"),
                    auth_mode=identity.get("auth_mode"),
                )
                _json_response(
                    self,
                    {"ok": True, "status": storage.get_providers_status(), "audit": audit.to_dict()},
                )
                return
            if parsed.path == "/api/routing/config":
                ok_body, payload = self._read_json_body()
                if not ok_body:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": "Invalid JSON body."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                identity = self._authorize_write(
                    action="ROUTING_CONFIG_UPDATE",
                    payload=payload,
                    required_role="operator",
                )
                if identity is None:
                    return
                role_for_routing = identity.get("role")
                if not role_for_routing:
                    role_for_routing = (
                        "director" if str(identity.get("actor", "")).strip().lower() == "art" else "operator"
                    )
                if not storage.role_allows(role_for_routing, "director"):
                    storage.record_operator_action(
                        operator=identity["operator"],
                        action="ROUTING_CONFIG_UPDATE",
                        payload=payload,
                        result="DENIED: director role required.",
                        actor=identity["actor"],
                        username=identity.get("username"),
                        role=identity.get("role"),
                        auth_mode=identity.get("auth_mode"),
                    )
                    _json_response(
                        self,
                        {"ok": False, "error": "forbidden", "detail": "Director role required."},
                        status=HTTPStatus.FORBIDDEN,
                    )
                    return
                try:
                    diff_payload = storage.set_routing_config(payload)
                except ValueError as exc:
                    storage.record_operator_action(
                        operator=identity["operator"],
                        action="ROUTING_CONFIG_UPDATE",
                        payload=payload,
                        result=f"INVALID_PAYLOAD: {exc}",
                        actor=identity["actor"],
                        username=identity.get("username"),
                        role=identity.get("role"),
                        auth_mode=identity.get("auth_mode"),
                    )
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": str(exc)},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                audit = storage.record_operator_action(
                    operator=identity["operator"],
                    action="ROUTING_CONFIG_UPDATE",
                    payload=diff_payload,
                    result="OK",
                    actor=identity["actor"],
                    username=identity.get("username"),
                    role=identity.get("role"),
                    auth_mode=identity.get("auth_mode"),
                )
                _json_response(
                    self,
                    {"ok": True, "status": storage.get_routing_status(), "audit": audit.to_dict()},
                )
                return
            _json_response(
                self,
                {"ok": False, "error": "not_found", "path": parsed.path},
                status=HTTPStatus.NOT_FOUND,
            )

        def do_DELETE(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/memory/cultural/"):
                note_id = parsed.path[len("/api/memory/cultural/") :].strip()
                if not note_id:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": "Missing cultural note id."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                identity = self._authorize_write(
                    action="MEMORY_CULTURAL_DELETE",
                    payload={"id": note_id},
                    required_role="operator",
                )
                if identity is None:
                    return
                try:
                    deleted = storage.delete_memory_cultural(
                        note_id,
                        username=self._identity_memory_username(identity),
                        role=identity.get("role"),
                        auth_mode=identity.get("auth_mode"),
                    )
                except ValueError as exc:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": str(exc)},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                except KeyError:
                    _json_response(
                        self,
                        {"ok": False, "error": "not_found", "detail": "Cultural note not found."},
                        status=HTTPStatus.NOT_FOUND,
                    )
                    return
                audit = storage.record_operator_action(
                    operator=identity["operator"],
                    action="MEMORY_CULTURAL_DELETE",
                    payload={"id": note_id},
                    result="OK",
                    actor=identity["actor"],
                    username=identity.get("username"),
                    role=identity.get("role"),
                    auth_mode=identity.get("auth_mode"),
                )
                _json_response(self, {"ok": True, "deleted": deleted, "audit": audit.to_dict()})
                return

            if parsed.path.startswith("/api/memory/viewer/"):
                note_id = parsed.path[len("/api/memory/viewer/") :].strip()
                if not note_id:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": "Missing viewer note id."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                identity = self._authorize_write(
                    action="MEMORY_VIEWER_DELETE",
                    payload={"id": note_id},
                    required_role="operator",
                )
                if identity is None:
                    return
                try:
                    deleted = storage.delete_memory_viewer(
                        note_id,
                        username=self._identity_memory_username(identity),
                        role=identity.get("role"),
                        auth_mode=identity.get("auth_mode"),
                    )
                except ValueError as exc:
                    _json_response(
                        self,
                        {"ok": False, "error": "bad_request", "detail": str(exc)},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                except KeyError:
                    _json_response(
                        self,
                        {"ok": False, "error": "not_found", "detail": "Viewer note not found."},
                        status=HTTPStatus.NOT_FOUND,
                    )
                    return
                audit = storage.record_operator_action(
                    operator=identity["operator"],
                    action="MEMORY_VIEWER_DELETE",
                    payload={"id": note_id},
                    result="OK",
                    actor=identity["actor"],
                    username=identity.get("username"),
                    role=identity.get("role"),
                    auth_mode=identity.get("auth_mode"),
                )
                _json_response(self, {"ok": True, "deleted": deleted, "audit": audit.to_dict()})
                return

            _json_response(
                self,
                {"ok": False, "error": "not_found", "path": parsed.path},
                status=HTTPStatus.NOT_FOUND,
            )

    return DashboardHandler


def create_server(
    *,
    host: str = "0.0.0.0",
    port: int = 8787,
    runs_dir: Path | None = None,
    readiness_state: Dict[str, Any] | None = None,
) -> ThreadingHTTPServer:
    storage = DashboardStorage(runs_dir=runs_dir, readiness_state=readiness_state)
    handler_cls = build_handler(storage)
    server = ThreadingHTTPServer((host, port), handler_cls)
    setattr(server, "_roonie_storage", storage)
    return server


def _arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="roonie-dashboard-api")
    p.add_argument("--host", default=os.getenv("ROONIE_DASHBOARD_HOST", "0.0.0.0"))
    p.add_argument("--port", type=int, default=int(os.getenv("ROONIE_DASHBOARD_PORT", "8787")))
    p.add_argument("--runs-dir", default=os.getenv("ROONIE_DASHBOARD_RUNS_DIR", "runs"))
    return p


def main(argv: list[str] | None = None) -> int:
    args = _arg_parser().parse_args(argv)
    runs_dir = Path(args.runs_dir)
    if not runs_dir.is_absolute():
        runs_dir = Path.cwd() / runs_dir

    server = create_server(host=args.host, port=args.port, runs_dir=runs_dir)
    print(f"Dashboard API listening on http://{args.host}:{args.port}")
    print(f"Using runs directory: {runs_dir}")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
