from __future__ import annotations

import json
import io
import os
import threading
import hashlib
import sqlite3
import re
import secrets
import base64
import unicodedata
import zipfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from copy import deepcopy
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlencode, quote_plus
from uuid import uuid4

from providers.router import (
    get_resolved_model_config,
    get_provider_runtime_metrics,
    get_runtime_config_paths,
    get_provider_runtime_status,
    get_routing_runtime_status,
    set_provider_active,
    update_routing_runtime_controls,
    update_provider_caps,
)

from .models import EventResponse, OperatorLogResponse, StatusResponse


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _safe_read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return _read_json(path)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None


def _to_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _env_bool(names: List[str], default: bool) -> bool:
    for name in names:
        if name in os.environ:
            return _to_bool(os.getenv(name), default)
    return default


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    raw = str(ts).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _format_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _hms(ts: Optional[str]) -> str:
    dt = _parse_iso(ts)
    if dt is None:
        return "--:--:--"
    return dt.astimezone(timezone.utc).strftime("%H:%M:%S")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"), salt, 200_000)
    return base64.b64encode(salt + dk).decode("ascii")


def verify_password(password: str, stored: str) -> bool:
    try:
        raw = base64.b64decode(str(stored).encode("ascii"), validate=True)
    except Exception:
        return False
    if len(raw) != 48:
        return False
    salt = raw[:16]
    stored_key = raw[16:]
    new_key = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"), salt, 200_000)
    return secrets.compare_digest(new_key, stored_key)


def _is_legacy_password_hash(stored: str) -> bool:
    text = str(stored or "").strip()
    if not text:
        return True
    if "$" in text:
        return True
    try:
        raw = base64.b64decode(text.encode("ascii"), validate=True)
    except Exception:
        return True
    return len(raw) != 48


def _looks_like_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _trim_for_audit(value: Any, max_len: int = 180) -> str:
    text = _canonical_json(value)
    return text if len(text) <= max_len else (text[: max_len - 3] + "...")


def _normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower().strip()
    text = re.sub(r"[^\w\s]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _build_track_search_key(artist: str, title: str) -> str:
    return _normalize_text(f"{artist} - {title}".strip())


class LoginRateLimiter:
    """In-memory per-key failed attempt tracker with TTL lockout."""

    def __init__(self, max_attempts: int = 5, lockout_seconds: float = 60.0) -> None:
        self._max_attempts = max(1, int(max_attempts))
        self._lockout_seconds = max(0.001, float(lockout_seconds))
        self._attempts: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent_attempts(self, key: str, now: float) -> list[float]:
        cutoff = now - self._lockout_seconds
        attempts = self._attempts.get(key, [])
        recent = [t for t in attempts if t > cutoff]
        self._attempts[key] = recent
        return recent

    def is_locked_out(self, key: str) -> bool:
        with self._lock:
            recent = self._recent_attempts(str(key), time.monotonic())
            return len(recent) >= self._max_attempts

    def record_failure(self, key: str) -> None:
        with self._lock:
            norm_key = str(key)
            now = time.monotonic()
            recent = self._recent_attempts(norm_key, now)
            recent.append(now)
            self._attempts[norm_key] = recent

    def reset(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(str(key), None)


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    class _DpapiDataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
        ]

    _CRYPTPROTECT_UI_FORBIDDEN = 0x01

    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32
    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DpapiDataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DpapiDataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DpapiDataBlob),
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DpapiDataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DpapiDataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DpapiDataBlob),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p

    def _dpapi_blob_from_bytes(raw: bytes) -> Tuple[_DpapiDataBlob, Any]:
        src = bytes(raw or b"")
        if src:
            arr = (ctypes.c_ubyte * len(src)).from_buffer_copy(src)
            blob = _DpapiDataBlob(len(src), ctypes.cast(arr, ctypes.POINTER(ctypes.c_ubyte)))
            return blob, arr
        arr = (ctypes.c_ubyte * 1)()
        blob = _DpapiDataBlob(0, ctypes.cast(arr, ctypes.POINTER(ctypes.c_ubyte)))
        return blob, arr

    def _dpapi_protect_bytes(raw: bytes) -> bytes:
        in_blob, in_buf = _dpapi_blob_from_bytes(raw)
        out_blob = _DpapiDataBlob()
        if not _crypt32.CryptProtectData(
            ctypes.byref(in_blob),
            "roonie.twitch.auth.state",
            None,
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(out_blob),
        ):
            raise OSError(f"CryptProtectData failed: {ctypes.WinError()}")
        _ = in_buf
        try:
            if not out_blob.pbData or int(out_blob.cbData) <= 0:
                return b""
            return ctypes.string_at(out_blob.pbData, int(out_blob.cbData))
        finally:
            if out_blob.pbData:
                _kernel32.LocalFree(out_blob.pbData)

    def _dpapi_unprotect_bytes(raw: bytes) -> bytes:
        in_blob, in_buf = _dpapi_blob_from_bytes(raw)
        out_blob = _DpapiDataBlob()
        descr = wintypes.LPWSTR()
        if not _crypt32.CryptUnprotectData(
            ctypes.byref(in_blob),
            ctypes.byref(descr),
            None,
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(out_blob),
        ):
            raise OSError(f"CryptUnprotectData failed: {ctypes.WinError()}")
        _ = in_buf
        try:
            if not out_blob.pbData or int(out_blob.cbData) <= 0:
                return b""
            return ctypes.string_at(out_blob.pbData, int(out_blob.cbData))
        finally:
            if out_blob.pbData:
                _kernel32.LocalFree(out_blob.pbData)
            if descr:
                _kernel32.LocalFree(descr)
else:

    def _dpapi_protect_bytes(raw: bytes) -> bytes:
        raise RuntimeError("DPAPI is only available on Windows.")

    def _dpapi_unprotect_bytes(raw: bytes) -> bytes:
        raise RuntimeError("DPAPI is only available on Windows.")


def _default_studio_profile(*, updated_by: str) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "version": 1,
        "updated_at": now,
        "updated_by": updated_by,
        "location": {"display": "Washington DC area"},
        "social_links": [
            {"label": "Twitch", "url": "https://twitch.tv/ruleofrune"},
            {"label": "TikTok", "url": "https://tiktok.com/@ruleofrune"},
        ],
        "gear": [],
        "faq": [
            {"q": "Where are you based?", "a": "Washington DC area."},
        ],
        "approved_emotes": [
            {"name": "RoonieWave", "desc": "", "denied": False},
            {"name": "RoonieHi", "desc": "", "denied": False},
        ],
    }


class DashboardStorage:
    _KILL_SWITCH_ENV_NAMES = ("ROONIE_KILL_SWITCH", "KILL_SWITCH", "ROONIE_KILL_SWITCH_ON")
    _DRY_RUN_ENV_NAMES = ("ROONIE_DRY_RUN", "ROONIE_READ_ONLY_MODE")

    def __init__(self, runs_dir: Optional[Path] = None, readiness_state: Optional[Dict[str, Any]] = None) -> None:
        root = _repo_root()
        self.runs_dir = runs_dir or (root / "runs")
        logs_dir_env = (os.getenv("ROONIE_DASHBOARD_LOGS_DIR") or "").strip()
        self.logs_dir = (Path(logs_dir_env) if logs_dir_env else (root / "logs")).resolve()
        data_dir_env = (os.getenv("ROONIE_DASHBOARD_DATA_DIR") or "").strip()
        self.data_dir = (Path(data_dir_env) if data_dir_env else (root / "data")).resolve()
        self._lock = threading.Lock()
        self._queue: List[Dict[str, Any]] = []
        self._control_state_path = self.data_dir / "control_state.json"
        self._senses_config_path = self.data_dir / "senses_config.json"
        self._twitch_config_path = self.data_dir / "twitch_config.json"
        self._studio_profile_path = self.data_dir / "studio_profile.json"
        self._inner_circle_path = self.data_dir / "inner_circle.json"
        self._stream_schedule_path = self.data_dir / "stream_schedule.json"
        self._audio_config_path = self.data_dir / "audio_config.json"
        self._library_dir = self.data_dir / "library"
        self._library_xml_path = self._library_dir / "rekordbox.xml"
        self._library_index_path = self._library_dir / "library_index.json"
        self._library_meta_path = self._library_dir / "library_meta.json"
        self._legacy_control_state_path = self.logs_dir / "dashboard_control_state.json"
        self._audit_log_path = self.logs_dir / "operator_audit.jsonl"
        self._eventsub_events_log_path = self.logs_dir / "eventsub_events.jsonl"
        self.auth_users_path = Path(os.path.join(str(self.data_dir), "auth_users.json")).resolve()
        self._twitch_auth_state_path = self.data_dir / "twitch_auth_state.json"
        self._memory_db_path = self.data_dir / "memory.sqlite"
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._session_ttl_seconds = self._session_ttl_seconds_from_env()
        self._retention_enabled = self._retention_enabled_from_env()
        self._retention_days = self._retention_days_from_env()
        self._retention_check_interval_seconds = self._retention_check_interval_seconds_from_env()
        self._retention_last_run_monotonic: float = 0.0
        self._kill_switch_env_pinned = any(name in os.environ for name in self._KILL_SWITCH_ENV_NAMES)
        self._kill_switch_env_pinned_true = self._kill_switch_env_pinned and _env_bool(list(self._KILL_SWITCH_ENV_NAMES), False)
        self._dashboard_kill_switch: bool = False
        self._dry_run_env_pinned = any(name in os.environ for name in self._DRY_RUN_ENV_NAMES)
        self._readiness_state: Dict[str, Any] = {
            "ready": False,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "items": [],
            "blocking_reasons": ["preflight_not_run"],
        }
        self._eventsub_runtime_state: Dict[str, Any] = {
            "eventsub_connected": False,
            "eventsub_session_id": None,
            "last_eventsub_message_ts": None,
            "reconnect_count": 0,
            "eventsub_last_error": None,
        }
        self._audio_runtime_state: Dict[str, Any] = {}
        self._send_failure_state: Dict[str, Any] = {
            "fail_count": 0,
            "last_fail_reason": None,
            "last_fail_at": None,
            "last_success_at": None,
        }
        self._twitch_status_cache: Optional[Dict[str, Any]] = None
        self._twitch_status_cache_expiry_ts: float = 0.0
        self._status_runtime: Dict[str, Any] = {
            "last_heartbeat_at": None,
            "active_provider": "none",
            "version": os.getenv("ROONIE_VERSION", "unknown"),
            "mode": os.getenv("ROONIE_MODE", "offline"),
            "context_last_active": False,
            "context_last_turns_used": 0,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._control_state = self._load_control_state()
        self._init_memory_db()
        self._ensure_senses_config()
        self._ensure_twitch_config()
        with self._lock:
            self._twitch_runtime_config_locked()
        auth_payload = self._read_or_seed_auth_users_locked()
        auth_users = [
            str(item.get("username", "")).strip().lower()
            for item in auth_payload.get("users", [])
            if isinstance(item, dict)
        ]
        print(
            "[D6 auth] init "
            f"data_dir={self.data_dir} "
            f"auth_users_path={self.auth_users_path} "
            f"usernames={auth_users}"
        )
        self._prime_status_runtime_from_runs()
        self._sync_env_from_state()
        self._apply_retention_policy(force=True)
        if isinstance(readiness_state, dict):
            self.set_readiness_state(readiness_state)

    def is_read_only_mode(self) -> bool:
        """DRY_RUN flag (suppresses outbound posting attempts).

        This is intentionally NOT tied to dashboard auth. Session-auth can be enabled
        without ROONIE_OPERATOR_KEY, so we treat read_only_mode as an explicit runtime
        control instead.
        """
        return bool(self._dry_run_from_env_or_state())

    def _dry_run_from_env_or_state(self) -> bool:
        # Explicit env override, if provided.
        for name in self._DRY_RUN_ENV_NAMES:
            if name in os.environ:
                return _to_bool(os.getenv(name), False)
        with self._lock:
            return bool(self._control_state.get("dry_run", False))

    def set_dry_run(self, enabled: bool) -> Dict[str, Any]:
        enabled_bool = bool(enabled)
        with self._lock:
            prev = bool(self._control_state.get("dry_run", False))
            self._control_state["dry_run"] = enabled_bool
            self._save_control_state_locked()
            self._sync_env_from_state_locked()
        return {"old": prev, "new": enabled_bool}

    @staticmethod
    def validate_operator_key(header_value: Optional[str]) -> Tuple[bool, str]:
        configured = (os.getenv("ROONIE_OPERATOR_KEY") or "").strip()
        if not configured:
            return False, "API is READ-ONLY: set ROONIE_OPERATOR_KEY to enable write actions."
        if not secrets.compare_digest((header_value or "").strip(), configured):
            return False, "Forbidden: invalid X-ROONIE-OP-KEY."
        return True, "ok"

    @staticmethod
    def normalize_actor(actor: Optional[str]) -> str:
        val = str(actor or "").strip().lower()
        if val in {"jen", "art", "system", "unknown"}:
            return val
        return "unknown"

    @staticmethod
    def normalize_role(role: Optional[str]) -> str:
        value = str(role or "").strip().lower()
        if value in {"operator", "director"}:
            return value
        return "operator"

    @staticmethod
    def normalize_active_director(value: Optional[str]) -> str:
        raw = str(value or "").strip().lower()
        if raw in {"providerdirector", "provider", "live"}:
            return "ProviderDirector"
        if raw in {"offlinedirector", "offline"}:
            return "OfflineDirector"
        return "ProviderDirector"

    @staticmethod
    def _session_ttl_seconds_from_env() -> int:
        raw = os.getenv("ROONIE_DASHBOARD_SESSION_TTL_SECONDS", "43200")
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            return 43200
        return max(1, parsed)

    def session_ttl_seconds(self) -> int:
        return int(self._session_ttl_seconds)

    @staticmethod
    def _retention_enabled_from_env() -> bool:
        raw = os.getenv("ROONIE_RETENTION_ENABLED", "1")
        return _to_bool(raw, True)

    @staticmethod
    def _retention_days_from_env() -> int:
        raw = os.getenv("ROONIE_RETENTION_DAYS", os.getenv("ROONIE_DATA_RETENTION_DAYS", "180"))
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            parsed = 180
        return max(1, min(parsed, 3650))

    @staticmethod
    def _retention_check_interval_seconds_from_env() -> float:
        raw = os.getenv("ROONIE_RETENTION_CHECK_INTERVAL_SECONDS", "300")
        try:
            parsed = float(raw)
        except (TypeError, ValueError):
            parsed = 300.0
        return max(1.0, min(parsed, 86400.0))

    def _purge_old_run_files_locked(self, cutoff_dt: datetime) -> int:
        if not self.runs_dir.exists():
            return 0
        removed = 0
        cutoff_ts = cutoff_dt.timestamp()
        for path in self.runs_dir.glob("*.json"):
            if not path.is_file():
                continue
            try:
                if path.stat().st_mtime < cutoff_ts:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        return removed

    def _prune_jsonl_by_ts_locked(self, path: Path, cutoff_dt: datetime) -> int:
        if not path.exists():
            return 0
        try:
            raw_lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return 0

        dropped = 0
        kept: List[str] = []
        for line in raw_lines:
            text = str(line or "").strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                kept.append(text)
                continue
            if not isinstance(obj, dict):
                kept.append(text)
                continue
            ts = _parse_iso(str(obj.get("ts", "")).strip())
            if ts is not None and ts < cutoff_dt:
                dropped += 1
                continue
            kept.append(text)

        if dropped <= 0:
            return 0
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            payload = ("\n".join(kept) + "\n") if kept else ""
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(path)
        except OSError:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            return 0
        return dropped

    def _apply_retention_policy(self, *, force: bool = False) -> Dict[str, int]:
        if not self._retention_enabled:
            return {"runs_removed": 0, "audit_rows_removed": 0, "eventsub_rows_removed": 0}
        with self._lock:
            now_mono = time.monotonic()
            if (
                (not force)
                and self._retention_last_run_monotonic > 0.0
                and (now_mono - self._retention_last_run_monotonic) < self._retention_check_interval_seconds
            ):
                return {"runs_removed": 0, "audit_rows_removed": 0, "eventsub_rows_removed": 0}
            cutoff_dt = datetime.now(timezone.utc) - timedelta(days=self._retention_days)
            runs_removed = self._purge_old_run_files_locked(cutoff_dt)
            audit_rows_removed = self._prune_jsonl_by_ts_locked(self._audit_log_path, cutoff_dt)
            eventsub_rows_removed = self._prune_jsonl_by_ts_locked(self._eventsub_events_log_path, cutoff_dt)
            self._retention_last_run_monotonic = now_mono
        return {
            "runs_removed": runs_removed,
            "audit_rows_removed": audit_rows_removed,
            "eventsub_rows_removed": eventsub_rows_removed,
        }

    @staticmethod
    def role_allows(role: Optional[str], required_role: str) -> bool:
        rank = {"operator": 1, "director": 2}
        have = rank.get(DashboardStorage.normalize_role(role), 0)
        need = rank.get(DashboardStorage.normalize_role(required_role), 1)
        return have >= need

    def set_readiness_state(self, readiness_state: Dict[str, Any]) -> None:
        if not isinstance(readiness_state, dict):
            return
        items = readiness_state.get("items", [])
        reasons = readiness_state.get("blocking_reasons", [])
        with self._lock:
            self._readiness_state = {
                "ready": bool(readiness_state.get("ready", False)),
                "checked_at": str(readiness_state.get("checked_at") or datetime.now(timezone.utc).isoformat()),
                "items": list(items) if isinstance(items, list) else [],
                "blocking_reasons": [str(item) for item in reasons] if isinstance(reasons, list) else [],
            }

    def get_readiness_state(self) -> Dict[str, Any]:
        with self._lock:
            payload = deepcopy(self._readiness_state)

        degraded_reason: Optional[str] = None
        try:
            with sqlite3.connect(str(self._memory_db_path)) as conn:
                conn.execute("SELECT 1").fetchone()
        except sqlite3.Error:
            degraded_reason = "memory_db_unreachable"

        if degraded_reason:
            payload["ready"] = False
            reasons = payload.get("blocking_reasons", [])
            if not isinstance(reasons, list):
                reasons = []
            if degraded_reason not in reasons:
                reasons.append(degraded_reason)
            payload["blocking_reasons"] = reasons
            items = payload.get("items", [])
            if not isinstance(items, list):
                items = []
            items.append({"name": "runtime_memory_db", "ok": False, "detail": degraded_reason})
            payload["items"] = items
        return payload

    def _default_auth_users_payload(self) -> Dict[str, Any]:
        art_password = os.getenv("ROONIE_DASHBOARD_ART_PASSWORD")
        jen_password = os.getenv("ROONIE_DASHBOARD_JEN_PASSWORD")
        if not art_password:
            art_password = secrets.token_urlsafe(12)
            print("[D6 auth] ROONIE_DASHBOARD_ART_PASSWORD missing; generated temporary password for art (this startup only).")
        if not jen_password:
            jen_password = secrets.token_urlsafe(12)
            print("[D6 auth] ROONIE_DASHBOARD_JEN_PASSWORD missing; generated temporary password for jen (this startup only).")
        return {
            "version": 1,
            "users": [
                {
                    "username": "art",
                    "role": "director",
                    "password_hash": hash_password(art_password),
                },
                {
                    "username": "jen",
                    "role": "operator",
                    "password_hash": hash_password(jen_password),
                },
            ],
        }

    def _normalize_auth_users_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return self._default_auth_users_payload()
        users_raw = payload.get("users", [])
        users: List[Dict[str, str]] = []
        seen: set[str] = set()
        if isinstance(users_raw, list):
            for item in users_raw:
                if not isinstance(item, dict):
                    continue
                username = str(item.get("username", "")).strip().lower()
                role = self.normalize_role(item.get("role"))
                password_hash = str(item.get("password_hash", "")).strip()
                if not username or username in seen:
                    continue
                if not password_hash:
                    continue
                if _is_legacy_password_hash(password_hash):
                    print("[D6 auth] Legacy auth_users hash format detected; reseeding auth_users.json.")
                    return self._default_auth_users_payload()
                users.append(
                    {
                        "username": username,
                        "role": role,
                        "password_hash": password_hash,
                    }
                )
                seen.add(username)
        if users:
            return {"version": 1, "users": users}
        return self._default_auth_users_payload()

    def _read_or_seed_auth_users_locked(self) -> Dict[str, Any]:
        raw = _safe_read_json(self.auth_users_path)
        if isinstance(raw, dict):
            payload = self._normalize_auth_users_payload(raw)
        else:
            payload = self._default_auth_users_payload()
        self._write_json_atomic(self.auth_users_path, payload)
        return payload

    def _auth_user_by_username_locked(self, username: str) -> Optional[Dict[str, str]]:
        payload = self._read_or_seed_auth_users_locked()
        for item in payload.get("users", []):
            if not isinstance(item, dict):
                continue
            if str(item.get("username", "")).strip().lower() == username:
                return {
                    "username": username,
                    "role": self.normalize_role(item.get("role")),
                    "password_hash": str(item.get("password_hash", "")).strip(),
                }
        return None

    def _cleanup_sessions_locked(self) -> None:
        if not self._sessions:
            return
        now = datetime.now(timezone.utc)
        expired: List[str] = []
        for sid, data in self._sessions.items():
            expires_at = _parse_iso(str(data.get("expires_at")))
            if expires_at is None:
                expired.append(sid)
                continue
            if now >= expires_at:
                expired.append(sid)
        for sid in expired:
            self._sessions.pop(sid, None)

    def login_dashboard_user(self, username: str, password: str) -> Optional[Dict[str, str]]:
        user_name = str(username or "").strip().lower()
        raw_password = str(password or "")
        if not user_name or not raw_password:
            return None
        with self._lock:
            user = self._auth_user_by_username_locked(user_name)
            if not user:
                return None
            if not verify_password(raw_password, user.get("password_hash", "")):
                return None
            self._cleanup_sessions_locked()
            sid = secrets.token_urlsafe(32)
            now_dt = datetime.now(timezone.utc)
            now = now_dt.isoformat()
            expires = (now_dt + timedelta(seconds=self._session_ttl_seconds)).isoformat()
            self._sessions[sid] = {
                "username": user_name,
                "role": self.normalize_role(user.get("role")),
                "created_at": now,
                "expires_at": expires,
            }
            return {
                "session_id": sid,
                "username": user_name,
                "role": self.normalize_role(user.get("role")),
            }

    def get_session_user(self, session_id: Optional[str]) -> Optional[Dict[str, str]]:
        sid = str(session_id or "").strip()
        if not sid:
            return None
        with self._lock:
            self._cleanup_sessions_locked()
            session = self._sessions.get(sid)
            if not isinstance(session, dict):
                return None
            expires_at = _parse_iso(str(session.get("expires_at")))
            if expires_at is None or datetime.now(timezone.utc) >= expires_at:
                self._sessions.pop(sid, None)
                return None
            return {
                "username": str(session.get("username", "")).strip().lower(),
                "role": self.normalize_role(session.get("role")),
            }

    def logout_session(self, session_id: Optional[str]) -> None:
        sid = str(session_id or "").strip()
        if not sid:
            return
        with self._lock:
            self._sessions.pop(sid, None)

    @staticmethod
    def effective_blocks(
        *,
        kill_switch_on: bool,
        armed: bool,
        silenced: bool,
        dry_run: bool = False,
        cost_cap_on: bool = False,
    ) -> List[str]:
        blocks: List[str] = []
        if bool(kill_switch_on):
            blocks.append("KILL_SWITCH")
        if not bool(armed):
            blocks.append("DISARMED")
        if bool(dry_run):
            blocks.append("DRY_RUN")
        if bool(silenced):
            blocks.append("SILENCE_TTL")
        return blocks

    @staticmethod
    def effective_can_post(blocked_by: List[str]) -> bool:
        return len(list(blocked_by or [])) == 0

    @staticmethod
    def _default_silence_ttl_seconds() -> int:
        raw = os.getenv("ROONIE_SILENCE_TTL_SECONDS", "300")
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return 300

    def _load_control_state(self) -> Dict[str, Any]:
        state: Dict[str, Any] = {
            # Canon invariant: process starts disarmed every launch.
            "armed": False,
            "output_disabled": True,
            # DRY_RUN/read-only suppresses outbound posting attempts even if armed.
            # Defaults OFF unless explicitly enabled.
            "dry_run": False,
            "silence_until": None,
            "session_id": None,
            "active_director": "ProviderDirector",
        }
        loaded = _safe_read_json(self._control_state_path)
        if not isinstance(loaded, dict):
            loaded = _safe_read_json(self._legacy_control_state_path)
        if isinstance(loaded, dict):
            state["active_director"] = self.normalize_active_director(
                loaded.get("active_director", state.get("active_director"))
            )
            # dry_run is safe to rehydrate from disk.
            if "dry_run" in loaded:
                state["dry_run"] = bool(loaded.get("dry_run", False))
            elif "read_only_mode" in loaded:
                state["dry_run"] = bool(loaded.get("read_only_mode", False))
        return state

    def _reload_control_state_from_file_locked(self) -> None:
        loaded = _safe_read_json(self._control_state_path)
        if not isinstance(loaded, dict):
            loaded = _safe_read_json(self._legacy_control_state_path)
        if not isinstance(loaded, dict):
            return
        # Arm/disarm state is runtime-owned and intentionally not rehydrated from disk.
        self._control_state["active_director"] = self.normalize_active_director(
            loaded.get("active_director", self._control_state.get("active_director"))
        )
        if "dry_run" in loaded:
            self._control_state["dry_run"] = bool(loaded.get("dry_run", False))
        elif "read_only_mode" in loaded:
            self._control_state["dry_run"] = bool(loaded.get("read_only_mode", False))

    def _save_control_state_locked(self) -> None:
        self._control_state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "armed": bool(self._control_state.get("armed", False)),
            "output_disabled": bool(self._control_state.get("output_disabled", True)),
            "dry_run": bool(self._control_state.get("dry_run", False)),
            "silence_until": self._control_state.get("silence_until"),
            "active_director": self.normalize_active_director(self._control_state.get("active_director")),
        }
        tmp_path = self._control_state_path.with_suffix(".tmp")
        try:
            tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp_path.replace(self._control_state_path)
        except OSError:
            return

    def _is_silenced_locked(self) -> bool:
        silence_until = self._control_state.get("silence_until")
        if not isinstance(silence_until, str):
            return False
        dt = _parse_iso(silence_until)
        if dt is None:
            return False
        return datetime.now(timezone.utc) < dt

    def _refresh_silence_locked(self) -> None:
        silence_until = self._control_state.get("silence_until")
        if not isinstance(silence_until, str):
            return
        dt = _parse_iso(silence_until)
        if dt is None or datetime.now(timezone.utc) >= dt:
            self._control_state["silence_until"] = None
            self._save_control_state_locked()

    def _control_snapshot_locked(self) -> Dict[str, Any]:
        self._refresh_silence_locked()
        silenced = self._is_silenced_locked()
        session_raw = self._control_state.get("session_id")
        session_id = session_raw.strip() if isinstance(session_raw, str) else ""
        return {
            "armed": bool(self._control_state.get("armed", False)),
            "output_disabled": bool(self._control_state.get("output_disabled", True)),
            "dry_run": bool(self._control_state.get("dry_run", False)),
            "silenced": silenced,
            "silence_until": self._control_state.get("silence_until") if silenced else None,
            "session_id": (session_id or None),
            "active_director": self.normalize_active_director(self._control_state.get("active_director")),
        }

    def _sync_env_from_state_locked(self) -> None:
        snap = self._control_snapshot_locked()
        kill_switch_on = _env_bool(list(self._KILL_SWITCH_ENV_NAMES), False) or self._dashboard_kill_switch
        cost_cap_on = bool(get_provider_runtime_status().get("cost_cap_blocked", False))
        dry_run = bool(snap.get("dry_run", False))
        blocked_by = self.effective_blocks(
            kill_switch_on=kill_switch_on,
            armed=bool(snap["armed"]),
            silenced=bool(snap["silenced"]),
            cost_cap_on=cost_cap_on,
            dry_run=dry_run,
        )
        # Posting disabled state is controlled by active switch (armed) and silence latch.
        # DRY_RUN is intentionally enforced at OutputGate to keep test visibility.
        output_disabled = (not bool(snap["armed"])) or bool(snap["silenced"])
        self._control_state["output_disabled"] = bool(output_disabled)
        os.environ["ROONIE_ARMED"] = "1" if snap["armed"] else "0"
        os.environ["ROONIE_ARM"] = os.environ["ROONIE_ARMED"]
        os.environ["ROONIE_OUTPUT_DISABLED"] = "1" if output_disabled else "0"
        # Keep Twitch output gate aligned with dashboard active/silence state so
        # /api/status can_post and adapter eligibility stay consistent.
        os.environ["TWITCH_OUTPUT_ENABLED"] = "0" if output_disabled else "1"
        os.environ["ROONIE_ACTIVE_DIRECTOR"] = self.normalize_active_director(snap.get("active_director"))
        if not self._kill_switch_env_pinned_true:
            os.environ["ROONIE_KILL_SWITCH"] = "1" if self._dashboard_kill_switch else "0"
        if not self._dry_run_env_pinned:
            os.environ["ROONIE_DRY_RUN"] = "1" if dry_run else "0"

    def _sync_env_from_state(self) -> None:
        with self._lock:
            self._sync_env_from_state_locked()

    def set_armed(self, armed: bool) -> Dict[str, Any]:
        setup_gate_blockers: List[str] = []
        if bool(armed):
            setup_gate_blockers = self._active_setup_gate_blockers()
        with self._lock:
            previous_armed = bool(self._control_state.get("armed", False))
            if bool(armed) and setup_gate_blockers:
                self._control_state["armed"] = False
                self._control_state["output_disabled"] = True
                self._control_state["silence_until"] = None
                self._save_control_state_locked()
                self._sync_env_from_state_locked()
                snap = self._control_snapshot_locked()
                snap["previous_armed"] = previous_armed
                snap["setup_gate_blocked"] = True
                snap["setup_blockers"] = list(setup_gate_blockers)
                return snap
            self._control_state["armed"] = bool(armed)
            self._control_state["output_disabled"] = not bool(armed)
            if bool(armed):
                # Every ARM transition creates a new active session id.
                self._control_state["session_id"] = str(uuid4())
            if not armed:
                # Disarm implies immediate non-speaking state.
                self._control_state["silence_until"] = None
            self._save_control_state_locked()
            self._sync_env_from_state_locked()
            snap = self._control_snapshot_locked()
            snap["previous_armed"] = previous_armed
            return snap

    def set_kill_switch(self, on: bool) -> Dict[str, Any]:
        on = bool(on)
        self._dashboard_kill_switch = on
        if on:
            self.set_armed(False)
        if not self._kill_switch_env_pinned_true:
            os.environ["ROONIE_KILL_SWITCH"] = "1" if on else "0"
        with self._lock:
            self._sync_env_from_state_locked()
            snap = self._control_snapshot_locked()
        snap["kill_switch_on"] = on
        return snap

    def force_safe_start_defaults(self) -> Dict[str, Any]:
        with self._lock:
            self._control_state["armed"] = False
            self._control_state["output_disabled"] = True
            if not self._dry_run_env_pinned:
                self._control_state["dry_run"] = False
            if not self._kill_switch_env_pinned:
                os.environ["ROONIE_KILL_SWITCH"] = "0"
            self._control_state["silence_until"] = None
            self._control_state["session_id"] = None
            self._control_state["active_director"] = self.normalize_active_director(
                self._control_state.get("active_director")
            )
            self._save_control_state_locked()
            self._sync_env_from_state_locked()
            return self._control_snapshot_locked()

    def set_active_director(self, active: str) -> Dict[str, str]:
        selected = self.normalize_active_director(active)
        with self._lock:
            previous = self.normalize_active_director(self._control_state.get("active_director"))
            self._control_state["active_director"] = selected
            self._save_control_state_locked()
            self._sync_env_from_state_locked()
        return {"old": previous, "new": selected}

    def silence_now(self, ttl_seconds: Optional[int] = None) -> Dict[str, Any]:
        ttl = ttl_seconds if ttl_seconds is not None else self._default_silence_ttl_seconds()
        try:
            ttl = max(1, int(ttl))
        except (TypeError, ValueError):
            ttl = self._default_silence_ttl_seconds()
        with self._lock:
            until = datetime.now(timezone.utc) + timedelta(seconds=ttl)
            self._control_state["silence_until"] = _format_iso(until)
            self._save_control_state_locked()
            self._sync_env_from_state_locked()
            return self._control_snapshot_locked()

    @staticmethod
    def _write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(_canonical_json(payload), encoding="utf-8")
        tmp_path.replace(path)

    @staticmethod
    def _senses_defaults() -> Dict[str, Any]:
        return {
            "enabled": False,
            "local_only": True,
            "whitelist": ["Art", "Jen"],
            "purpose": "avoid_interrupting_hosts",
            "never_initiate": True,
            "never_publicly_reference_detection": True,
            "no_viewer_recognition": True,
        }

    def _persona_policy_senses_seed(self) -> Dict[str, Any]:
        root = _repo_root()
        candidates = [
            root / "persona_policy.yaml",
            root / "persona_policy.yml",
            root / "config" / "persona_policy.yaml",
            root / "config" / "persona_policy.yml",
        ]
        path = next((item for item in candidates if item.exists() and item.is_file()), None)
        if path is None:
            return {}
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return {}

        out: Dict[str, Any] = {}
        bool_keys = (
            "enabled",
            "local_only",
            "never_initiate",
            "never_publicly_reference_detection",
            "no_viewer_recognition",
        )
        for key in bool_keys:
            match = re.search(rf"^\s*{re.escape(key)}\s*:\s*(true|false)\s*$", raw, flags=re.IGNORECASE | re.MULTILINE)
            if match:
                out[key] = match.group(1).strip().lower() == "true"

        purpose_match = re.search(r"^\s*purpose\s*:\s*([^\n#]+)", raw, flags=re.IGNORECASE | re.MULTILINE)
        if purpose_match:
            purpose = str(purpose_match.group(1)).strip().strip("'\"")
            if purpose:
                out["purpose"] = purpose

        whitelist_match = re.search(r"^\s*whitelist\s*:\s*\[([^\]]*)\]\s*$", raw, flags=re.IGNORECASE | re.MULTILINE)
        if whitelist_match:
            parts = [part.strip().strip("'\"") for part in whitelist_match.group(1).split(",")]
            names = [part for part in parts if part]
            if names:
                out["whitelist"] = names
        else:
            block_match = re.search(
                r"^\s*whitelist\s*:\s*\n((?:\s*-\s*[^\n]+\n?)*)",
                raw,
                flags=re.IGNORECASE | re.MULTILINE,
            )
            if block_match:
                block = block_match.group(1)
                names: List[str] = []
                for item in re.findall(r"^\s*-\s*([^\n#]+)", block, flags=re.MULTILINE):
                    text = str(item).strip().strip("'\"")
                    if text:
                        names.append(text)
                if names:
                    out["whitelist"] = names
        return out

    def _normalize_senses_config(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        base = self._senses_defaults()
        raw = payload if isinstance(payload, dict) else {}

        whitelist_raw = raw.get("whitelist", base["whitelist"])
        whitelist: List[str] = []
        if isinstance(whitelist_raw, list):
            for item in whitelist_raw[:20]:
                text = self._bounded_text(item, field_name="whitelist[]", max_len=40, required=False)
                if text:
                    whitelist.append(text)
        if not whitelist:
            whitelist = list(base["whitelist"])

        purpose = self._bounded_text(
            raw.get("purpose", base["purpose"]),
            field_name="purpose",
            max_len=120,
            required=True,
        )
        return {
            "enabled": _to_bool(raw.get("enabled"), bool(base["enabled"])),
            "local_only": _to_bool(raw.get("local_only"), bool(base["local_only"])),
            "whitelist": whitelist,
            "purpose": purpose,
            "never_initiate": _to_bool(raw.get("never_initiate"), bool(base["never_initiate"])),
            "never_publicly_reference_detection": _to_bool(
                raw.get("never_publicly_reference_detection"),
                bool(base["never_publicly_reference_detection"]),
            ),
            "no_viewer_recognition": _to_bool(
                raw.get("no_viewer_recognition"),
                bool(base["no_viewer_recognition"]),
            ),
        }

    def _read_or_create_senses_config_locked(self) -> Dict[str, Any]:
        raw = _safe_read_json(self._senses_config_path)
        if isinstance(raw, dict):
            config = self._normalize_senses_config(raw)
        else:
            seeded = self._senses_defaults()
            seeded.update(self._persona_policy_senses_seed())
            config = self._normalize_senses_config(seeded)
        self._write_json_atomic(self._senses_config_path, config)
        return config

    def _ensure_senses_config(self) -> None:
        with self._lock:
            self._read_or_create_senses_config_locked()

    def get_senses_status(self) -> Dict[str, Any]:
        with self._lock:
            config = self._read_or_create_senses_config_locked()
            enabled = bool(config.get("enabled", False))
            return {
                **deepcopy(config),
                "live_hard_disabled": not enabled,
                "reason": "" if enabled else "Senses disabled in config.",
            }

    @staticmethod
    def _merge_dicts(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
        out = deepcopy(base)
        for key, value in (patch or {}).items():
            if isinstance(out.get(key), dict) and isinstance(value, dict):
                out[key] = DashboardStorage._merge_dicts(out[key], value)
            else:
                out[key] = value
        return out

    @staticmethod
    def _bounded_text(value: Any, *, field_name: str, max_len: int, required: bool) -> str:
        if value is None:
            if required:
                raise ValueError(f"{field_name} is required.")
            return ""
        text = str(value).strip()
        if required and not text:
            raise ValueError(f"{field_name} is required.")
        if len(text) > max_len:
            raise ValueError(f"{field_name} exceeds {max_len} characters.")
        return text

    def _init_memory_db(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self._memory_db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cultural_notes (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    note TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    source TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS viewer_notes (
                    id TEXT PRIMARY KEY,
                    viewer_handle TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    note TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    source TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_audit (
                    id TEXT PRIMARY KEY,
                    ts TEXT NOT NULL,
                    username TEXT NOT NULL,
                    role TEXT NOT NULL,
                    auth_mode TEXT NOT NULL,
                    action TEXT NOT NULL,
                    table_name TEXT NOT NULL,
                    row_id TEXT NOT NULL,
                    before_hash TEXT,
                    after_hash TEXT,
                    diff_summary TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_pending (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    source_event_id TEXT,
                    session_id TEXT,
                    viewer_handle TEXT NOT NULL,
                    memory_key TEXT NOT NULL,
                    preference TEXT NOT NULL,
                    proposed_note TEXT NOT NULL,
                    proposed_tags TEXT NOT NULL,
                    intent_hash TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    reviewed_by TEXT,
                    reviewed_at TEXT,
                    review_reason TEXT,
                    source TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cultural_notes_updated ON cultural_notes(updated_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_viewer_notes_handle_updated ON viewer_notes(viewer_handle, updated_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_audit_ts ON memory_audit(ts DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_pending_status_updated ON memory_pending(status, updated_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_pending_intent_hash ON memory_pending(intent_hash)"
            )
            conn.commit()

    @staticmethod
    def _memory_auth_mode(value: Optional[str]) -> str:
        text = str(value or "").strip().lower()
        return text if text in {"session", "legacy_key"} else "legacy_key"

    @staticmethod
    def _memory_source() -> str:
        return "operator_manual"

    @staticmethod
    def _decode_tags(raw: Any) -> List[str]:
        if raw is None:
            return []
        if isinstance(raw, list):
            return [str(item).strip() for item in raw if str(item).strip()]
        text = str(raw).strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except json.JSONDecodeError:
            pass
        return [part.strip() for part in text.split(",") if part.strip()]

    @staticmethod
    def _encode_tags(tags: List[str]) -> str:
        return _canonical_json(list(tags or []))

    def _normalize_tags(self, tags: Any) -> List[str]:
        if tags is None:
            return []
        if not isinstance(tags, list):
            raise ValueError("tags must be a list of strings.")
        if len(tags) > 20:
            raise ValueError("tags exceeds maximum size.")
        out: List[str] = []
        seen: set[str] = set()
        for idx, item in enumerate(tags):
            tag = self._bounded_text(item, field_name=f"tags[{idx}]", max_len=40, required=True)
            key = tag.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(tag)
        return out

    def normalize_viewer_handle(self, value: Any, *, required: bool) -> str:
        handle = self._bounded_text(
            value,
            field_name="viewer_handle",
            max_len=80,
            required=required,
        )
        if not handle:
            return ""
        normalized = handle.lstrip("@").strip().lower()
        if not normalized:
            raise ValueError("viewer_handle is required.")
        if not re.fullmatch(r"[a-z0-9_]+", normalized):
            raise ValueError("viewer_handle must use only [a-z0-9_].")
        return normalized

    def _validate_memory_note(self, value: Any) -> str:
        note = self._bounded_text(
            value,
            field_name="note",
            max_len=500,
            required=True,
        )
        lowered = note.lower()
        disallowed_inference_patterns = (
            r"\bprobably\b",
            r"\bseems like\b",
            r"\bi think they are\b",
            r"\bmust be\b",
        )
        if any(re.search(pattern, lowered) for pattern in disallowed_inference_patterns):
            raise ValueError("Memory must be explicit and non-inferential.")
        identity_terms = (
            "race",
            "racial",
            "ethnicity",
            "ethnic",
            "religion",
            "religious",
            "sexual orientation",
            "gay",
            "lesbian",
            "bisexual",
            "transgender",
            "nonbinary",
            "gender identity",
        )
        for term in identity_terms:
            if re.search(rf"\b{re.escape(term)}\b", lowered):
                raise ValueError("Memory must be explicit and non-inferential.")
        return note

    @staticmethod
    def _row_from_sql(row: sqlite3.Row) -> Dict[str, Any]:
        return {key: row[key] for key in row.keys()}

    def _memory_row_public(self, row: Dict[str, Any]) -> Dict[str, Any]:
        out = {
            "id": str(row.get("id", "")),
            "created_at": str(row.get("created_at", "")),
            "updated_at": str(row.get("updated_at", "")),
            "created_by": str(row.get("created_by", "")),
            "updated_by": str(row.get("updated_by", "")),
            "note": str(row.get("note", "")),
            "tags": self._decode_tags(row.get("tags")),
            "source": str(row.get("source", self._memory_source())),
            "is_active": bool(int(row.get("is_active", 0) or 0)),
        }
        viewer = row.get("viewer_handle")
        if viewer is not None:
            out["viewer_handle"] = self.normalize_viewer_handle(viewer, required=False)
        return out

    @staticmethod
    def _snapshot_hash(snapshot: Optional[Dict[str, Any]]) -> Optional[str]:
        if snapshot is None:
            return None
        return _json_sha256(snapshot)

    def _record_memory_audit_locked(
        self,
        conn: sqlite3.Connection,
        *,
        username: str,
        role: str,
        auth_mode: str,
        action: str,
        table_name: str,
        row_id: str,
        before_snapshot: Optional[Dict[str, Any]],
        after_snapshot: Optional[Dict[str, Any]],
        diff_summary: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO memory_audit (
                id, ts, username, role, auth_mode, action, table_name, row_id,
                before_hash, after_hash, diff_summary
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                datetime.now(timezone.utc).isoformat(),
                str(username or "unknown").strip().lower() or "unknown",
                self.normalize_role(role),
                self._memory_auth_mode(auth_mode),
                str(action).strip().upper(),
                str(table_name).strip(),
                str(row_id).strip(),
                self._snapshot_hash(before_snapshot),
                self._snapshot_hash(after_snapshot),
                self._bounded_text(
                    diff_summary,
                    field_name="diff_summary",
                    max_len=300,
                    required=False,
                ),
            ),
        )

    def query_memory_cultural(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        q: Optional[str] = None,
        active_only: bool = True,
    ) -> Tuple[List[Dict[str, Any]], int]:
        lim = max(1, min(int(limit), 500))
        off = max(0, int(offset))
        q_text = str(q or "").strip().lower()
        where: List[str] = []
        params: List[Any] = []
        if active_only:
            where.append("is_active = 1")
        if q_text:
            where.append("LOWER(note) LIKE ?")
            params.append(f"%{q_text}%")
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        with self._lock:
            with sqlite3.connect(str(self._memory_db_path)) as conn:
                conn.row_factory = sqlite3.Row
                total = int(
                    conn.execute(
                        f"SELECT COUNT(*) AS c FROM cultural_notes {where_sql}",
                        params,
                    ).fetchone()["c"]
                )
                rows = conn.execute(
                    f"""
                    SELECT id, created_at, updated_at, created_by, updated_by, note, tags, source, is_active
                    FROM cultural_notes
                    {where_sql}
                    ORDER BY updated_at DESC, created_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    [*params, lim, off],
                ).fetchall()
        return [self._memory_row_public(self._row_from_sql(row)) for row in rows], total

    def query_memory_viewers(
        self,
        *,
        viewer_handle: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        q: Optional[str] = None,
        active_only: bool = True,
    ) -> Tuple[List[Dict[str, Any]], int]:
        lim = max(1, min(int(limit), 500))
        off = max(0, int(offset))
        q_text = str(q or "").strip().lower()
        viewer = self.normalize_viewer_handle(viewer_handle, required=False) if viewer_handle else ""
        where: List[str] = []
        params: List[Any] = []
        if active_only:
            where.append("is_active = 1")
        if viewer:
            where.append("viewer_handle = ?")
            params.append(viewer)
        if q_text:
            where.append("(LOWER(viewer_handle) LIKE ? OR LOWER(note) LIKE ?)")
            like = f"%{q_text}%"
            params.extend([like, like])
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        with self._lock:
            with sqlite3.connect(str(self._memory_db_path)) as conn:
                conn.row_factory = sqlite3.Row
                total = int(
                    conn.execute(
                        f"SELECT COUNT(*) AS c FROM viewer_notes {where_sql}",
                        params,
                    ).fetchone()["c"]
                )
                rows = conn.execute(
                    f"""
                    SELECT id, viewer_handle, created_at, updated_at, created_by, updated_by, note, tags, source, is_active
                    FROM viewer_notes
                    {where_sql}
                    ORDER BY updated_at DESC, created_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    [*params, lim, off],
                ).fetchall()
        return [self._memory_row_public(self._row_from_sql(row)) for row in rows], total

    def create_memory_cultural(
        self,
        payload: Dict[str, Any],
        *,
        username: Optional[str],
        role: Optional[str],
        auth_mode: Optional[str],
    ) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("Invalid JSON payload.")
        note = self._validate_memory_note(payload.get("note"))
        tags = self._normalize_tags(payload.get("tags"))
        actor = str(username or "unknown").strip().lower() or "unknown"
        now = datetime.now(timezone.utc).isoformat()
        item = {
            "id": str(uuid4()),
            "created_at": now,
            "updated_at": now,
            "created_by": actor,
            "updated_by": actor,
            "note": note,
            "tags": tags,
            "source": self._memory_source(),
            "is_active": True,
        }
        with self._lock:
            with sqlite3.connect(str(self._memory_db_path)) as conn:
                conn.execute(
                    """
                    INSERT INTO cultural_notes (
                        id, created_at, updated_at, created_by, updated_by, note, tags, source, is_active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["id"],
                        item["created_at"],
                        item["updated_at"],
                        item["created_by"],
                        item["updated_by"],
                        item["note"],
                        self._encode_tags(item["tags"]),
                        item["source"],
                        1,
                    ),
                )
                self._record_memory_audit_locked(
                    conn,
                    username=actor,
                    role=self.normalize_role(role),
                    auth_mode=self._memory_auth_mode(auth_mode),
                    action="CREATE",
                    table_name="cultural_notes",
                    row_id=item["id"],
                    before_snapshot=None,
                    after_snapshot=item,
                    diff_summary="created fields: note,tags,is_active",
                )
                conn.commit()
        return item

    def update_memory_cultural(
        self,
        note_id: str,
        payload: Dict[str, Any],
        *,
        username: Optional[str],
        role: Optional[str],
        auth_mode: Optional[str],
    ) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("Invalid JSON payload.")
        row_id = str(note_id or "").strip()
        if not row_id:
            raise ValueError("Missing cultural note id.")
        actor = str(username or "unknown").strip().lower() or "unknown"
        allowed = {"note", "tags", "is_active"}
        unknown = [key for key in payload.keys() if key not in allowed]
        if unknown:
            raise ValueError("Unsupported fields in payload.")
        with self._lock:
            with sqlite3.connect(str(self._memory_db_path)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """
                    SELECT id, created_at, updated_at, created_by, updated_by, note, tags, source, is_active
                    FROM cultural_notes
                    WHERE id = ?
                    """,
                    (row_id,),
                ).fetchone()
                if row is None:
                    raise KeyError("cultural note not found")
                before = self._memory_row_public(self._row_from_sql(row))
                updated = dict(before)
                changed_fields: List[str] = []
                if "note" in payload:
                    updated["note"] = self._validate_memory_note(payload.get("note"))
                    changed_fields.append("note")
                if "tags" in payload:
                    updated["tags"] = self._normalize_tags(payload.get("tags"))
                    changed_fields.append("tags")
                if "is_active" in payload:
                    updated["is_active"] = bool(payload.get("is_active"))
                    changed_fields.append("is_active")
                if not changed_fields:
                    return before
                updated["updated_by"] = actor
                updated["updated_at"] = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    """
                    UPDATE cultural_notes
                    SET updated_at = ?, updated_by = ?, note = ?, tags = ?, is_active = ?
                    WHERE id = ?
                    """,
                    (
                        updated["updated_at"],
                        updated["updated_by"],
                        updated["note"],
                        self._encode_tags(updated["tags"]),
                        1 if updated["is_active"] else 0,
                        row_id,
                    ),
                )
                self._record_memory_audit_locked(
                    conn,
                    username=actor,
                    role=self.normalize_role(role),
                    auth_mode=self._memory_auth_mode(auth_mode),
                    action="UPDATE",
                    table_name="cultural_notes",
                    row_id=row_id,
                    before_snapshot=before,
                    after_snapshot=updated,
                    diff_summary=f"changed fields: {','.join(changed_fields)}",
                )
                conn.commit()
                return updated

    def delete_memory_cultural(
        self,
        note_id: str,
        *,
        username: Optional[str],
        role: Optional[str],
        auth_mode: Optional[str],
    ) -> Dict[str, Any]:
        row_id = str(note_id or "").strip()
        if not row_id:
            raise ValueError("Missing cultural note id.")
        actor = str(username or "unknown").strip().lower() or "unknown"
        with self._lock:
            with sqlite3.connect(str(self._memory_db_path)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """
                    SELECT id, created_at, updated_at, created_by, updated_by, note, tags, source, is_active
                    FROM cultural_notes
                    WHERE id = ?
                    """,
                    (row_id,),
                ).fetchone()
                if row is None:
                    raise KeyError("cultural note not found")
                before = self._memory_row_public(self._row_from_sql(row))
                conn.execute("DELETE FROM cultural_notes WHERE id = ?", (row_id,))
                self._record_memory_audit_locked(
                    conn,
                    username=actor,
                    role=self.normalize_role(role),
                    auth_mode=self._memory_auth_mode(auth_mode),
                    action="DELETE",
                    table_name="cultural_notes",
                    row_id=row_id,
                    before_snapshot=before,
                    after_snapshot=None,
                    diff_summary="hard delete",
                )
                conn.commit()
                return before

    def create_memory_viewer(
        self,
        payload: Dict[str, Any],
        *,
        username: Optional[str],
        role: Optional[str],
        auth_mode: Optional[str],
    ) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("Invalid JSON payload.")
        viewer_handle = self.normalize_viewer_handle(payload.get("viewer_handle"), required=True)
        note = self._validate_memory_note(payload.get("note"))
        tags = self._normalize_tags(payload.get("tags"))
        actor = str(username or "unknown").strip().lower() or "unknown"
        now = datetime.now(timezone.utc).isoformat()
        item = {
            "id": str(uuid4()),
            "viewer_handle": viewer_handle,
            "created_at": now,
            "updated_at": now,
            "created_by": actor,
            "updated_by": actor,
            "note": note,
            "tags": tags,
            "source": self._memory_source(),
            "is_active": True,
        }
        with self._lock:
            with sqlite3.connect(str(self._memory_db_path)) as conn:
                conn.execute(
                    """
                    INSERT INTO viewer_notes (
                        id, viewer_handle, created_at, updated_at, created_by, updated_by, note, tags, source, is_active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["id"],
                        item["viewer_handle"],
                        item["created_at"],
                        item["updated_at"],
                        item["created_by"],
                        item["updated_by"],
                        item["note"],
                        self._encode_tags(item["tags"]),
                        item["source"],
                        1,
                    ),
                )
                self._record_memory_audit_locked(
                    conn,
                    username=actor,
                    role=self.normalize_role(role),
                    auth_mode=self._memory_auth_mode(auth_mode),
                    action="CREATE",
                    table_name="viewer_notes",
                    row_id=item["id"],
                    before_snapshot=None,
                    after_snapshot=item,
                    diff_summary="created fields: viewer_handle,note,tags,is_active",
                )
                conn.commit()
        return item

    def update_memory_viewer(
        self,
        note_id: str,
        payload: Dict[str, Any],
        *,
        username: Optional[str],
        role: Optional[str],
        auth_mode: Optional[str],
    ) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("Invalid JSON payload.")
        row_id = str(note_id or "").strip()
        if not row_id:
            raise ValueError("Missing viewer note id.")
        actor = str(username or "unknown").strip().lower() or "unknown"
        allowed = {"note", "tags", "is_active"}
        unknown = [key for key in payload.keys() if key not in allowed]
        if unknown:
            raise ValueError("Unsupported fields in payload.")
        with self._lock:
            with sqlite3.connect(str(self._memory_db_path)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """
                    SELECT id, viewer_handle, created_at, updated_at, created_by, updated_by, note, tags, source, is_active
                    FROM viewer_notes
                    WHERE id = ?
                    """,
                    (row_id,),
                ).fetchone()
                if row is None:
                    raise KeyError("viewer note not found")
                before = self._memory_row_public(self._row_from_sql(row))
                updated = dict(before)
                changed_fields: List[str] = []
                if "note" in payload:
                    updated["note"] = self._validate_memory_note(payload.get("note"))
                    changed_fields.append("note")
                if "tags" in payload:
                    updated["tags"] = self._normalize_tags(payload.get("tags"))
                    changed_fields.append("tags")
                if "is_active" in payload:
                    updated["is_active"] = bool(payload.get("is_active"))
                    changed_fields.append("is_active")
                if not changed_fields:
                    return before
                updated["updated_by"] = actor
                updated["updated_at"] = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    """
                    UPDATE viewer_notes
                    SET updated_at = ?, updated_by = ?, note = ?, tags = ?, is_active = ?
                    WHERE id = ?
                    """,
                    (
                        updated["updated_at"],
                        updated["updated_by"],
                        updated["note"],
                        self._encode_tags(updated["tags"]),
                        1 if updated["is_active"] else 0,
                        row_id,
                    ),
                )
                self._record_memory_audit_locked(
                    conn,
                    username=actor,
                    role=self.normalize_role(role),
                    auth_mode=self._memory_auth_mode(auth_mode),
                    action="UPDATE",
                    table_name="viewer_notes",
                    row_id=row_id,
                    before_snapshot=before,
                    after_snapshot=updated,
                    diff_summary=f"changed fields: {','.join(changed_fields)}",
                )
                conn.commit()
                return updated

    def delete_memory_viewer(
        self,
        note_id: str,
        *,
        username: Optional[str],
        role: Optional[str],
        auth_mode: Optional[str],
    ) -> Dict[str, Any]:
        row_id = str(note_id or "").strip()
        if not row_id:
            raise ValueError("Missing viewer note id.")
        actor = str(username or "unknown").strip().lower() or "unknown"
        with self._lock:
            with sqlite3.connect(str(self._memory_db_path)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """
                    SELECT id, viewer_handle, created_at, updated_at, created_by, updated_by, note, tags, source, is_active
                    FROM viewer_notes
                    WHERE id = ?
                    """,
                    (row_id,),
                ).fetchone()
                if row is None:
                    raise KeyError("viewer note not found")
                before = self._memory_row_public(self._row_from_sql(row))
                conn.execute("DELETE FROM viewer_notes WHERE id = ?", (row_id,))
                self._record_memory_audit_locked(
                    conn,
                    username=actor,
                    role=self.normalize_role(role),
                    auth_mode=self._memory_auth_mode(auth_mode),
                    action="DELETE",
                    table_name="viewer_notes",
                    row_id=row_id,
                    before_snapshot=before,
                    after_snapshot=None,
                    diff_summary="hard delete",
                )
                conn.commit()
                return before

    def get_memory_audit(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        lim = max(1, min(int(limit), 500))
        off = max(0, int(offset))
        with self._lock:
            with sqlite3.connect(str(self._memory_db_path)) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT id, ts, username, role, auth_mode, action, table_name, row_id, before_hash, after_hash, diff_summary
                    FROM memory_audit
                    ORDER BY ts DESC
                    LIMIT ? OFFSET ?
                    """,
                    (lim, off),
                ).fetchall()
        return [self._row_from_sql(row) for row in rows]

    def get_active_cultural_notes(self, limit: int = 5) -> List[str]:
        rows, _ = self.query_memory_cultural(limit=limit, offset=0, q=None, active_only=True)
        return [str(item.get("note", "")).strip() for item in rows if str(item.get("note", "")).strip()]

    def get_viewer_notes(self, viewer_handle: str, limit: int = 5) -> List[str]:
        rows, _ = self.query_memory_viewers(
            viewer_handle=viewer_handle,
            limit=limit,
            offset=0,
            q=None,
            active_only=True,
        )
        return [str(item.get("note", "")).strip() for item in rows if str(item.get("note", "")).strip()]

    def _memory_pending_row_public(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": str(row.get("id", "")).strip(),
            "created_at": str(row.get("created_at", "")).strip(),
            "updated_at": str(row.get("updated_at", "")).strip(),
            "source_event_id": str(row.get("source_event_id", "")).strip() or None,
            "session_id": str(row.get("session_id", "")).strip() or None,
            "viewer_handle": self.normalize_viewer_handle(row.get("viewer_handle"), required=False),
            "memory_key": str(row.get("memory_key", "")).strip(),
            "preference": str(row.get("preference", "")).strip().lower(),
            "proposed_note": str(row.get("proposed_note", "")).strip(),
            "proposed_tags": self._decode_tags(row.get("proposed_tags")),
            "status": str(row.get("status", "pending")).strip().lower() or "pending",
            "reviewed_by": str(row.get("reviewed_by", "")).strip() or None,
            "reviewed_at": str(row.get("reviewed_at", "")).strip() or None,
            "review_reason": str(row.get("review_reason", "")).strip() or None,
            "source": str(row.get("source", "roonie_candidate")).strip() or "roonie_candidate",
        }

    @staticmethod
    def _memory_preference_label(value: str) -> str:
        pref = str(value or "").strip().lower()
        if pref == "dislike":
            return "dislike"
        return "like"

    def _build_memory_candidate_note(self, *, preference: str, memory_object: str) -> str:
        pref = self._memory_preference_label(preference)
        object_text = self._bounded_text(
            memory_object,
            field_name="memory_object",
            max_len=180,
            required=True,
        )
        return self._validate_memory_note(f"Viewer said they {pref} {object_text}.")

    def _memory_intent_hash(
        self,
        *,
        viewer_handle: str,
        preference: str,
        memory_key: str,
    ) -> str:
        return _json_sha256(
            {
                "viewer_handle": self.normalize_viewer_handle(viewer_handle, required=True),
                "preference": self._memory_preference_label(preference),
                "memory_key": _normalize_text(memory_key),
            }
        )

    def ingest_memory_candidates_from_run(self, run_data: Dict[str, Any]) -> Dict[str, int]:
        if not isinstance(run_data, dict):
            return {"seen": 0, "inserted": 0, "skipped_invalid": 0, "skipped_duplicate": 0, "skipped_learned": 0}

        decisions = run_data.get("decisions", [])
        if not isinstance(decisions, list):
            decisions = []

        session_id = str(run_data.get("session_id", "")).strip()
        seen = 0
        inserted = 0
        skipped_invalid = 0
        skipped_duplicate = 0
        skipped_learned = 0

        with self._lock:
            with sqlite3.connect(str(self._memory_db_path)) as conn:
                conn.row_factory = sqlite3.Row
                for decision in decisions:
                    if not isinstance(decision, dict):
                        continue
                    if str(decision.get("action", "")).strip().upper() != "MEMORY_WRITE_INTENT":
                        continue
                    seen += 1
                    trace = decision.get("trace", {})
                    if not isinstance(trace, dict):
                        trace = {}
                    mi = trace.get("memory_intent", {})
                    if not isinstance(mi, dict):
                        mi = {}
                    try:
                        viewer_handle = self.normalize_viewer_handle(mi.get("user"), required=True)
                        preference = self._memory_preference_label(str(mi.get("preference", "")).strip())
                        memory_object = self._bounded_text(
                            mi.get("object"),
                            field_name="memory_intent.object",
                            max_len=180,
                            required=True,
                        )
                        memory_key = _normalize_text(memory_object)
                        if not memory_key:
                            raise ValueError("empty memory key")
                        proposed_note = self._build_memory_candidate_note(
                            preference=preference,
                            memory_object=memory_object,
                        )
                        proposed_tags = ["candidate", "preference", preference]
                        intent_hash = self._memory_intent_hash(
                            viewer_handle=viewer_handle,
                            preference=preference,
                            memory_key=memory_key,
                        )
                    except Exception:
                        skipped_invalid += 1
                        continue

                    existing_row = conn.execute(
                        """
                        SELECT id, status
                        FROM memory_pending
                        WHERE intent_hash = ?
                        ORDER BY updated_at DESC
                        LIMIT 1
                        """,
                        (intent_hash,),
                    ).fetchone()
                    if existing_row is not None:
                        status = str(existing_row["status"] or "").strip().lower()
                        if status in {"approved", "denied"}:
                            skipped_learned += 1
                            continue
                        if status == "pending":
                            skipped_duplicate += 1
                            continue

                    now = datetime.now(timezone.utc).isoformat()
                    row_id = str(uuid4())
                    event_id = str(decision.get("event_id", "")).strip() or None
                    row_public = {
                        "id": row_id,
                        "created_at": now,
                        "updated_at": now,
                        "source_event_id": event_id,
                        "session_id": session_id or None,
                        "viewer_handle": viewer_handle,
                        "memory_key": memory_key,
                        "preference": preference,
                        "proposed_note": proposed_note,
                        "proposed_tags": proposed_tags,
                        "status": "pending",
                        "reviewed_by": None,
                        "reviewed_at": None,
                        "review_reason": None,
                        "source": "roonie_candidate",
                    }
                    conn.execute(
                        """
                        INSERT INTO memory_pending (
                            id, created_at, updated_at, source_event_id, session_id, viewer_handle,
                            memory_key, preference, proposed_note, proposed_tags, intent_hash,
                            status, reviewed_by, reviewed_at, review_reason, source
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            row_id,
                            now,
                            now,
                            event_id,
                            session_id or None,
                            viewer_handle,
                            memory_key,
                            preference,
                            proposed_note,
                            self._encode_tags(proposed_tags),
                            intent_hash,
                            "pending",
                            None,
                            None,
                            None,
                            "roonie_candidate",
                        ),
                    )
                    self._record_memory_audit_locked(
                        conn,
                        username="system",
                        role="operator",
                        auth_mode="legacy_key",
                        action="CREATE",
                        table_name="memory_pending",
                        row_id=row_id,
                        before_snapshot=None,
                        after_snapshot=row_public,
                        diff_summary="candidate created from MEMORY_WRITE_INTENT",
                    )
                    inserted += 1
                conn.commit()

        return {
            "seen": seen,
            "inserted": inserted,
            "skipped_invalid": skipped_invalid,
            "skipped_duplicate": skipped_duplicate,
            "skipped_learned": skipped_learned,
        }

    def query_memory_pending(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        q: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        lim = max(1, min(int(limit), 500))
        off = max(0, int(offset))
        q_text = str(q or "").strip().lower()
        params: List[Any] = ["pending"]
        where = ["status = ?"]
        if q_text:
            where.append("(LOWER(viewer_handle) LIKE ? OR LOWER(proposed_note) LIKE ?)")
            like = f"%{q_text}%"
            params.extend([like, like])
        where_sql = f"WHERE {' AND '.join(where)}"
        with self._lock:
            with sqlite3.connect(str(self._memory_db_path)) as conn:
                conn.row_factory = sqlite3.Row
                total = int(
                    conn.execute(
                        f"SELECT COUNT(*) AS c FROM memory_pending {where_sql}",
                        params,
                    ).fetchone()["c"]
                )
                rows = conn.execute(
                    f"""
                    SELECT id, created_at, updated_at, source_event_id, session_id, viewer_handle, memory_key, preference,
                           proposed_note, proposed_tags, status, reviewed_by, reviewed_at, review_reason, source
                    FROM memory_pending
                    {where_sql}
                    ORDER BY updated_at DESC, created_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    [*params, lim, off],
                ).fetchall()
        return [self._memory_pending_row_public(self._row_from_sql(row)) for row in rows], total

    def approve_memory_pending(
        self,
        candidate_id: str,
        *,
        username: Optional[str],
        role: Optional[str],
        auth_mode: Optional[str],
    ) -> Dict[str, Any]:
        row_id = str(candidate_id or "").strip()
        if not row_id:
            raise ValueError("Missing pending memory id.")
        actor = str(username or "unknown").strip().lower() or "unknown"
        with self._lock:
            with sqlite3.connect(str(self._memory_db_path)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """
                    SELECT id, created_at, updated_at, source_event_id, session_id, viewer_handle, memory_key, preference,
                           proposed_note, proposed_tags, intent_hash, status, reviewed_by, reviewed_at, review_reason, source
                    FROM memory_pending
                    WHERE id = ?
                    """,
                    (row_id,),
                ).fetchone()
                if row is None:
                    raise KeyError("pending memory candidate not found")

                before = self._memory_pending_row_public(self._row_from_sql(row))
                status = str(before.get("status", "")).strip().lower()
                if status != "pending":
                    raise ValueError("Memory candidate is already reviewed.")

                now = datetime.now(timezone.utc).isoformat()
                viewer_note = {
                    "id": str(uuid4()),
                    "viewer_handle": before["viewer_handle"],
                    "created_at": now,
                    "updated_at": now,
                    "created_by": actor,
                    "updated_by": actor,
                    "note": self._validate_memory_note(before["proposed_note"]),
                    "tags": self._normalize_tags(before["proposed_tags"]),
                    "source": "operator_manual",
                    "is_active": True,
                }
                conn.execute(
                    """
                    INSERT INTO viewer_notes (
                        id, viewer_handle, created_at, updated_at, created_by, updated_by, note, tags, source, is_active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        viewer_note["id"],
                        viewer_note["viewer_handle"],
                        viewer_note["created_at"],
                        viewer_note["updated_at"],
                        viewer_note["created_by"],
                        viewer_note["updated_by"],
                        viewer_note["note"],
                        self._encode_tags(viewer_note["tags"]),
                        viewer_note["source"],
                        1,
                    ),
                )
                self._record_memory_audit_locked(
                    conn,
                    username=actor,
                    role=self.normalize_role(role),
                    auth_mode=self._memory_auth_mode(auth_mode),
                    action="CREATE",
                    table_name="viewer_notes",
                    row_id=viewer_note["id"],
                    before_snapshot=None,
                    after_snapshot=viewer_note,
                    diff_summary=f"approved candidate {row_id}",
                )

                conn.execute(
                    """
                    UPDATE memory_pending
                    SET status = ?, reviewed_by = ?, reviewed_at = ?, review_reason = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    ("approved", actor, now, "approved", now, row_id),
                )
                reviewed = dict(before)
                reviewed["status"] = "approved"
                reviewed["reviewed_by"] = actor
                reviewed["reviewed_at"] = now
                reviewed["review_reason"] = "approved"
                reviewed["updated_at"] = now
                self._record_memory_audit_locked(
                    conn,
                    username=actor,
                    role=self.normalize_role(role),
                    auth_mode=self._memory_auth_mode(auth_mode),
                    action="UPDATE",
                    table_name="memory_pending",
                    row_id=row_id,
                    before_snapshot=before,
                    after_snapshot=reviewed,
                    diff_summary=f"approved -> viewer_note:{viewer_note['id']}",
                )
                conn.commit()

        return {"candidate": reviewed, "created_note": viewer_note}

    def deny_memory_pending(
        self,
        candidate_id: str,
        *,
        username: Optional[str],
        role: Optional[str],
        auth_mode: Optional[str],
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        row_id = str(candidate_id or "").strip()
        if not row_id:
            raise ValueError("Missing pending memory id.")
        actor = str(username or "unknown").strip().lower() or "unknown"
        review_reason = self._bounded_text(
            reason,
            field_name="reason",
            max_len=200,
            required=False,
        ) or "denied"
        with self._lock:
            with sqlite3.connect(str(self._memory_db_path)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """
                    SELECT id, created_at, updated_at, source_event_id, session_id, viewer_handle, memory_key, preference,
                           proposed_note, proposed_tags, status, reviewed_by, reviewed_at, review_reason, source
                    FROM memory_pending
                    WHERE id = ?
                    """,
                    (row_id,),
                ).fetchone()
                if row is None:
                    raise KeyError("pending memory candidate not found")
                before = self._memory_pending_row_public(self._row_from_sql(row))
                status = str(before.get("status", "")).strip().lower()
                if status != "pending":
                    raise ValueError("Memory candidate is already reviewed.")

                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    """
                    UPDATE memory_pending
                    SET status = ?, reviewed_by = ?, reviewed_at = ?, review_reason = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    ("denied", actor, now, review_reason, now, row_id),
                )
                reviewed = dict(before)
                reviewed["status"] = "denied"
                reviewed["reviewed_by"] = actor
                reviewed["reviewed_at"] = now
                reviewed["review_reason"] = review_reason
                reviewed["updated_at"] = now
                self._record_memory_audit_locked(
                    conn,
                    username=actor,
                    role=self.normalize_role(role),
                    auth_mode=self._memory_auth_mode(auth_mode),
                    action="UPDATE",
                    table_name="memory_pending",
                    row_id=row_id,
                    before_snapshot=before,
                    after_snapshot=reviewed,
                    diff_summary="candidate denied",
                )
                conn.commit()
        return reviewed

    def _coerce_studio_profile_fields(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("studio profile payload must be an object.")

        location_raw = payload.get("location", {})
        if not isinstance(location_raw, dict):
            raise ValueError("location must be an object.")
        location_display = self._bounded_text(
            location_raw.get("display"),
            field_name="location.display",
            max_len=120,
            required=True,
        )
        location = {"display": location_display}

        social_links_raw = payload.get("social_links", [])
        if not isinstance(social_links_raw, list):
            raise ValueError("social_links must be a list.")
        if len(social_links_raw) > 20:
            raise ValueError("social_links exceeds maximum size.")
        social_links: List[Dict[str, str]] = []
        for idx, item in enumerate(social_links_raw):
            if not isinstance(item, dict):
                raise ValueError(f"social_links[{idx}] must be an object.")
            label = self._bounded_text(
                item.get("label"),
                field_name=f"social_links[{idx}].label",
                max_len=50,
                required=True,
            )
            url = self._bounded_text(
                item.get("url"),
                field_name=f"social_links[{idx}].url",
                max_len=300,
                required=True,
            )
            if not _looks_like_url(url):
                raise ValueError(f"social_links[{idx}].url must be an http(s) URL.")
            social_links.append({"label": label, "url": url})

        gear_raw = payload.get("gear", [])
        gear: List[str] = []
        if isinstance(gear_raw, list):
            if len(gear_raw) > 120:
                raise ValueError("gear exceeds maximum size.")
            for idx, item in enumerate(gear_raw):
                entry = self._bounded_text(
                    item,
                    field_name=f"gear[{idx}]",
                    max_len=200,
                    required=True,
                )
                gear.append(entry)
        elif isinstance(gear_raw, dict):
            # Backward compatibility: flatten legacy sectioned gear structure.
            for section in ("dj", "audio", "video", "software"):
                section_items = gear_raw.get(section, [])
                if not isinstance(section_items, list):
                    raise ValueError(f"gear.{section} must be a list.")
                if len(section_items) > 30:
                    raise ValueError(f"gear.{section} exceeds maximum size.")
                for idx, row in enumerate(section_items):
                    if not isinstance(row, dict):
                        raise ValueError(f"gear.{section}[{idx}] must be an object.")
                    name = self._bounded_text(
                        row.get("name"),
                        field_name=f"gear.{section}[{idx}].name",
                        max_len=80,
                        required=True,
                    )
                    value = self._bounded_text(
                        row.get("value"),
                        field_name=f"gear.{section}[{idx}].value",
                        max_len=200,
                        required=True,
                    )
                    gear.append(f"{name}: {value}")
        else:
            raise ValueError("gear must be a list.")

        faq_raw = payload.get("faq", [])
        if not isinstance(faq_raw, list):
            raise ValueError("faq must be a list.")
        if len(faq_raw) > 40:
            raise ValueError("faq exceeds maximum size.")
        faq: List[Dict[str, str]] = []
        for idx, item in enumerate(faq_raw):
            if not isinstance(item, dict):
                raise ValueError(f"faq[{idx}] must be an object.")
            q = self._bounded_text(
                item.get("q"),
                field_name=f"faq[{idx}].q",
                max_len=220,
                required=True,
            )
            a = self._bounded_text(
                item.get("a"),
                field_name=f"faq[{idx}].a",
                max_len=400,
                required=True,
            )
            faq.append({"q": q, "a": a})

        emotes_raw = payload.get("approved_emotes", [])
        if not isinstance(emotes_raw, list):
            raise ValueError("approved_emotes must be a list.")
        if len(emotes_raw) > 200:
            raise ValueError("approved_emotes exceeds maximum size.")
        approved_emotes: List[Dict[str, Any]] = []
        for idx, value in enumerate(emotes_raw):
            if isinstance(value, str):
                name = value.strip()
                if not name:
                    continue
                approved_emotes.append({"name": name, "desc": "", "denied": False})
            elif isinstance(value, dict):
                name = self._bounded_text(
                    value.get("name"),
                    field_name=f"approved_emotes[{idx}].name",
                    max_len=40,
                    required=True,
                )
                desc = str(value.get("desc") or "").strip()[:120]
                denied = bool(value.get("denied", False))
                approved_emotes.append({"name": name, "desc": desc, "denied": denied})
            else:
                text = str(value or "").strip()
                if text:
                    approved_emotes.append({"name": text[:40], "desc": "", "denied": False})

        return {
            "version": 1,
            "location": location,
            "social_links": social_links,
            "gear": gear,
            "faq": faq,
            "approved_emotes": approved_emotes,
        }

    def _normalize_studio_profile_for_read(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        fields = self._coerce_studio_profile_fields(payload)
        updated_at_raw = payload.get("updated_at")
        updated_at = _format_iso(_parse_iso(str(updated_at_raw))) if updated_at_raw else None
        return {
            **fields,
            "updated_at": updated_at or datetime.now(timezone.utc).isoformat(),
            "updated_by": self.normalize_actor(payload.get("updated_by")),
        }

    def _normalize_studio_profile_for_write(self, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        fields = self._coerce_studio_profile_fields(payload)
        return {
            **fields,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": self.normalize_actor(actor),
        }

    def _read_or_create_studio_profile_locked(self) -> Dict[str, Any]:
        raw = _safe_read_json(self._studio_profile_path)
        if isinstance(raw, dict):
            try:
                return self._normalize_studio_profile_for_read(raw)
            except ValueError:
                pass
        default_profile = _default_studio_profile(updated_by="unknown")
        self._write_json_atomic(self._studio_profile_path, default_profile)
        return default_profile

    def get_studio_profile(self) -> Dict[str, Any]:
        with self._lock:
            profile = self._read_or_create_studio_profile_locked()
            # Keep disk copy normalized if the source had stale/invalid metadata.
            self._write_json_atomic(self._studio_profile_path, profile)
            return deepcopy(profile)

    def update_studio_profile(
        self,
        payload: Dict[str, Any],
        *,
        actor: Optional[str] = None,
        patch: bool = False,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if not isinstance(payload, dict):
            raise ValueError("Invalid JSON payload.")
        actor_norm = self.normalize_actor(actor)
        with self._lock:
            old_profile = self._read_or_create_studio_profile_locked()
            merged = (
                self._merge_dicts(old_profile, payload)
                if patch
                else self._merge_dicts(_default_studio_profile(updated_by=actor_norm), payload)
            )
            new_profile = self._normalize_studio_profile_for_write(merged, actor_norm)
            self._write_json_atomic(self._studio_profile_path, new_profile)

        changed_keys = [
            key
            for key in ("location", "social_links", "gear", "faq", "approved_emotes")
            if old_profile.get(key) != new_profile.get(key)
        ]
        old_hash = _json_sha256(old_profile)
        new_hash = _json_sha256(new_profile)
        old_to_new = {
            key: {
                "old": _trim_for_audit(old_profile.get(key)),
                "new": _trim_for_audit(new_profile.get(key)),
            }
            for key in changed_keys[:6]
        }
        audit_payload = {
            "changed_keys": changed_keys,
            "old_snapshot_hash": old_hash,
            "new_snapshot_hash": new_hash,
            "old_to_new": old_to_new,
            "mode": "patch" if patch else "put",
        }
        return new_profile, audit_payload

    # ── inner_circle ──────────────────────────────────────────────

    @staticmethod
    def _default_inner_circle() -> Dict[str, Any]:
        return {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": "system",
            "members": [
                {
                    "username": "cland3stine",
                    "display_name": "Art",
                    "role": "host",
                    "note": "DJ host of RuleOfRune. One of Roonie's humans.",
                },
                {
                    "username": "c0rcyra",
                    "display_name": "Jen",
                    "role": "host",
                    "note": "DJ hostess of RuleOfRune. One of Roonie's humans.",
                },
                {
                    "username": "ruleofrune",
                    "display_name": "Art or Jen",
                    "role": "host",
                    "note": "Stream account — whoever is DJing at the moment.",
                },
            ],
        }

    def _coerce_inner_circle_fields(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("inner_circle payload must be an object.")
        members_raw = payload.get("members", [])
        if not isinstance(members_raw, list):
            raise ValueError("members must be a list.")
        if len(members_raw) > 50:
            raise ValueError("members exceeds maximum size (50).")
        members: List[Dict[str, str]] = []
        seen_usernames: set[str] = set()
        for idx, item in enumerate(members_raw):
            if not isinstance(item, dict):
                raise ValueError(f"members[{idx}] must be an object.")
            username = self._bounded_text(
                item.get("username"),
                field_name=f"members[{idx}].username",
                max_len=40,
                required=True,
            ).lower()
            display_name = self._bounded_text(
                item.get("display_name"),
                field_name=f"members[{idx}].display_name",
                max_len=40,
                required=False,
            )
            role = self._bounded_text(
                item.get("role"),
                field_name=f"members[{idx}].role",
                max_len=30,
                required=False,
            )
            note = self._bounded_text(
                item.get("note"),
                field_name=f"members[{idx}].note",
                max_len=200,
                required=False,
            )
            key = username.lower()
            if key in seen_usernames:
                raise ValueError(f"Duplicate username: {username}")
            seen_usernames.add(key)
            members.append({
                "username": username,
                "display_name": display_name,
                "role": role,
                "note": note,
            })
        return {"version": 1, "members": members}

    def _normalize_inner_circle_for_read(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        fields = self._coerce_inner_circle_fields(payload)
        updated_at_raw = payload.get("updated_at")
        updated_at = _format_iso(_parse_iso(str(updated_at_raw))) if updated_at_raw else None
        return {
            **fields,
            "updated_at": updated_at or datetime.now(timezone.utc).isoformat(),
            "updated_by": self.normalize_actor(payload.get("updated_by")),
        }

    def _normalize_inner_circle_for_write(self, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        fields = self._coerce_inner_circle_fields(payload)
        return {
            **fields,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": self.normalize_actor(actor),
        }

    def _read_or_create_inner_circle_locked(self) -> Dict[str, Any]:
        raw = _safe_read_json(self._inner_circle_path)
        if isinstance(raw, dict):
            try:
                return self._normalize_inner_circle_for_read(raw)
            except ValueError:
                pass
        default = self._default_inner_circle()
        self._write_json_atomic(self._inner_circle_path, default)
        return default

    def get_inner_circle(self) -> Dict[str, Any]:
        with self._lock:
            circle = self._read_or_create_inner_circle_locked()
            self._write_json_atomic(self._inner_circle_path, circle)
            return deepcopy(circle)

    def update_inner_circle(
        self,
        payload: Dict[str, Any],
        *,
        actor: Optional[str] = None,
        patch: bool = False,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if not isinstance(payload, dict):
            raise ValueError("Invalid JSON payload.")
        actor_norm = self.normalize_actor(actor)
        with self._lock:
            old = self._read_or_create_inner_circle_locked()
            merged = (
                self._merge_dicts(old, payload)
                if patch
                else self._merge_dicts(self._default_inner_circle(), payload)
            )
            new = self._normalize_inner_circle_for_write(merged, actor_norm)
            self._write_json_atomic(self._inner_circle_path, new)

        changed_keys = [
            key for key in ("members",)
            if old.get(key) != new.get(key)
        ]
        old_hash = _json_sha256(old)
        new_hash = _json_sha256(new)
        audit_payload = {
            "changed_keys": changed_keys,
            "old_snapshot_hash": old_hash,
            "new_snapshot_hash": new_hash,
            "mode": "patch" if patch else "put",
        }
        return new, audit_payload

    # ── stream_schedule ─────────────────────────────────────────

    _VALID_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

    @staticmethod
    def _default_stream_schedule() -> Dict[str, Any]:
        return {
            "version": 1,
            "timezone": "ET",
            "slots": [
                {"day": "thursday", "time": "7:00 PM", "note": "Art solo", "enabled": True},
                {"day": "saturday", "time": "7:00 PM", "note": "", "enabled": True},
            ],
            "next_stream_override": "",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": "system",
        }

    def _coerce_stream_schedule_fields(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("stream_schedule payload must be an object.")
        tz = self._bounded_text(
            payload.get("timezone"), field_name="timezone", max_len=40, required=False,
        )
        if not tz:
            tz = "ET"
        override = self._bounded_text(
            payload.get("next_stream_override"), field_name="next_stream_override", max_len=200, required=False,
        )
        slots_raw = payload.get("slots", [])
        if not isinstance(slots_raw, list):
            raise ValueError("slots must be a list.")
        if len(slots_raw) > 7:
            raise ValueError("slots exceeds maximum size (7).")
        slots: List[Dict[str, Any]] = []
        seen_days: set[str] = set()
        for idx, item in enumerate(slots_raw):
            if not isinstance(item, dict):
                raise ValueError(f"slots[{idx}] must be an object.")
            day = self._bounded_text(
                item.get("day"), field_name=f"slots[{idx}].day", max_len=20, required=True,
            ).lower()
            if day not in self._VALID_DAYS:
                raise ValueError(f"slots[{idx}].day must be a valid weekday, got '{day}'.")
            if day in seen_days:
                raise ValueError(f"Duplicate day: {day}")
            seen_days.add(day)
            time_val = self._bounded_text(
                item.get("time"), field_name=f"slots[{idx}].time", max_len=30, required=True,
            )
            note = self._bounded_text(
                item.get("note"), field_name=f"slots[{idx}].note", max_len=100, required=False,
            )
            enabled = item.get("enabled", True)
            if not isinstance(enabled, bool):
                enabled = bool(enabled)
            slots.append({"day": day, "time": time_val, "note": note, "enabled": enabled})
        return {"version": 1, "timezone": tz, "slots": slots, "next_stream_override": override}

    def _normalize_stream_schedule_for_read(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        fields = self._coerce_stream_schedule_fields(payload)
        updated_at_raw = payload.get("updated_at")
        updated_at = _format_iso(_parse_iso(str(updated_at_raw))) if updated_at_raw else None
        return {
            **fields,
            "updated_at": updated_at or datetime.now(timezone.utc).isoformat(),
            "updated_by": self.normalize_actor(payload.get("updated_by")),
        }

    def _normalize_stream_schedule_for_write(self, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        fields = self._coerce_stream_schedule_fields(payload)
        return {
            **fields,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": self.normalize_actor(actor),
        }

    def _read_or_create_stream_schedule_locked(self) -> Dict[str, Any]:
        raw = _safe_read_json(self._stream_schedule_path)
        if isinstance(raw, dict):
            try:
                return self._normalize_stream_schedule_for_read(raw)
            except ValueError:
                pass
        default = self._default_stream_schedule()
        self._write_json_atomic(self._stream_schedule_path, default)
        return default

    def get_stream_schedule(self) -> Dict[str, Any]:
        with self._lock:
            schedule = self._read_or_create_stream_schedule_locked()
            self._write_json_atomic(self._stream_schedule_path, schedule)
            return deepcopy(schedule)

    def update_stream_schedule(
        self,
        payload: Dict[str, Any],
        *,
        actor: Optional[str] = None,
        patch: bool = False,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if not isinstance(payload, dict):
            raise ValueError("Invalid JSON payload.")
        actor_norm = self.normalize_actor(actor)
        with self._lock:
            old = self._read_or_create_stream_schedule_locked()
            merged = (
                self._merge_dicts(old, payload)
                if patch
                else self._merge_dicts(self._default_stream_schedule(), payload)
            )
            new = self._normalize_stream_schedule_for_write(merged, actor_norm)
            self._write_json_atomic(self._stream_schedule_path, new)

        changed_keys = [
            key for key in ("slots", "timezone", "next_stream_override")
            if old.get(key) != new.get(key)
        ]
        old_hash = _json_sha256(old)
        new_hash = _json_sha256(new)
        audit_payload = {
            "changed_keys": changed_keys,
            "old_snapshot_hash": old_hash,
            "new_snapshot_hash": new_hash,
            "mode": "patch" if patch else "put",
        }
        return new, audit_payload

    # ── audio_config ───────────────────────────────────────────

    @staticmethod
    def _default_audio_config() -> Dict[str, Any]:
        return {
            "enabled": False,
            "device_name": "",
            "sample_rate": 16_000,
            "whisper_model": "base.en",
            "whisper_device": "cuda",
            "wake_word_enabled": True,
            "transcription_interval_seconds": 3.0,
            "voice_default_user": "Art",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": "system",
        }

    def _normalize_audio_config(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        base = self._default_audio_config()
        raw = payload if isinstance(payload, dict) else {}
        device = self._bounded_text(
            raw.get("device_name", base["device_name"]),
            field_name="device_name", max_len=120, required=False,
        )
        whisper_model = self._bounded_text(
            raw.get("whisper_model", base["whisper_model"]),
            field_name="whisper_model", max_len=40, required=False,
        ) or "base.en"
        whisper_device = self._bounded_text(
            raw.get("whisper_device", base["whisper_device"]),
            field_name="whisper_device", max_len=20, required=False,
        ) or "cuda"
        voice_user = self._bounded_text(
            raw.get("voice_default_user", base["voice_default_user"]),
            field_name="voice_default_user", max_len=40, required=False,
        ) or "Art"
        sample_rate = raw.get("sample_rate", base["sample_rate"])
        if not isinstance(sample_rate, int) or sample_rate not in (8_000, 16_000, 22_050, 44_100, 48_000):
            sample_rate = 16_000
        interval = raw.get("transcription_interval_seconds", base["transcription_interval_seconds"])
        if not isinstance(interval, (int, float)) or not (1.0 <= interval <= 30.0):
            interval = 3.0
        return {
            "enabled": _to_bool(raw.get("enabled"), bool(base["enabled"])),
            "device_name": device,
            "sample_rate": sample_rate,
            "whisper_model": whisper_model,
            "whisper_device": whisper_device,
            "wake_word_enabled": _to_bool(raw.get("wake_word_enabled"), bool(base["wake_word_enabled"])),
            "transcription_interval_seconds": float(interval),
            "voice_default_user": voice_user,
        }

    def _read_or_create_audio_config_locked(self) -> Dict[str, Any]:
        raw = _safe_read_json(self._audio_config_path)
        if isinstance(raw, dict):
            config = self._normalize_audio_config(raw)
        else:
            config = self._normalize_audio_config(self._default_audio_config())
        config.setdefault("updated_at", datetime.now(timezone.utc).isoformat())
        config.setdefault("updated_by", "system")
        self._write_json_atomic(self._audio_config_path, config)
        return config

    def get_audio_config(self) -> Dict[str, Any]:
        with self._lock:
            config = self._read_or_create_audio_config_locked()
            return deepcopy(config)

    def update_audio_config(
        self,
        payload: Dict[str, Any],
        *,
        actor: Optional[str] = None,
        patch: bool = False,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if not isinstance(payload, dict):
            raise ValueError("Invalid JSON payload.")
        actor_norm = self.normalize_actor(actor)
        with self._lock:
            old = self._read_or_create_audio_config_locked()
            merged = (
                self._merge_dicts(old, payload)
                if patch
                else self._merge_dicts(self._default_audio_config(), payload)
            )
            new = self._normalize_audio_config(merged)
            new["updated_at"] = datetime.now(timezone.utc).isoformat()
            new["updated_by"] = actor_norm
            self._write_json_atomic(self._audio_config_path, new)
        changed_keys = [
            key for key in ("enabled", "device_name", "whisper_model", "whisper_device",
                            "wake_word_enabled", "sample_rate", "transcription_interval_seconds",
                            "voice_default_user")
            if old.get(key) != new.get(key)
        ]
        old_hash = _json_sha256(old)
        new_hash = _json_sha256(new)
        audit_payload = {
            "changed_keys": changed_keys,
            "old_snapshot_hash": old_hash,
            "new_snapshot_hash": new_hash,
            "mode": "patch" if patch else "put",
        }
        return new, audit_payload

    @staticmethod
    def _library_build_version() -> str:
        return "d3-library-v1"

    @staticmethod
    def _max_library_xml_upload_bytes() -> int:
        raw = os.getenv("ROONIE_MAX_XML_UPLOAD_BYTES", os.getenv("ROONIE_MAX_MULTIPART_BODY_BYTES", "8388608"))
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            parsed = 8388608
        return max(1024, min(parsed, 104857600))

    @staticmethod
    def _max_library_track_count() -> int:
        raw = os.getenv("ROONIE_MAX_LIBRARY_TRACKS", "200000")
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            parsed = 200000
        return max(1000, min(parsed, 1000000))

    @staticmethod
    def _hash_bytes(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def _default_library_meta(self) -> Dict[str, Any]:
        return {
            "last_indexed_at": None,
            "xml_hash": None,
            "track_count": 0,
            "build_version": self._library_build_version(),
            "build_ok": False,
        }

    def _read_library_index_locked(self) -> List[Dict[str, Any]]:
        raw = _safe_read_json(self._library_index_path)
        if not isinstance(raw, dict):
            return []
        tracks = raw.get("tracks", [])
        if not isinstance(tracks, list):
            return []
        out: List[Dict[str, Any]] = []
        for item in tracks:
            if not isinstance(item, dict):
                continue
            out.append(
                {
                    "artist": str(item.get("artist", "")).strip(),
                    "title": str(item.get("title", "")).strip(),
                    "mix": str(item.get("mix", "")).strip(),
                    "bpm": (str(item.get("bpm", "")).strip() or None),
                    "key": (str(item.get("key", "")).strip() or None),
                    "file_path": (str(item.get("file_path", "")).strip() or None),
                    "rekordbox_id": (str(item.get("rekordbox_id", "")).strip() or None),
                    "search_key": str(item.get("search_key", "")).strip(),
                }
            )
        return out

    def _read_library_meta_locked(self) -> Dict[str, Any]:
        base = self._default_library_meta()
        raw = _safe_read_json(self._library_meta_path)
        if not isinstance(raw, dict):
            return base
        out = dict(base)
        out["last_indexed_at"] = str(raw.get("last_indexed_at")) if raw.get("last_indexed_at") else None
        out["xml_hash"] = str(raw.get("xml_hash")) if raw.get("xml_hash") else None
        try:
            out["track_count"] = max(0, int(raw.get("track_count", 0)))
        except (TypeError, ValueError):
            out["track_count"] = 0
        out["build_version"] = str(raw.get("build_version") or base["build_version"])
        out["build_ok"] = bool(raw.get("build_ok", False))
        return out

    @staticmethod
    def _parse_rekordbox_xml(xml_bytes: bytes) -> List[Dict[str, Any]]:
        if not xml_bytes:
            return []
        max_xml_bytes = DashboardStorage._max_library_xml_upload_bytes()
        if len(xml_bytes) > max_xml_bytes:
            raise ValueError(f"XML upload too large. Max {max_xml_bytes} bytes.")
        header_scan = bytes(xml_bytes[:4096]).lower()
        if b"<!doctype" in header_scan or b"<!entity" in header_scan:
            raise ValueError("Invalid Rekordbox XML: DTD/ENTITY declarations are not allowed.")

        max_tracks = DashboardStorage._max_library_track_count()
        tracks: List[Dict[str, Any]] = []
        for _, elem in ET.iterparse(io.BytesIO(xml_bytes), events=("end",)):
            if elem.tag is None:
                continue
            tag = str(elem.tag).split("}")[-1].upper()
            if tag != "TRACK":
                elem.clear()
                continue
            attrs = {str(k).strip().lower(): (str(v).strip() if v is not None else "") for k, v in elem.attrib.items()}
            artist = attrs.get("artist", "")
            title = attrs.get("name", "") or attrs.get("title", "")
            mix = attrs.get("mix", "") or attrs.get("version", "")
            bpm_raw = attrs.get("averagebpm", "") or attrs.get("bpm", "")
            key = attrs.get("tonality", "") or attrs.get("key", "")
            file_path = attrs.get("location", "") or attrs.get("filepath", "")
            rekordbox_id = attrs.get("trackid", "") or attrs.get("track_id", "") or attrs.get("id", "")
            search_key = _build_track_search_key(artist, title)
            if search_key:
                track: Dict[str, Any] = {
                    "artist": artist,
                    "title": title,
                    "mix": mix,
                    "bpm": None,
                    "key": key or None,
                    "file_path": file_path or None,
                    "rekordbox_id": rekordbox_id or None,
                    "search_key": search_key,
                }
                if bpm_raw:
                    try:
                        bpm_val = float(bpm_raw)
                        track["bpm"] = (
                            str(int(round(bpm_val)))
                            if bpm_val.is_integer()
                            else f"{bpm_val:.2f}".rstrip("0").rstrip(".")
                        )
                    except ValueError:
                        track["bpm"] = bpm_raw
                tracks.append(track)
                if len(tracks) > max_tracks:
                    raise ValueError(f"Rekordbox XML track limit exceeded ({max_tracks}).")
            elem.clear()
        tracks.sort(key=lambda t: (t.get("search_key", ""), t.get("mix", "")))
        return tracks

    def get_library_status(self) -> Dict[str, Any]:
        with self._lock:
            meta = self._read_library_meta_locked()
            if meta["track_count"] <= 0:
                meta["track_count"] = len(self._read_library_index_locked())
            return meta

    def save_library_xml(self, xml_bytes: bytes) -> Dict[str, Any]:
        content = bytes(xml_bytes or b"")
        if not content:
            raise ValueError("XML upload is empty.")
        max_xml_bytes = self._max_library_xml_upload_bytes()
        if len(content) > max_xml_bytes:
            raise ValueError(f"XML upload too large. Max {max_xml_bytes} bytes.")
        with self._lock:
            self._library_dir.mkdir(parents=True, exist_ok=True)
            tmp = self._library_xml_path.with_suffix(".xml.tmp")
            tmp.write_bytes(content)
            tmp.replace(self._library_xml_path)
        return {"xml_hash": self._hash_bytes(content), "size_bytes": len(content)}

    def rebuild_library_index(self) -> Dict[str, Any]:
        with self._lock:
            if not self._library_xml_path.exists():
                raise ValueError("No uploaded Rekordbox XML found.")
            xml_bytes = self._library_xml_path.read_bytes()
            xml_hash = self._hash_bytes(xml_bytes)

            old_index_raw = _safe_read_json(self._library_index_path)
            old_hash = _json_sha256(old_index_raw) if isinstance(old_index_raw, dict) else None

            try:
                tracks = self._parse_rekordbox_xml(xml_bytes)
            except ET.ParseError as exc:
                raise ValueError(f"Invalid Rekordbox XML: {exc}") from exc
            index_payload = {
                "version": 1,
                "built_at": datetime.now(timezone.utc).isoformat(),
                "xml_hash": xml_hash,
                "tracks": tracks,
            }
            meta_payload = {
                "last_indexed_at": index_payload["built_at"],
                "xml_hash": xml_hash,
                "track_count": len(tracks),
                "build_version": self._library_build_version(),
                "build_ok": True,
            }
            self._write_json_atomic(self._library_index_path, index_payload)
            self._write_json_atomic(self._library_meta_path, meta_payload)

            new_hash = _json_sha256(index_payload)
            return {
                **meta_payload,
                "old_snapshot_hash": old_hash,
                "new_snapshot_hash": new_hash,
            }

    @staticmethod
    def _search_score(query_key: str, search_key: str) -> float:
        if not query_key or not search_key:
            return 0.0
        if query_key == search_key:
            return 1.0
        ratio = SequenceMatcher(None, query_key, search_key).ratio()
        if query_key in search_key or search_key in query_key:
            ratio = max(ratio, 0.9)
        return ratio

    def search_library_index(self, q: str, limit: int = 25) -> Dict[str, Any]:
        query = str(q or "").strip()
        lim = max(1, min(int(limit), 100))
        query_key = _build_track_search_key("", query)
        with self._lock:
            tracks = self._read_library_index_locked()

        if not query_key:
            return {"q": query, "confidence": "NONE", "matches": []}

        exact: List[Dict[str, Any]] = []
        close_scored: List[Tuple[float, Dict[str, Any]]] = []
        for track in tracks:
            key = str(track.get("search_key", "")).strip()
            if not key:
                continue
            if key == query_key:
                exact.append(track)
                continue
            score = self._search_score(query_key, key)
            if score >= 0.82:
                close_scored.append((score, track))

        def _to_view(track: Dict[str, Any], confidence: str) -> Dict[str, Any]:
            return {
                "artist": track.get("artist"),
                "title": track.get("title"),
                "mix": track.get("mix"),
                "bpm": track.get("bpm"),
                "key": track.get("key"),
                "file_path": track.get("file_path"),
                "rekordbox_id": track.get("rekordbox_id"),
                "confidence": confidence,
            }

        if exact:
            return {
                "q": query,
                "confidence": "EXACT",
                "matches": [_to_view(track, "EXACT") for track in exact[:lim]],
            }

        if close_scored:
            close_scored.sort(key=lambda item: item[0], reverse=True)
            return {
                "q": query,
                "confidence": "CLOSE",
                "matches": [_to_view(track, "CLOSE") for _, track in close_scored[:lim]],
            }

        return {"q": query, "confidence": "NONE", "matches": []}

    def get_queue(self, limit: int = 25) -> List[Dict[str, Any]]:
        lim = max(0, min(int(limit), 100))
        with self._lock:
            return list(self._queue)[:lim]

    def cancel_queue_item(self, item_id: str) -> bool:
        target = str(item_id or "").strip()
        if not target:
            return False
        with self._lock:
            for idx, item in enumerate(self._queue):
                if str(item.get("id", "")).strip() == target:
                    self._queue.pop(idx)
                    return True
        return False

    @staticmethod
    def _summarize_payload(payload: Dict[str, Any]) -> str:
        if not isinstance(payload, dict) or not payload:
            return "{}"
        keys = sorted(payload.keys())[:8]
        compact = {k: payload.get(k) for k in keys}
        text = json.dumps(compact, ensure_ascii=False)
        return text if len(text) <= 512 else (text[:509] + "...")

    def record_operator_action(
        self,
        *,
        operator: str,
        action: str,
        payload: Optional[Dict[str, Any]],
        result: str,
        actor: Optional[str] = None,
        username: Optional[str] = None,
        role: Optional[str] = None,
        auth_mode: Optional[str] = None,
    ) -> OperatorLogResponse:
        username_norm = str(username or "").strip().lower() or None
        role_norm = self.normalize_role(role) if role else None
        auth_mode_norm = str(auth_mode or "").strip().lower() or None
        if auth_mode_norm not in {None, "session", "legacy_key"}:
            auth_mode_norm = None
        rec = OperatorLogResponse(
            ts=datetime.now(timezone.utc).isoformat(),
            operator=(operator or "Operator"),
            action=action,
            payload_summary=self._summarize_payload(payload or {}),
            result=result,
            actor=(username_norm or self.normalize_actor(actor)),
            username=username_norm,
            role=role_norm,
            auth_mode=auth_mode_norm,
        )
        self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(rec.to_dict(), ensure_ascii=False)
        try:
            with self._audit_log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            # Keep API functional even if audit write fails; the action result still returns.
            pass
        self._apply_retention_policy()
        return rec

    @staticmethod
    def _recent_file_grace_seconds() -> float:
        raw = os.getenv("ROONIE_DASHBOARD_RECENT_FILE_GRACE_SECONDS", "1.5")
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return 1.5

    def _candidate_run_paths(self) -> List[Path]:
        self._apply_retention_policy()
        if not self.runs_dir.exists():
            return []
        candidates: List[Tuple[float, Path]] = []
        for path in self.runs_dir.glob("*.json"):
            if not path.is_file():
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            candidates.append((mtime, path))
        candidates.sort(key=lambda item: item[0], reverse=True)
        return [path for _, path in candidates]

    def _load_latest_run(self) -> Tuple[Optional[Path], Optional[Dict[str, Any]]]:
        candidates = self._candidate_run_paths()
        if not candidates:
            return None, None

        newest_seen: Optional[Path] = candidates[0]
        now_ts = datetime.now(timezone.utc).timestamp()
        grace_s = self._recent_file_grace_seconds()

        for run_path in candidates:
            try:
                age_s = now_ts - run_path.stat().st_mtime
            except OSError:
                continue
            if grace_s > 0 and age_s < grace_s:
                continue
            parsed = _safe_read_json(run_path)
            if parsed is not None:
                self._update_status_runtime_from_run_data(run_path, parsed)
                return run_path, parsed

        # Fall back: if everything was skipped (e.g., only very-recent files), try parse in order anyway.
        for run_path in candidates:
            parsed = _safe_read_json(run_path)
            if parsed is not None:
                self._update_status_runtime_from_run_data(run_path, parsed)
                return run_path, parsed

        return newest_seen, None

    def _prime_status_runtime_from_runs(self) -> None:
        try:
            self._load_latest_run()
        except Exception:
            return

    @staticmethod
    def _max_log_run_files() -> int:
        raw = os.getenv("ROONIE_DASHBOARD_LOG_MAX_RUN_FILES", "40")
        try:
            return max(1, min(int(raw), 500))
        except (TypeError, ValueError):
            return 40

    def _load_recent_runs(self, max_files: Optional[int] = None) -> List[Dict[str, Any]]:
        limit = max_files if max_files is not None else self._max_log_run_files()
        limit = max(1, int(limit))
        candidates = self._candidate_run_paths()
        if not candidates:
            return []

        now_ts = datetime.now(timezone.utc).timestamp()
        grace_s = self._recent_file_grace_seconds()
        out: List[Dict[str, Any]] = []

        for run_path in candidates:
            if len(out) >= limit:
                break
            try:
                age_s = now_ts - run_path.stat().st_mtime
            except OSError:
                continue
            if grace_s > 0 and age_s < grace_s:
                continue
            parsed = _safe_read_json(run_path)
            if isinstance(parsed, dict):
                out.append(parsed)

        if out:
            return out

        # Fall back when only very-recent runs exist.
        for run_path in candidates:
            if len(out) >= limit:
                break
            parsed = _safe_read_json(run_path)
            if isinstance(parsed, dict):
                out.append(parsed)
        return out

    @staticmethod
    def _active_provider_from_route(route: str) -> str:
        text = str(route or "").strip()
        if text.startswith("primary:"):
            return text.split(":", 1)[1].strip() or "none"
        return "none"

    @staticmethod
    def _suppression_detail(trace: Dict[str, Any]) -> Optional[str]:
        policy = trace.get("policy", {}) if isinstance(trace, dict) else {}
        routing = trace.get("routing", {}) if isinstance(trace, dict) else {}
        refusal = policy.get("refusal_reason_code")
        if isinstance(refusal, str) and refusal.strip():
            return refusal.strip()
        codes = routing.get("routing_reason_codes", [])
        if isinstance(codes, list) and codes:
            return ", ".join(str(code) for code in codes if str(code).strip())
        return None

    def _events_from_run(self, run_data: Dict[str, Any]) -> List[EventResponse]:
        inputs = run_data.get("inputs", [])
        decisions = run_data.get("decisions", [])
        outputs = run_data.get("outputs", [])
        run_session_id = str(run_data.get("session_id", "")).strip() or None

        inputs_by_id: Dict[str, Dict[str, Any]] = {}
        for item in inputs if isinstance(inputs, list) else []:
            if isinstance(item, dict):
                event_id = str(item.get("event_id", "")).strip()
                if event_id:
                    inputs_by_id[event_id] = item

        outputs_by_id: Dict[str, Dict[str, Any]] = {}
        for item in outputs if isinstance(outputs, list) else []:
            if isinstance(item, dict):
                event_id = str(item.get("event_id", "")).strip()
                if event_id:
                    outputs_by_id[event_id] = item

        started_at = str(run_data.get("started_at", "")).strip() or None
        started_dt = _parse_iso(started_at)

        out: List[EventResponse] = []
        for idx, decision in enumerate(decisions if isinstance(decisions, list) else []):
            if not isinstance(decision, dict):
                continue

            event_id = str(decision.get("event_id", "")).strip()
            source = inputs_by_id.get(event_id, {})
            metadata = source.get("metadata", {}) if isinstance(source, dict) else {}
            if not isinstance(metadata, dict):
                metadata = {}
            output = outputs_by_id.get(event_id, {})
            if not isinstance(output, dict):
                output = {}

            trace = decision.get("trace", {})
            if not isinstance(trace, dict):
                trace = {}
            proposal = trace.get("proposal", {})
            if not isinstance(proposal, dict):
                proposal = {}
            routing = trace.get("routing", {})
            if not isinstance(routing, dict):
                routing = {}
            gates = trace.get("gates", {})
            if not isinstance(gates, dict):
                gates = {}

            derived_ts: Optional[str] = None
            msg_ts = metadata.get("ts")
            if isinstance(msg_ts, str) and msg_ts.strip():
                derived_ts = msg_ts.strip()
            elif started_dt is not None:
                derived_ts = _format_iso(started_dt + timedelta(seconds=idx))
            else:
                derived_ts = started_at

            response_text = decision.get("response_text")
            if isinstance(response_text, str) and response_text.strip():
                final_text = response_text
            else:
                action = decision.get("action")
                final_text = str(action) if action is not None else None

            emitted = bool(output.get("emitted", False))
            reason = output.get("reason")
            suppression_reason: Optional[str] = None
            if not emitted and isinstance(reason, str) and reason.strip():
                suppression_reason = reason.strip()

            decision_type = "speak"
            if suppression_reason:
                decision_type = "suppress"
            else:
                final_upper = str(final_text or "").strip().upper()
                if not final_upper or final_upper == "NOOP":
                    decision_type = "noop"

            context_active = bool(
                decision.get("context_active", trace.get("context_active", False))
            )
            context_turns_used_raw = decision.get(
                "context_turns_used", trace.get("context_turns_used", 0)
            )
            try:
                context_turns_used = int(context_turns_used_raw or 0)
            except (TypeError, ValueError):
                context_turns_used = 0

            # Extract model_used from proposal trace
            model_used_raw = proposal.get("model_used") or proposal.get("model") or None
            model_used = str(model_used_raw).strip() if model_used_raw else None

            # Extract provider_used from proposal/routing trace, fallback to route.
            provider_used_raw = (
                proposal.get("provider_used")
                or routing.get("provider_selected")
                or self._active_provider_from_route(str(decision.get("route", "")))
                or None
            )
            provider_used = str(provider_used_raw).strip().lower() if provider_used_raw else None
            if provider_used not in {"openai", "grok", "anthropic"}:
                provider_used = None

            # Extract behavior_category from trace or output
            behavior_raw = trace.get("behavior", {})
            if not isinstance(behavior_raw, dict):
                behavior_raw = {}
            behavior_category = (
                str(behavior_raw.get("category", "")).strip()
                or str(output.get("category", "")).strip()
                or str(decision.get("category", "")).strip()
                or None
            )

            out.append(
                EventResponse(
                    ts=derived_ts,
                    session_id=(
                        str(output.get("session_id", "")).strip()
                        or str(proposal.get("session_id", "")).strip()
                        or run_session_id
                    ),
                    user_handle=str(metadata.get("user", source.get("actor", "viewer"))),
                    message_text=str(source.get("message", "")),
                    direct_address=bool(
                        metadata.get("is_direct_mention", gates.get("addressed_to_roonie", False))
                    ),
                    decision_type=decision_type,
                    final_text=final_text,
                    decision=final_text,
                    suppression_reason=suppression_reason,
                    suppression_detail=self._suppression_detail(trace),
                    context_active=context_active,
                    context_turns_used=context_turns_used,
                    model_used=model_used,
                    provider_used=provider_used,
                    behavior_category=behavior_category,
                )
            )

        out.reverse()
        return out

    def _current_blocks_and_provider(
        self,
        *,
        reload_control_from_disk: bool = False,
    ) -> Tuple[bool, Dict[str, Any], Dict[str, Any], List[str], bool]:
        kill_switch_on = _env_bool(list(self._KILL_SWITCH_ENV_NAMES), False) or self._dashboard_kill_switch
        provider_status = get_provider_runtime_status()
        setup_gate_blockers = self._active_setup_gate_blockers()
        with self._lock:
            if reload_control_from_disk:
                self._reload_control_state_from_file_locked()
            control = self._control_snapshot_locked()
            blocked_by = self.effective_blocks(
                kill_switch_on=kill_switch_on,
                armed=bool(control["armed"]),
                silenced=bool(control["silenced"]),
                cost_cap_on=bool(provider_status.get("cost_cap_blocked", False)),
                dry_run=bool(control.get("dry_run", False)),
            )
            for blocker in setup_gate_blockers:
                if blocker not in blocked_by:
                    blocked_by.append(blocker)
            can_post = self.effective_can_post(blocked_by) and not bool(control.get("output_disabled", True))
            self._sync_env_from_state_locked()
        return kill_switch_on, provider_status, control, blocked_by, can_post

    @staticmethod
    def _status_slow_log_threshold_ms() -> float:
        raw = os.getenv("ROONIE_STATUS_SLOW_LOG_MS", "250")
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return 250.0

    def _update_status_runtime_from_run_data(self, run_path: Optional[Path], run_data: Optional[Dict[str, Any]]) -> None:
        if not isinstance(run_data, dict):
            return
        mode = str(os.getenv("ROONIE_MODE", "offline") or "offline")
        version = str(run_data.get("director_commit") or os.getenv("ROONIE_VERSION", "unknown"))
        active_provider = "none"
        context_active = False
        context_turns_used = 0
        last_heartbeat_at = str(run_data.get("started_at") or "").strip() or None

        inputs = run_data.get("inputs", [])
        if isinstance(inputs, list) and inputs:
            first = inputs[0] if isinstance(inputs[0], dict) else {}
            md = first.get("metadata", {}) if isinstance(first, dict) else {}
            if isinstance(md, dict):
                mode = str(md.get("mode", mode) or mode)

        decisions = run_data.get("decisions", [])
        if isinstance(decisions, list):
            for candidate in reversed(decisions):
                if not isinstance(candidate, dict):
                    continue
                trace = candidate.get("trace", {})
                if not isinstance(trace, dict):
                    trace = {}
                proposal = trace.get("proposal", {})
                if not isinstance(proposal, dict):
                    proposal = {}
                provider_from_proposal = str(proposal.get("provider_used", "")).strip().lower()
                provider_from_route = self._active_provider_from_route(str(candidate.get("route", ""))).strip().lower()
                if provider_from_proposal and provider_from_proposal != "none":
                    active_provider = provider_from_proposal
                elif provider_from_route and provider_from_route != "none":
                    active_provider = provider_from_route
                context_active = bool(candidate.get("context_active", trace.get("context_active", False)))
                turns_raw = candidate.get("context_turns_used", trace.get("context_turns_used", 0))
                try:
                    context_turns_used = int(turns_raw or 0)
                except (TypeError, ValueError):
                    context_turns_used = 0
                break

        if not last_heartbeat_at and run_path is not None:
            try:
                last_heartbeat_at = _format_iso(datetime.fromtimestamp(run_path.stat().st_mtime, timezone.utc))
            except OSError:
                last_heartbeat_at = None

        with self._lock:
            self._status_runtime.update(
                {
                    "last_heartbeat_at": last_heartbeat_at,
                    "active_provider": active_provider or "none",
                    "version": version,
                    "mode": mode,
                    "context_last_active": context_active,
                    "context_last_turns_used": context_turns_used,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )

    def _status_runtime_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._status_runtime)

    def _twitch_connected_cached_fast(self) -> bool:
        with self._lock:
            cache = self._twitch_status_cache
            if isinstance(cache, dict):
                return bool(cache.get("connected", False))
        # Cheap env fallback only; avoids disk/network in /api/status path.
        for name in ("TWITCH_OAUTH_TOKEN", "TWITCH_OAUTH", "TWITCH_BROADCASTER_OAUTH_TOKEN", "TWITCH_BROADCASTER_TOKEN"):
            token = str(os.getenv(name, "")).strip()
            if self._token_looks_valid(token):
                return True
        return False

    def get_status(self) -> StatusResponse:
        total_start = time.perf_counter()
        step_ms: Dict[str, float] = {}

        t0 = time.perf_counter()
        kill_switch_on, provider_status, control, blocked_by, can_post = self._current_blocks_and_provider(
            reload_control_from_disk=False
        )
        step_ms["blocks_provider"] = (time.perf_counter() - t0) * 1000.0

        t1 = time.perf_counter()
        routing_status = get_routing_runtime_status()
        step_ms["routing"] = (time.perf_counter() - t1) * 1000.0

        t1b = time.perf_counter()
        model_cfg = get_resolved_model_config()
        step_ms["models"] = (time.perf_counter() - t1b) * 1000.0

        t2 = time.perf_counter()
        runtime_snapshot = self._status_runtime_snapshot()
        step_ms["runtime_snapshot"] = (time.perf_counter() - t2) * 1000.0

        t3 = time.perf_counter()
        twitch_connected = self._twitch_connected_cached_fast()
        step_ms["twitch_cached"] = (time.perf_counter() - t3) * 1000.0

        t4 = time.perf_counter()
        eventsub_state = self.get_eventsub_runtime_state()
        step_ms["eventsub"] = (time.perf_counter() - t4) * 1000.0

        send_fail = self.get_send_failure_state()

        active_provider = str(provider_status.get("active_provider", "none") or "none")
        if active_provider == "none":
            active_provider = str(runtime_snapshot.get("active_provider", "none") or "none")
        if active_provider == "none":
            active_provider = str(os.getenv("ROONIE_ACTIVE_PROVIDER", "none") or "none")
        provider_models_raw = model_cfg.get("provider_models", {})
        provider_models = dict(provider_models_raw) if isinstance(provider_models_raw, dict) else {}
        active_model = str(provider_models.get(active_provider, "")).strip() or None
        version = str(runtime_snapshot.get("version") or os.getenv("ROONIE_VERSION", "unknown"))
        mode = str(runtime_snapshot.get("mode") or os.getenv("ROONIE_MODE", "offline"))
        last_heartbeat_at = runtime_snapshot.get("last_heartbeat_at")

        shadow_enabled = _env_bool(["ROONIE_SHADOW_ENABLED", "SHADOW_ENABLED"], False)
        if shadow_enabled and "shadow" not in str(mode).lower():
            mode = f"{mode}/shadow"

        control_session_raw = control.get("session_id")
        control_session_id = control_session_raw.strip() if isinstance(control_session_raw, str) else ""
        eventsub_session_raw = eventsub_state.get("eventsub_session_id")
        eventsub_session_id = eventsub_session_raw.strip() if isinstance(eventsub_session_raw, str) else ""
        eventsub_last_ts_raw = eventsub_state.get("last_eventsub_message_ts")
        eventsub_last_ts = eventsub_last_ts_raw.strip() if isinstance(eventsub_last_ts_raw, str) else ""
        eventsub_last_error_raw = eventsub_state.get("eventsub_last_error")
        eventsub_last_error = eventsub_last_error_raw.strip() if isinstance(eventsub_last_error_raw, str) else ""

        t5 = time.perf_counter()
        response = StatusResponse(
            kill_switch_on=kill_switch_on,
            armed=bool(control["armed"]),
            session_id=(control_session_id or None),
            mode=mode,
            twitch_connected=twitch_connected,
            last_heartbeat_at=last_heartbeat_at,
            active_provider=active_provider,
            version=version,
            policy_loaded_at=os.getenv("ROONIE_POLICY_LOADED_AT"),
            policy_version=os.getenv("ROONIE_POLICY_VERSION"),
            context_last_active=bool(runtime_snapshot.get("context_last_active", False)),
            context_last_turns_used=int(runtime_snapshot.get("context_last_turns_used", 0) or 0),
            silenced=bool(control["silenced"]),
            silence_until=control["silence_until"],
            read_only_mode=self.is_read_only_mode(),
            can_post=can_post,
            blocked_by=blocked_by,
            active_director=self.normalize_active_director(control.get("active_director")),
            routing_enabled=bool(routing_status.get("enabled", True)),
            eventsub_connected=bool(eventsub_state.get("eventsub_connected", False)),
            eventsub_session_id=(eventsub_session_id or None),
            eventsub_last_message_ts=(eventsub_last_ts or None),
            eventsub_reconnect_count=int(eventsub_state.get("reconnect_count", 0) or 0),
            eventsub_last_error=(eventsub_last_error or None),
            active_model=active_model,
            provider_models=provider_models,
            resolved_models={
                "openai_model": str(model_cfg.get("openai_model", "")),
                "director_model": str(model_cfg.get("director_model", "")),
                "grok_model": str(model_cfg.get("grok_model", "")),
            },
            routing_info={
                "enabled": bool(routing_status.get("enabled", True)),
                "default_provider": str(routing_status.get("default_provider", "openai") or "openai"),
                "music_route_provider": str(routing_status.get("music_route_provider", "grok") or "grok"),
                "manual_override": str(routing_status.get("manual_override", "default") or "default"),
                "provider_models": provider_models,
                "music_route_model": str(
                    provider_models.get(str(routing_status.get("music_route_provider", "grok") or "grok"), "")
                ).strip()
                or None,
            },
            send_fail_count=int(send_fail.get("fail_count", 0) or 0),
            send_fail_reason=send_fail.get("last_fail_reason"),
            send_fail_at=send_fail.get("last_fail_at"),
        )
        step_ms["compose"] = (time.perf_counter() - t5) * 1000.0

        total_ms = (time.perf_counter() - total_start) * 1000.0
        if total_ms > self._status_slow_log_threshold_ms():
            print(
                "[perf][/api/status] "
                f"total_ms={total_ms:.1f} "
                f"blocks_provider={step_ms.get('blocks_provider', 0.0):.1f} "
                f"routing={step_ms.get('routing', 0.0):.1f} "
                f"models={step_ms.get('models', 0.0):.1f} "
                f"runtime_snapshot={step_ms.get('runtime_snapshot', 0.0):.1f} "
                f"twitch_cached={step_ms.get('twitch_cached', 0.0):.1f} "
                f"eventsub={step_ms.get('eventsub', 0.0):.1f} "
                f"compose={step_ms.get('compose', 0.0):.1f}"
            )

        return response

    def get_providers_status(self) -> Dict[str, Any]:
        _, provider_status, _, blocked_by, can_post = self._current_blocks_and_provider(
            reload_control_from_disk=False
        )
        model_cfg = get_resolved_model_config()
        provider_models_raw = model_cfg.get("provider_models", {})
        provider_models = dict(provider_models_raw) if isinstance(provider_models_raw, dict) else {}
        active_provider = str(provider_status.get("active_provider", "openai") or "openai")
        with self._lock:
            active_director = self.normalize_active_director(self._control_state.get("active_director"))
        routing_cfg = get_routing_runtime_status()
        runtime_metrics = get_provider_runtime_metrics()
        providers_metrics = runtime_metrics.get("providers", {}) if isinstance(runtime_metrics, dict) else {}
        return {
            "active_provider": active_provider,
            "active_model": str(provider_models.get(active_provider, "")).strip() or None,
            "approved_providers": list(provider_status.get("approved_providers", [])),
            "caps": dict(provider_status.get("caps", {})),
            "usage": dict(provider_status.get("usage", {})),
            "metrics": providers_metrics if isinstance(providers_metrics, dict) else {},
            "can_post": can_post,
            "blocked_by": blocked_by,
            "routing_enabled": bool(routing_cfg.get("enabled", True)),
            "active_director": active_director,
            "provider_models": provider_models,
            "resolved_models": {
                "openai_model": str(model_cfg.get("openai_model", "")),
                "director_model": str(model_cfg.get("director_model", "")),
                "grok_model": str(model_cfg.get("grok_model", "")),
                "anthropic_model": str(model_cfg.get("anthropic_model", "")),
            },
            "model_sources": dict(model_cfg.get("sources", {})) if isinstance(model_cfg.get("sources"), dict) else {},
            "model_fallback_defaults": list(model_cfg.get("fallback_defaults", [])),
        }

    def _latest_routing_decision(self, run_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        latest = {
            "ts": None,
            "routing_class": "general",
            "provider_selected": "openai",
            "provider_model": None,
            "moderation_provider_used": None,
            "moderation_result": None,
            "override_mode": "default",
        }
        if not isinstance(run_data, dict):
            return latest

        inputs_by_id: Dict[str, Dict[str, Any]] = {}
        inputs = run_data.get("inputs", [])
        for item in inputs if isinstance(inputs, list) else []:
            if not isinstance(item, dict):
                continue
            event_id = str(item.get("event_id", "")).strip()
            if event_id:
                inputs_by_id[event_id] = item

        decisions = run_data.get("decisions", [])
        if not isinstance(decisions, list):
            return latest

        for decision in reversed(decisions):
            if not isinstance(decision, dict):
                continue
            trace = decision.get("trace", {})
            if not isinstance(trace, dict):
                trace = {}
            routing = trace.get("routing", {})
            if not isinstance(routing, dict):
                routing = {}
            provider_selected = str(routing.get("provider_selected", "")).strip().lower()
            if not provider_selected:
                provider_selected = self._active_provider_from_route(decision.get("route", ""))
            if not provider_selected or provider_selected == "none":
                provider_selected = latest["provider_selected"]

            event_id = str(decision.get("event_id", "")).strip()
            source = inputs_by_id.get(event_id, {})
            metadata = source.get("metadata", {}) if isinstance(source, dict) else {}
            if not isinstance(metadata, dict):
                metadata = {}
            ts_value = metadata.get("ts")
            ts = str(ts_value).strip() if isinstance(ts_value, str) else None
            if not ts:
                started_at = run_data.get("started_at")
                ts = str(started_at).strip() if isinstance(started_at, str) else None

            latest = {
                "ts": ts,
                "routing_class": str(routing.get("routing_class", "general") or "general"),
                "provider_selected": provider_selected,
                "provider_model": (
                    str(routing.get("model_selected")).strip()
                    if routing.get("model_selected") is not None
                    else None
                ),
                "moderation_provider_used": (
                    str(routing.get("moderation_provider_used")).strip().lower()
                    if routing.get("moderation_provider_used") is not None
                    else None
                ),
                "moderation_result": (
                    str(routing.get("moderation_result")).strip().lower()
                    if routing.get("moderation_result") is not None
                    else None
                ),
                "override_mode": str(routing.get("override_mode", "default") or "default"),
            }
            return latest
        return latest

    def get_routing_status(self) -> Dict[str, Any]:
        routing_status = get_routing_runtime_status()
        _, _, _, blocked_by, can_post = self._current_blocks_and_provider(
            reload_control_from_disk=False
        )
        model_cfg = get_resolved_model_config()
        provider_models_raw = model_cfg.get("provider_models", {})
        provider_models = dict(provider_models_raw) if isinstance(provider_models_raw, dict) else {}
        with self._lock:
            active_director = self.normalize_active_director(self._control_state.get("active_director"))
        _, run_data = self._load_latest_run()
        return {
            "enabled": bool(routing_status.get("enabled", True)),
            "default_provider": str(routing_status.get("default_provider", "openai") or "openai"),
            "general_route_mode": str(routing_status.get("general_route_mode", "active_provider") or "active_provider"),
            "music_route_provider": str(routing_status.get("music_route_provider", "grok") or "grok"),
            "moderation_provider": str(routing_status.get("moderation_provider", "openai") or "openai"),
            "manual_override": str(routing_status.get("manual_override", "default") or "default"),
            "provider_weights": dict(routing_status.get("provider_weights", {})),
            "classification_rules": dict(routing_status.get("classification_rules", {})),
            "can_post": can_post,
            "blocked_by": blocked_by,
            "active_director": active_director,
            "provider_models": provider_models,
            "music_route_model": str(
                provider_models.get(str(routing_status.get("music_route_provider", "grok") or "grok"), "")
            ).strip()
            or None,
            "default_model": str(
                provider_models.get(str(routing_status.get("default_provider", "openai") or "openai"), "")
            ).strip()
            or None,
            "last_decision": self._latest_routing_decision(run_data),
        }

    def set_routing_config(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        old_cfg, new_cfg = update_routing_runtime_controls(payload)
        return {
            "old": {
                "enabled": bool(old_cfg.get("enabled", True)),
                "manual_override": str(old_cfg.get("manual_override", "default") or "default"),
                "general_route_mode": str(old_cfg.get("general_route_mode", "active_provider") or "active_provider"),
                "provider_weights": dict(old_cfg.get("provider_weights", {})),
            },
            "new": {
                "enabled": bool(new_cfg.get("enabled", True)),
                "manual_override": str(new_cfg.get("manual_override", "default") or "default"),
                "general_route_mode": str(new_cfg.get("general_route_mode", "active_provider") or "active_provider"),
                "provider_weights": dict(new_cfg.get("provider_weights", {})),
            },
        }

    def get_system_health(self) -> Dict[str, Any]:
        metrics = get_provider_runtime_metrics()
        routing_cfg = get_routing_runtime_status()
        config_paths = get_runtime_config_paths()

        memory_reachable = False
        try:
            with sqlite3.connect(str(self._memory_db_path)) as conn:
                conn.execute("SELECT 1").fetchone()
            memory_reachable = True
        except sqlite3.Error:
            memory_reachable = False

        try:
            memory_size = int(self._memory_db_path.stat().st_size) if self._memory_db_path.exists() else 0
        except OSError:
            memory_size = 0

        providers_cfg_path = config_paths.get("providers_config")
        routing_cfg_path = config_paths.get("routing_config")
        return {
            "providers": dict(metrics.get("providers", {})),
            "routing": {
                "enabled": bool(routing_cfg.get("enabled", False)),
                "music_culture_hits": int(metrics.get("routing", {}).get("music_culture_hits", 0)),
                "general_hits": int(metrics.get("routing", {}).get("general_hits", 0)),
                "override_hits": int(metrics.get("routing", {}).get("override_hits", 0)),
            },
            "memory_db": {
                "reachable": memory_reachable,
                "file_size_bytes": memory_size,
            },
            "providers_config_present": bool(isinstance(providers_cfg_path, Path) and providers_cfg_path.exists()),
            "routing_config_present": bool(isinstance(routing_cfg_path, Path) and routing_cfg_path.exists()),
            "last_error": metrics.get("last_error"),
        }

    def build_system_export_zip(self) -> bytes:
        # Seed required config files deterministically.
        _ = get_provider_runtime_status()
        _ = get_routing_runtime_status()
        _ = self.get_studio_profile()
        _ = self.get_senses_status()
        _ = self.get_twitch_status()
        self._init_memory_db()

        export_files: List[Tuple[Path, str]] = []
        paths = get_runtime_config_paths()
        providers_cfg = paths.get("providers_config")
        routing_cfg = paths.get("routing_config")
        if isinstance(providers_cfg, Path):
            export_files.append((providers_cfg, "data/providers_config.json"))
        if isinstance(routing_cfg, Path):
            export_files.append((routing_cfg, "data/routing_config.json"))
        export_files.extend(
            [
                (self._studio_profile_path, "data/studio_profile.json"),
                (self._senses_config_path, "data/senses_config.json"),
                (self._twitch_config_path, "data/twitch_config.json"),
                (self._memory_db_path, "data/memory.sqlite"),
            ]
        )

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path, arcname in export_files:
                try:
                    payload = path.read_bytes()
                except OSError:
                    payload = b""
                zf.writestr(arcname, payload)
        return buf.getvalue()

    def set_active_provider(self, provider: str) -> Dict[str, Any]:
        old_cfg, new_cfg = set_provider_active(provider)
        return {
            "old": {
                "active_provider": old_cfg.get("active_provider"),
                "approved_providers": old_cfg.get("approved_providers", []),
            },
            "new": {
                "active_provider": new_cfg.get("active_provider"),
                "approved_providers": new_cfg.get("approved_providers", []),
            },
        }

    @staticmethod
    def _cap_bounds_for_role(role: str) -> Dict[str, Tuple[int, int]]:
        role_norm = DashboardStorage.normalize_role(role)
        if role_norm == "director":
            return {
                "daily_requests_max": (0, 100_000),
                "daily_tokens_max": (0, 100_000_000),
            }
        return {
            "daily_requests_max": (0, 5_000),
            "daily_tokens_max": (0, 5_000_000),
        }

    def set_provider_caps(self, payload: Dict[str, Any], *, role: str = "operator") -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("caps payload must be an object.")
        role_norm = self.normalize_role(role)
        clean: Dict[str, Any] = {}
        bounds = self._cap_bounds_for_role(role_norm)
        for field in ("daily_requests_max", "daily_tokens_max"):
            if field in payload:
                try:
                    value = int(payload.get(field))
                except (TypeError, ValueError):
                    raise ValueError(f"{field} must be an integer.") from None
                lo, hi = bounds[field]
                if value < lo or value > hi:
                    if role_norm != "director":
                        raise PermissionError(f"{field} outside operator bounds ({lo}-{hi}).")
                    raise ValueError(f"{field} must be between {lo} and {hi}.")
                clean[field] = value
        if "hard_stop_on_cap" in payload:
            if role_norm != "director":
                raise PermissionError("hard_stop_on_cap changes require director role.")
            clean["hard_stop_on_cap"] = bool(payload.get("hard_stop_on_cap"))
        if not clean:
            raise ValueError("No supported cap fields provided.")
        old_cfg, new_cfg = update_provider_caps(clean)
        return {
            "old": {"caps": old_cfg.get("caps", {})},
            "new": {"caps": new_cfg.get("caps", {})},
        }

    def set_eventsub_runtime_state(
        self,
        *,
        connected: Optional[bool] = None,
        session_id: Optional[str] = None,
        last_message_ts: Optional[str] = None,
        reconnect_count: Optional[int] = None,
        last_error: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            state = self._eventsub_runtime_state
            if connected is not None:
                state["eventsub_connected"] = bool(connected)
            if session_id is not None:
                state["eventsub_session_id"] = str(session_id).strip() or None
            if last_message_ts is not None:
                state["last_eventsub_message_ts"] = str(last_message_ts).strip() or None
            if reconnect_count is not None:
                try:
                    state["reconnect_count"] = max(0, int(reconnect_count))
                except (TypeError, ValueError):
                    pass
            if last_error is not None:
                state["eventsub_last_error"] = str(last_error).strip() or None
            return dict(state)

    def get_eventsub_runtime_state(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._eventsub_runtime_state)

    def set_audio_runtime_state(self, state: Dict[str, Any]) -> None:
        with self._lock:
            self._audio_runtime_state = dict(state)

    def get_audio_runtime_state(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._audio_runtime_state)

    @staticmethod
    def list_audio_devices() -> list:
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            return [
                {
                    "index": i,
                    "name": d["name"],
                    "max_input_channels": d["max_input_channels"],
                    "default_samplerate": d["default_samplerate"],
                }
                for i, d in enumerate(devices)
                if d["max_input_channels"] > 0
            ]
        except Exception:
            return []

    def record_send_failure(self, reason: str) -> None:
        with self._lock:
            self._send_failure_state["fail_count"] = int(self._send_failure_state.get("fail_count", 0)) + 1
            self._send_failure_state["last_fail_reason"] = str(reason).strip() or "UNKNOWN"
            self._send_failure_state["last_fail_at"] = datetime.now(timezone.utc).isoformat()

    def record_send_success(self) -> None:
        with self._lock:
            self._send_failure_state["fail_count"] = 0
            self._send_failure_state["last_fail_reason"] = None
            self._send_failure_state["last_fail_at"] = None
            self._send_failure_state["last_success_at"] = datetime.now(timezone.utc).isoformat()

    def get_send_failure_state(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._send_failure_state)

    def record_eventsub_notification(
        self,
        *,
        twitch_event_id: str,
        event_type: str,
        session_id: Optional[str],
        emitted: bool,
        suppression_reason: Optional[str],
    ) -> None:
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "twitch_event_id": str(twitch_event_id or "").strip() or None,
            "event_type": str(event_type or "").strip().upper() or "UNKNOWN",
            "session_id": (session_id.strip() if isinstance(session_id, str) else None),
            "emitted": bool(emitted),
            "suppression_reason": str(suppression_reason or "").strip() or None,
        }
        self._eventsub_events_log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._eventsub_events_log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            return
        self._apply_retention_policy()

    @staticmethod
    def _twitch_access_token_without_prefix(token: str) -> str:
        text = str(token or "").strip()
        if text.lower().startswith("oauth:"):
            return text.split(":", 1)[1].strip()
        return text

    def _resolve_twitch_user_id(self, *, login: str, oauth_token: str, client_id: str) -> Optional[str]:
        clean_login = str(login or "").strip().lstrip("#").lower()
        token = self._twitch_access_token_without_prefix(oauth_token)
        if not clean_login or not token or not client_id:
            return None
        url = f"https://api.twitch.tv/helix/users?login={quote_plus(clean_login)}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Client-ID": client_id,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=8.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            return None
        data = payload.get("data", []) if isinstance(payload, dict) else []
        if not isinstance(data, list) or not data:
            return None
        row = data[0] if isinstance(data[0], dict) else {}
        user_id = str(row.get("id", "")).strip()
        return user_id or None

    def get_eventsub_runtime_credentials(self) -> Dict[str, Any]:
        if self._twitch_auto_refresh_enabled():
            try:
                self.refresh_twitch_tokens_if_needed(force=False)
            except Exception:
                pass
        checked_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            runtime_config = self._twitch_runtime_config_locked()
            state = self._load_twitch_auth_state_locked()
            account_status = self._twitch_account_status_locked(
                "broadcaster",
                state,
                checked_at=checked_at,
                runtime_config=runtime_config,
            )
            accounts = state.get("accounts", {}) if isinstance(state, dict) else {}
            row = accounts.get("broadcaster", {}) if isinstance(accounts, dict) else {}
            if not isinstance(row, dict):
                row = {}
            disconnected = bool(row.get("disconnected", False))
            local_raw = row.get("token")
            local_token = local_raw.strip() if isinstance(local_raw, str) else ""
            env_token = self._twitch_env_value(self._twitch_token_env_names("broadcaster"))
            oauth_token = "" if disconnected else (local_token or env_token)
            client_id = str(runtime_config.get("client_id", "")).strip()
            primary_channel = str(runtime_config.get("primary_channel", "")).strip().lstrip("#").lower()

        if not bool(account_status.get("connected", False)):
            reason = str(account_status.get("reason") or "DISCONNECTED").strip() or "DISCONNECTED"
            return {
                "ok": False,
                "error": reason,
                "detail": str(account_status.get("reason_detail") or "Broadcaster account not connected."),
            }
        if not oauth_token:
            return {"ok": False, "error": "NO_TOKEN", "detail": "Missing broadcaster token for EventSub."}
        if not client_id:
            return {"ok": False, "error": "CONFIG_MISSING", "detail": "TWITCH_CLIENT_ID required for EventSub."}
        if not primary_channel:
            return {"ok": False, "error": "MISSING_PRIMARY_CHANNEL", "detail": "Primary channel is required."}

        broadcaster_user_id = self._resolve_twitch_user_id(
            login=primary_channel,
            oauth_token=oauth_token,
            client_id=client_id,
        )
        if not broadcaster_user_id:
            return {
                "ok": False,
                "error": "USER_LOOKUP_FAILED",
                "detail": f"Could not resolve broadcaster user id for '{primary_channel}'.",
            }
        return {
            "ok": True,
            "oauth_token": oauth_token,
            "client_id": client_id,
            "broadcaster_user_id": broadcaster_user_id,
            "primary_channel": primary_channel,
        }

    def fetch_channel_emotes(self) -> Dict[str, Any]:
        """Fetch custom channel emotes from Twitch Helix API."""
        checked_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            runtime_config = self._twitch_runtime_config_locked()
            state = self._load_twitch_auth_state_locked()
            account_status = self._twitch_account_status_locked(
                "broadcaster",
                state,
                checked_at=checked_at,
                runtime_config=runtime_config,
            )
            accounts = state.get("accounts", {}) if isinstance(state, dict) else {}
            row = accounts.get("broadcaster", {}) if isinstance(accounts, dict) else {}
            if not isinstance(row, dict):
                row = {}
            disconnected = bool(row.get("disconnected", False))
            local_raw = row.get("token")
            local_token = local_raw.strip() if isinstance(local_raw, str) else ""
            env_token = self._twitch_env_value(self._twitch_token_env_names("broadcaster"))
            oauth_token = "" if disconnected else (local_token or env_token)
            client_id = str(runtime_config.get("client_id", "")).strip()
            primary_channel = str(runtime_config.get("primary_channel", "")).strip().lstrip("#").lower()

        if not bool(account_status.get("connected", False)):
            return {"ok": False, "error": "Broadcaster account not connected."}
        if not oauth_token:
            return {"ok": False, "error": "Missing broadcaster token."}
        if not client_id:
            return {"ok": False, "error": "TWITCH_CLIENT_ID required."}
        if not primary_channel:
            return {"ok": False, "error": "Primary channel is required."}

        broadcaster_user_id = self._resolve_twitch_user_id(
            login=primary_channel,
            oauth_token=oauth_token,
            client_id=client_id,
        )
        if not broadcaster_user_id:
            return {"ok": False, "error": f"Could not resolve user id for '{primary_channel}'."}

        token = self._twitch_access_token_without_prefix(oauth_token)
        url = f"https://api.twitch.tv/helix/chat/emotes?broadcaster_id={quote_plus(broadcaster_user_id)}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Client-ID": client_id,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=8.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            return {"ok": False, "error": f"Twitch API error: {exc}"}

        data = payload.get("data", []) if isinstance(payload, dict) else []
        if not isinstance(data, list):
            data = []
        emotes = []
        for entry in data:
            if isinstance(entry, dict):
                name = str(entry.get("name", "")).strip()
                if not name:
                    continue
                emote_id = str(entry.get("id", "")).strip()
                images = entry.get("images", {})
                url = ""
                if isinstance(images, dict):
                    url = str(images.get("url_1x", "")).strip()
                if not url and emote_id:
                    url = f"https://static-cdn.jtvnbs.net/emoticons/v2/{emote_id}/static/dark/1.0"
                emotes.append({"name": name, "id": emote_id, "url": url})
        return {"ok": True, "emotes": emotes}

    def _default_twitch_config(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "primary_channel": "",
            "bot_account_name": self._twitch_account_display("bot"),
            "broadcaster_account_name": self._twitch_account_display("broadcaster"),
        }

    def _normalize_twitch_config(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        raw = payload if isinstance(payload, dict) else {}
        base = self._default_twitch_config()
        primary = self._bounded_text(
            raw.get("primary_channel", base["primary_channel"]),
            field_name="primary_channel",
            max_len=80,
            required=False,
        ).lstrip("#")
        bot_name = self._bounded_text(
            raw.get("bot_account_name", base["bot_account_name"]),
            field_name="bot_account_name",
            max_len=80,
            required=False,
        ) or base["bot_account_name"]
        broadcaster_name = self._bounded_text(
            raw.get("broadcaster_account_name", base["broadcaster_account_name"]),
            field_name="broadcaster_account_name",
            max_len=80,
            required=False,
        ) or base["broadcaster_account_name"]
        return {
            "version": 1,
            "primary_channel": str(primary).strip().lower(),
            "bot_account_name": bot_name,
            "broadcaster_account_name": broadcaster_name,
        }

    def _read_or_create_twitch_config_locked(self) -> Dict[str, Any]:
        raw = _safe_read_json(self._twitch_config_path)
        if isinstance(raw, dict):
            config = self._normalize_twitch_config(raw)
        else:
            config = self._normalize_twitch_config(self._default_twitch_config())
        self._write_json_atomic(self._twitch_config_path, config)
        return config

    def _ensure_twitch_config(self) -> None:
        with self._lock:
            self._read_or_create_twitch_config_locked()

    def _twitch_runtime_config_locked(self) -> Dict[str, Any]:
        config = self._read_or_create_twitch_config_locked()
        auth_flow = self._twitch_auth_flow()
        client_id = str(os.getenv("TWITCH_CLIENT_ID", "")).strip()
        client_secret = str(os.getenv("TWITCH_CLIENT_SECRET", "")).strip()
        redirect_uri = str(os.getenv("TWITCH_REDIRECT_URI", "")).strip()
        env_primary = self._twitch_env_value(["TWITCH_CHANNEL"])
        primary_channel = str(env_primary or config.get("primary_channel", "")).strip().lstrip("#").lower()
        if primary_channel and str(os.getenv("TWITCH_CHANNEL", "")).strip() != primary_channel:
            os.environ["TWITCH_CHANNEL"] = primary_channel
        missing: List[str] = []
        if not client_id:
            missing.append("TWITCH_CLIENT_ID")
        if auth_flow == "authorization_code":
            if not client_secret:
                missing.append("TWITCH_CLIENT_SECRET")
            if not redirect_uri:
                missing.append("TWITCH_REDIRECT_URI")
        if not primary_channel:
            missing.append("PRIMARY_CHANNEL")
        return {
            "auth_flow": auth_flow,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "primary_channel": primary_channel,
            "bot_account_name": str(config.get("bot_account_name") or self._twitch_account_display("bot")),
            "broadcaster_account_name": str(
                config.get("broadcaster_account_name") or self._twitch_account_display("broadcaster")
            ),
            "missing_config_fields": missing,
        }

    @staticmethod
    def _twitch_status_cache_ttl_seconds() -> float:
        raw = os.getenv("ROONIE_TWITCH_STATUS_CACHE_TTL_SECONDS", "2.0")
        try:
            return max(0.0, min(float(raw), 60.0))
        except (TypeError, ValueError):
            return 2.0

    @staticmethod
    def _twitch_revoke_timeout_seconds() -> float:
        raw = os.getenv("ROONIE_TWITCH_REVOKE_TIMEOUT_SECONDS", "4.0")
        try:
            return max(1.0, min(float(raw), 15.0))
        except (TypeError, ValueError):
            return 4.0

    @staticmethod
    def _twitch_auto_refresh_enabled() -> bool:
        raw = os.getenv("ROONIE_TWITCH_AUTO_REFRESH", "1")
        return _to_bool(raw, True)

    @staticmethod
    def _twitch_refresh_lead_seconds() -> int:
        raw = os.getenv("ROONIE_TWITCH_REFRESH_LEAD_SECONDS", "900")
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            parsed = 900
        return max(60, min(parsed, 86_400))

    @staticmethod
    def _twitch_auth_flow() -> str:
        raw = str(os.getenv("ROONIE_TWITCH_AUTH_FLOW", "authorization_code")).strip().lower()
        if raw in {"device", "device_code", "device-code", "oauth_device_code"}:
            return "device_code"
        return "authorization_code"

    @staticmethod
    def _setup_gate_enforced() -> bool:
        # Explicit gate flag wins when set.
        explicit = str(os.getenv("ROONIE_ENFORCE_SETUP_GATE", "")).strip()
        if explicit:
            return _to_bool(explicit, False)
        # Backward-compatible alias for rollout scripts.
        legacy = str(os.getenv("ROONIE_REQUIRE_SETUP_WIZARD", "")).strip()
        if legacy:
            return _to_bool(legacy, False)
        # Default to on when true live-provider mode is enabled.
        return _to_bool(os.getenv("ROONIE_ENABLE_LIVE_PROVIDER_NETWORK", "0"), False)

    @staticmethod
    def _provider_key_presence() -> Dict[str, bool]:
        return {
            "openai": bool(str(os.getenv("OPENAI_API_KEY", "")).strip()),
            "grok": bool(str(os.getenv("GROK_API_KEY", "")).strip() or str(os.getenv("XAI_API_KEY", "")).strip()),
            "anthropic": bool(str(os.getenv("ANTHROPIC_API_KEY", "")).strip()),
        }

    def _setup_wizard_status(
        self,
        *,
        runtime_config: Dict[str, Any],
        accounts: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        missing_fields = list(runtime_config.get("missing_config_fields", []))
        config_ready = len(missing_fields) == 0
        bot_connected = bool((accounts.get("bot") or {}).get("connected", False))
        broadcaster_connected = bool((accounts.get("broadcaster") or {}).get("connected", False))
        key_presence = self._provider_key_presence()
        provider_keys_ready = any(key_presence.values())
        readiness_payload = self.get_readiness_state()
        runtime_ready = bool(readiness_payload.get("ready", False))

        blockers: List[str] = []
        if not config_ready:
            blockers.append("SETUP_TWITCH_CONFIG")
        if config_ready and not bot_connected:
            blockers.append("SETUP_TWITCH_BOT_AUTH")
        if config_ready and not broadcaster_connected:
            blockers.append("SETUP_TWITCH_BROADCASTER_AUTH")
        if not provider_keys_ready:
            blockers.append("SETUP_PROVIDER_KEYS")
        if not runtime_ready:
            blockers.append("SETUP_RUNTIME_READINESS")

        steps = [
            {
                "id": "twitch_config",
                "label": "Twitch runtime config",
                "ready": config_ready,
                "detail": ("Missing: " + ", ".join(missing_fields)) if (not config_ready and missing_fields) else "",
            },
            {
                "id": "bot_auth",
                "label": "Bot account connected",
                "ready": bot_connected,
                "detail": "",
            },
            {
                "id": "broadcaster_auth",
                "label": "Broadcaster account connected",
                "ready": broadcaster_connected,
                "detail": "",
            },
            {
                "id": "provider_keys",
                "label": "Provider API key available",
                "ready": provider_keys_ready,
                "detail": "Expected one of OPENAI_API_KEY, GROK_API_KEY/XAI_API_KEY, ANTHROPIC_API_KEY.",
            },
            {
                "id": "runtime_readiness",
                "label": "System readiness",
                "ready": runtime_ready,
                "detail": "",
            },
        ]
        return {
            "enforced": self._setup_gate_enforced(),
            "complete": len(blockers) == 0,
            "blockers": blockers,
            "steps": steps,
            "provider_keys": key_presence,
        }

    def _active_setup_gate_blockers(self) -> List[str]:
        try:
            payload = self.get_twitch_status(force_refresh=False)
        except Exception:
            return []
        setup = payload.get("setup", {}) if isinstance(payload, dict) else {}
        if not isinstance(setup, dict):
            return []
        if not bool(setup.get("enforced", False)):
            return []
        blockers_raw = setup.get("blockers", [])
        if not isinstance(blockers_raw, list):
            return []
        out: List[str] = []
        for item in blockers_raw:
            text = str(item or "").strip().upper()
            if text:
                out.append(text)
        return out

    def _invalidate_twitch_status_cache_locked(self) -> None:
        self._twitch_status_cache = None
        self._twitch_status_cache_expiry_ts = 0.0

    @staticmethod
    def _twitch_account_names() -> Tuple[str, str]:
        return ("bot", "broadcaster")

    @staticmethod
    def _twitch_token_env_names(account: str) -> List[str]:
        if account == "bot":
            return ["TWITCH_OAUTH_TOKEN", "TWITCH_OAUTH"]
        return ["TWITCH_BROADCASTER_OAUTH_TOKEN", "TWITCH_BROADCASTER_TOKEN"]

    @staticmethod
    def _twitch_account_display(account: str) -> str:
        if account == "bot":
            return "RoonieTheCat"
        return "RuleOfRune"

    @staticmethod
    def _twitch_account_role(account: str) -> str:
        if account == "bot":
            return "BOT ACCOUNT"
        return "BROADCASTER"

    @staticmethod
    def _twitch_env_value(names: List[str]) -> str:
        for name in names:
            value = str(os.getenv(name, "")).strip()
            if value:
                return value
        return ""

    @staticmethod
    def _normalize_twitch_scopes(scopes_raw: Any) -> List[str]:
        if isinstance(scopes_raw, list):
            return [str(item).strip() for item in scopes_raw if str(item).strip()]
        if isinstance(scopes_raw, str):
            return sorted({token.strip() for token in re.split(r"[,\s]+", scopes_raw) if token.strip()})
        return []

    @staticmethod
    def _twitch_auth_state_encryption_enabled() -> bool:
        enabled = _to_bool(os.getenv("ROONIE_ENCRYPT_TWITCH_TOKENS_AT_REST", "1"), True)
        return bool(enabled) and os.name == "nt"

    @staticmethod
    def _encode_twitch_secret_for_disk(secret: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        text = str(secret or "").strip()
        if not text:
            return (None, None)
        if not DashboardStorage._twitch_auth_state_encryption_enabled():
            return (text, None)
        try:
            protected = _dpapi_protect_bytes(text.encode("utf-8"))
            return (None, "dpapi:" + base64.b64encode(protected).decode("ascii"))
        except Exception:
            # Fallback keeps runtime functional if DPAPI is unexpectedly unavailable.
            return (text, None)

    @staticmethod
    def _decode_twitch_secret_from_disk(secret: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        text = str(secret or "").strip()
        if not text:
            return (None, None)
        if not text.lower().startswith("dpapi:"):
            return (text, None)
        payload_b64 = text.split(":", 1)[1]
        if not payload_b64:
            return (None, text)
        try:
            protected = base64.b64decode(payload_b64.encode("ascii"), validate=True)
            clear = _dpapi_unprotect_bytes(protected).decode("utf-8")
            clear_text = str(clear).strip()
            if not clear_text:
                return (None, text)
            return (clear_text, text)
        except Exception:
            return (None, text)

    def _default_twitch_auth_state(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "accounts": {
                "bot": {
                    "token": None,
                    "token_enc": None,
                    "refresh_token": None,
                    "refresh_token_enc": None,
                    "expires_at": None,
                    "scopes": [],
                    "display_name": None,
                    "pending_state": None,
                    "pending_device_code": None,
                    "pending_user_code": None,
                    "pending_verification_uri": None,
                    "pending_device_expires_at": None,
                    "pending_poll_interval_seconds": None,
                    "pending_poll_next_at": None,
                    "updated_at": None,
                    "disconnected": False,
                },
                "broadcaster": {
                    "token": None,
                    "token_enc": None,
                    "refresh_token": None,
                    "refresh_token_enc": None,
                    "expires_at": None,
                    "scopes": [],
                    "display_name": None,
                    "pending_state": None,
                    "pending_device_code": None,
                    "pending_user_code": None,
                    "pending_verification_uri": None,
                    "pending_device_expires_at": None,
                    "pending_poll_interval_seconds": None,
                    "pending_poll_next_at": None,
                    "updated_at": None,
                    "disconnected": False,
                },
            },
        }

    def _normalize_twitch_auth_state(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return self._default_twitch_auth_state()
        accounts_raw = payload.get("accounts", {})
        if not isinstance(accounts_raw, dict):
            accounts_raw = {}
        accounts: Dict[str, Dict[str, Any]] = {}
        for account in self._twitch_account_names():
            row = accounts_raw.get(account, {})
            if not isinstance(row, dict):
                row = {}
            token_plain = row.get("token")
            token_str, token_enc_decoded = self._decode_twitch_secret_from_disk(token_plain)
            if token_str is None:
                token_str, token_enc_decoded = self._decode_twitch_secret_from_disk(row.get("token_enc"))
            refresh_plain = row.get("refresh_token")
            refresh_token_str, refresh_enc_decoded = self._decode_twitch_secret_from_disk(refresh_plain)
            if refresh_token_str is None:
                refresh_token_str, refresh_enc_decoded = self._decode_twitch_secret_from_disk(row.get("refresh_token_enc"))
            expires_at_raw = row.get("expires_at")
            expires_at = str(expires_at_raw).strip() if isinstance(expires_at_raw, str) and str(expires_at_raw).strip() else None
            display_name_raw = row.get("display_name")
            display_name = (
                str(display_name_raw).strip()
                if isinstance(display_name_raw, str) and str(display_name_raw).strip()
                else None
            )
            pending_state_raw = row.get("pending_state")
            pending_state = (
                str(pending_state_raw).strip()
                if isinstance(pending_state_raw, str) and str(pending_state_raw).strip()
                else None
            )
            pending_device_code_raw = row.get("pending_device_code")
            pending_device_code = (
                str(pending_device_code_raw).strip()
                if isinstance(pending_device_code_raw, str) and str(pending_device_code_raw).strip()
                else None
            )
            pending_user_code_raw = row.get("pending_user_code")
            pending_user_code = (
                str(pending_user_code_raw).strip()
                if isinstance(pending_user_code_raw, str) and str(pending_user_code_raw).strip()
                else None
            )
            pending_verification_uri_raw = row.get("pending_verification_uri")
            pending_verification_uri = (
                str(pending_verification_uri_raw).strip()
                if isinstance(pending_verification_uri_raw, str) and str(pending_verification_uri_raw).strip()
                else None
            )
            pending_device_expires_at_raw = row.get("pending_device_expires_at")
            pending_device_expires_at = (
                str(pending_device_expires_at_raw).strip()
                if isinstance(pending_device_expires_at_raw, str) and str(pending_device_expires_at_raw).strip()
                else None
            )
            pending_poll_interval_raw = row.get("pending_poll_interval_seconds")
            pending_poll_interval_seconds: Optional[int]
            if isinstance(pending_poll_interval_raw, int):
                pending_poll_interval_seconds = pending_poll_interval_raw if pending_poll_interval_raw > 0 else None
            else:
                pending_poll_interval_seconds = None
            pending_poll_next_at_raw = row.get("pending_poll_next_at")
            pending_poll_next_at = (
                str(pending_poll_next_at_raw).strip()
                if isinstance(pending_poll_next_at_raw, str) and str(pending_poll_next_at_raw).strip()
                else None
            )
            scopes_raw = row.get("scopes", [])
            scopes: List[str] = []
            if isinstance(scopes_raw, list):
                scopes = [str(item).strip() for item in scopes_raw if str(item).strip()]
            accounts[account] = {
                "token": token_str,
                "token_enc": token_enc_decoded,
                "refresh_token": refresh_token_str,
                "refresh_token_enc": refresh_enc_decoded,
                "expires_at": expires_at,
                "scopes": scopes,
                "display_name": display_name,
                "pending_state": pending_state,
                "pending_device_code": pending_device_code,
                "pending_user_code": pending_user_code,
                "pending_verification_uri": pending_verification_uri,
                "pending_device_expires_at": pending_device_expires_at,
                "pending_poll_interval_seconds": pending_poll_interval_seconds,
                "pending_poll_next_at": pending_poll_next_at,
                "disconnected": bool(row.get("disconnected", False)),
                "updated_at": str(row.get("updated_at")).strip() if row.get("updated_at") is not None else None,
            }
        return {"version": 1, "accounts": accounts}

    def _serialize_twitch_auth_state_for_disk(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        normalized = self._normalize_twitch_auth_state(payload)
        accounts_norm = normalized.get("accounts", {}) if isinstance(normalized, dict) else {}
        accounts: Dict[str, Dict[str, Any]] = {}
        for account in self._twitch_account_names():
            row = accounts_norm.get(account, {}) if isinstance(accounts_norm, dict) else {}
            if not isinstance(row, dict):
                row = {}
            disconnected = bool(row.get("disconnected", False))
            token_plain = str(row.get("token") or "").strip()
            refresh_plain = str(row.get("refresh_token") or "").strip()
            existing_token_enc = str(row.get("token_enc") or "").strip()
            existing_refresh_enc = str(row.get("refresh_token_enc") or "").strip()

            token_disk, token_enc_disk = self._encode_twitch_secret_for_disk(token_plain)
            refresh_disk, refresh_enc_disk = self._encode_twitch_secret_for_disk(refresh_plain)
            if not token_plain and existing_token_enc and not disconnected:
                token_disk = None
                token_enc_disk = existing_token_enc
            if not refresh_plain and existing_refresh_enc and not disconnected:
                refresh_disk = None
                refresh_enc_disk = existing_refresh_enc
            if disconnected:
                token_disk = None
                token_enc_disk = None
                refresh_disk = None
                refresh_enc_disk = None

            scopes_raw = row.get("scopes", [])
            scopes = [str(item).strip() for item in scopes_raw if str(item).strip()] if isinstance(scopes_raw, list) else []
            accounts[account] = {
                "token": token_disk,
                "token_enc": token_enc_disk,
                "refresh_token": refresh_disk,
                "refresh_token_enc": refresh_enc_disk,
                "expires_at": str(row.get("expires_at")).strip() if row.get("expires_at") is not None else None,
                "scopes": scopes,
                "display_name": str(row.get("display_name")).strip() if row.get("display_name") is not None else None,
                "pending_state": str(row.get("pending_state")).strip() if row.get("pending_state") is not None else None,
                "pending_device_code": (
                    str(row.get("pending_device_code")).strip()
                    if row.get("pending_device_code") is not None
                    else None
                ),
                "pending_user_code": (
                    str(row.get("pending_user_code")).strip()
                    if row.get("pending_user_code") is not None
                    else None
                ),
                "pending_verification_uri": (
                    str(row.get("pending_verification_uri")).strip()
                    if row.get("pending_verification_uri") is not None
                    else None
                ),
                "pending_device_expires_at": (
                    str(row.get("pending_device_expires_at")).strip()
                    if row.get("pending_device_expires_at") is not None
                    else None
                ),
                "pending_poll_interval_seconds": (
                    int(row.get("pending_poll_interval_seconds"))
                    if isinstance(row.get("pending_poll_interval_seconds"), int)
                    else None
                ),
                "pending_poll_next_at": (
                    str(row.get("pending_poll_next_at")).strip()
                    if row.get("pending_poll_next_at") is not None
                    else None
                ),
                "disconnected": disconnected,
                "updated_at": str(row.get("updated_at")).strip() if row.get("updated_at") is not None else None,
            }
        return {"version": 1, "accounts": accounts}

    def _load_twitch_auth_state_locked(self) -> Dict[str, Any]:
        raw = _safe_read_json(self._twitch_auth_state_path)
        if isinstance(raw, dict):
            payload = self._normalize_twitch_auth_state(raw)
        else:
            payload = self._default_twitch_auth_state()
        self._write_json_atomic(
            self._twitch_auth_state_path,
            self._serialize_twitch_auth_state_for_disk(payload),
        )
        return payload

    def _save_twitch_auth_state_locked(self, payload: Dict[str, Any]) -> None:
        self._write_json_atomic(
            self._twitch_auth_state_path,
            self._serialize_twitch_auth_state_for_disk(payload),
        )

    @staticmethod
    def _clear_twitch_pending_auth_locked(row: Dict[str, Any]) -> None:
        row["pending_state"] = None
        row["pending_device_code"] = None
        row["pending_user_code"] = None
        row["pending_verification_uri"] = None
        row["pending_device_expires_at"] = None
        row["pending_poll_interval_seconds"] = None
        row["pending_poll_next_at"] = None

    _DEFAULT_TWITCH_SCOPES = (
        "chat:read chat:edit "
        "moderator:read:followers "
        "channel:read:subscriptions "
        "bits:read"
    )

    @staticmethod
    def _twitch_device_scope_list() -> List[str]:
        scopes = str(os.getenv("TWITCH_REQUEST_SCOPES", DashboardStorage._DEFAULT_TWITCH_SCOPES)).strip() or DashboardStorage._DEFAULT_TWITCH_SCOPES
        return DashboardStorage._normalize_twitch_scopes(scopes)

    @staticmethod
    def _token_looks_valid(token: str) -> bool:
        text = str(token or "").strip()
        if not text:
            return False
        low = text.lower()
        if low.startswith("oauth:"):
            return len(text) > len("oauth:")
        # OAuth code flow stores a raw access token without oauth: prefix.
        return bool(re.fullmatch(r"[A-Za-z0-9_\-]{20,}", text))

    @staticmethod
    def _token_without_prefix(token: str) -> str:
        text = str(token or "").strip()
        if text.lower().startswith("oauth:"):
            return text.split(":", 1)[1].strip()
        return text

    @staticmethod
    def _twitch_remote_validation_enabled() -> bool:
        raw = os.getenv("ROONIE_TWITCH_VALIDATE_REMOTE", "0")
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    def _twitch_required_config(self) -> Dict[str, Any]:
        with self._lock:
            return self._twitch_runtime_config_locked()

    def _validate_twitch_token_remote(self, token: str) -> Dict[str, Any]:
        access_token = self._token_without_prefix(token)
        if not access_token:
            return {"ok": False, "error": "INVALID_TOKEN", "detail": "Empty token."}
        req = urllib.request.Request(
            "https://id.twitch.tv/oauth2/validate",
            method="GET",
            headers={"Authorization": f"OAuth {access_token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=4.0) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            code = getattr(exc, "code", None)
            if code == 401:
                return {"ok": False, "error": "INVALID_TOKEN", "detail": "Token rejected by Twitch."}
            return {"ok": False, "error": "INVALID_TOKEN", "detail": f"Twitch validation HTTP {code}."}
        except Exception as exc:  # pragma: no cover - network-dependent
            return {"ok": False, "error": "INVALID_TOKEN", "detail": f"Twitch validation failed: {exc}"}
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            return {"ok": False, "error": "INVALID_TOKEN", "detail": "Invalid validation response."}
        scopes_raw = payload.get("scopes", [])
        scopes = [str(item).strip() for item in scopes_raw if str(item).strip()] if isinstance(scopes_raw, list) else []
        login = str(payload.get("login", "")).strip() or None
        expires_in = payload.get("expires_in")
        return {
            "ok": True,
            "scopes": scopes,
            "login": login,
            "expires_in": int(expires_in) if isinstance(expires_in, int) else None,
        }

    def _revoke_twitch_token_remote(self, *, token: str, client_id: str) -> Dict[str, Any]:
        access_token = self._token_without_prefix(token)
        client_id_text = str(client_id or "").strip()
        if not client_id_text:
            return {"ok": False, "error": "CONFIG_MISSING", "detail": "TWITCH_CLIENT_ID is required for token revocation."}
        if not access_token:
            return {"ok": False, "error": "MISSING_TOKEN", "detail": "Token is empty."}

        query = urlencode({"client_id": client_id_text, "token": access_token})
        req = urllib.request.Request(
            f"https://id.twitch.tv/oauth2/revoke?{query}",
            data=b"",
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._twitch_revoke_timeout_seconds()) as response:
                _ = response.read()
        except urllib.error.HTTPError as exc:
            detail = f"Twitch revoke HTTP {getattr(exc, 'code', None)}."
            try:
                data = json.loads(exc.read().decode("utf-8"))
                if isinstance(data, dict):
                    msg = str(data.get("message") or "").strip()
                    if msg:
                        detail = msg
            except Exception:
                pass
            return {"ok": False, "error": "REVOKE_HTTP_ERROR", "detail": detail}
        except Exception as exc:  # pragma: no cover - network-dependent
            return {"ok": False, "error": "REVOKE_FAILED", "detail": str(exc)}
        return {"ok": True}

    def _exchange_twitch_code(self, *, code: str, redirect_uri: str, client_id: str, client_secret: str) -> Dict[str, Any]:
        payload = urlencode(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            "https://id.twitch.tv/oauth2/token",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=6.0) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = "Token exchange failed."
            try:
                data = json.loads(exc.read().decode("utf-8"))
                if isinstance(data, dict) and data.get("message"):
                    detail = str(data.get("message"))
            except Exception:
                pass
            return {"ok": False, "error": "TOKEN_EXCHANGE_FAILED", "detail": detail}
        except Exception as exc:  # pragma: no cover - network-dependent
            return {"ok": False, "error": "TOKEN_EXCHANGE_FAILED", "detail": str(exc)}
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            return {"ok": False, "error": "TOKEN_EXCHANGE_FAILED", "detail": "Invalid token response."}
        access_token = str(data.get("access_token", "")).strip()
        if not access_token:
            return {"ok": False, "error": "TOKEN_EXCHANGE_FAILED", "detail": "Missing access token."}
        scopes = self._normalize_twitch_scopes(data.get("scope", []))
        refresh_token = str(data.get("refresh_token", "")).strip() or None
        expires_in = data.get("expires_in")
        expires_at = None
        if isinstance(expires_in, int) and expires_in > 0:
            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()
        return {
            "ok": True,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "scopes": scopes,
            "expires_at": expires_at,
        }

    def _start_twitch_device_code(self, *, client_id: str, scopes: List[str]) -> Dict[str, Any]:
        client_id_text = str(client_id or "").strip()
        if not client_id_text:
            return {"ok": False, "error": "CONFIG_MISSING", "detail": "TWITCH_CLIENT_ID is required."}
        scope_text = " ".join([str(item).strip() for item in scopes if str(item).strip()])
        payload_dict: Dict[str, str] = {"client_id": client_id_text}
        if scope_text:
            payload_dict["scopes"] = scope_text
        payload = urlencode(payload_dict).encode("utf-8")
        req = urllib.request.Request(
            "https://id.twitch.tv/oauth2/device",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=6.0) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = "Device authorization request failed."
            oauth_error = ""
            try:
                data = json.loads(exc.read().decode("utf-8"))
                if isinstance(data, dict):
                    oauth_error = str(data.get("error", "")).strip().lower()
                    message = str(data.get("message", "")).strip()
                    if message:
                        detail = message
            except Exception:
                pass
            return {
                "ok": False,
                "error": "DEVICE_START_FAILED",
                "oauth_error": oauth_error or None,
                "detail": detail,
            }
        except Exception as exc:  # pragma: no cover - network-dependent
            return {"ok": False, "error": "DEVICE_START_FAILED", "detail": str(exc)}

        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            return {"ok": False, "error": "DEVICE_START_FAILED", "detail": "Invalid Twitch device response."}

        device_code = str(data.get("device_code", "")).strip()
        user_code = str(data.get("user_code", "")).strip()
        verification_uri = str(data.get("verification_uri", "")).strip()
        verification_uri_complete = str(data.get("verification_uri_complete", "")).strip() or None
        if not device_code or not user_code or not verification_uri:
            return {
                "ok": False,
                "error": "DEVICE_START_FAILED",
                "detail": "Missing required fields in Twitch device response.",
            }

        expires_at: Optional[str] = None
        expires_in_raw = data.get("expires_in")
        if isinstance(expires_in_raw, int) and expires_in_raw > 0:
            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in_raw)).isoformat()
        interval_raw = data.get("interval")
        interval_seconds = int(interval_raw) if isinstance(interval_raw, int) and interval_raw > 0 else 5
        interval_seconds = max(1, min(interval_seconds, 60))
        return {
            "ok": True,
            "device_code": device_code,
            "user_code": user_code,
            "verification_uri": verification_uri,
            "verification_uri_complete": verification_uri_complete,
            "expires_at": expires_at,
            "interval_seconds": interval_seconds,
        }

    def _exchange_twitch_device_code(self, *, client_id: str, device_code: str) -> Dict[str, Any]:
        payload = urlencode(
            {
                "client_id": str(client_id or "").strip(),
                "device_code": str(device_code or "").strip(),
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            "https://id.twitch.tv/oauth2/token",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=6.0) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = "Device token exchange failed."
            oauth_error = ""
            try:
                data = json.loads(exc.read().decode("utf-8"))
                if isinstance(data, dict):
                    oauth_error = str(data.get("error", "")).strip().lower()
                    message = str(data.get("message", "")).strip()
                    if message:
                        detail = message
            except Exception:
                pass
            return {
                "ok": False,
                "error": "DEVICE_TOKEN_EXCHANGE_FAILED",
                "oauth_error": oauth_error or None,
                "detail": detail,
            }
        except Exception as exc:  # pragma: no cover - network-dependent
            return {"ok": False, "error": "DEVICE_TOKEN_EXCHANGE_FAILED", "detail": str(exc)}

        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            return {"ok": False, "error": "DEVICE_TOKEN_EXCHANGE_FAILED", "detail": "Invalid token response."}
        access_token = str(data.get("access_token", "")).strip()
        if not access_token:
            return {"ok": False, "error": "DEVICE_TOKEN_EXCHANGE_FAILED", "detail": "Missing access token."}
        scopes = self._normalize_twitch_scopes(data.get("scope", []))
        refresh_token = str(data.get("refresh_token", "")).strip() or None
        expires_in = data.get("expires_in")
        expires_at = None
        if isinstance(expires_in, int) and expires_in > 0:
            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()
        return {
            "ok": True,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "scopes": scopes,
            "expires_at": expires_at,
        }

    def _refresh_twitch_access_token(
        self,
        *,
        refresh_token: str,
        client_id: str,
        client_secret: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload_data: Dict[str, str] = {
            "client_id": str(client_id or "").strip(),
            "refresh_token": str(refresh_token or "").strip(),
            "grant_type": "refresh_token",
        }
        client_secret_text = str(client_secret or "").strip()
        if client_secret_text:
            payload_data["client_secret"] = client_secret_text
        payload = urlencode(payload_data).encode("utf-8")
        req = urllib.request.Request(
            "https://id.twitch.tv/oauth2/token",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=6.0) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = "Token refresh failed."
            try:
                data = json.loads(exc.read().decode("utf-8"))
                if isinstance(data, dict):
                    message = str(data.get("message", "")).strip()
                    if message:
                        detail = message
            except Exception:
                pass
            low = detail.lower()
            if "invalid refresh token" in low or "refresh token is invalid" in low:
                return {"ok": False, "error": "INVALID_REFRESH_TOKEN", "detail": detail}
            return {"ok": False, "error": "TOKEN_REFRESH_FAILED", "detail": detail}
        except Exception as exc:  # pragma: no cover - network-dependent
            return {"ok": False, "error": "TOKEN_REFRESH_FAILED", "detail": str(exc)}

        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            return {"ok": False, "error": "TOKEN_REFRESH_FAILED", "detail": "Invalid token response."}
        access_token = str(data.get("access_token", "")).strip()
        if not access_token:
            return {"ok": False, "error": "TOKEN_REFRESH_FAILED", "detail": "Missing access token."}

        expires_at = None
        expires_in = data.get("expires_in")
        if isinstance(expires_in, int) and expires_in > 0:
            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()
        return {
            "ok": True,
            "access_token": access_token,
            "refresh_token": str(data.get("refresh_token", "")).strip() or None,
            "scopes": self._normalize_twitch_scopes(data.get("scope", [])),
            "expires_at": expires_at,
        }

    def _twitch_scopes_payload(self) -> Dict[str, Any]:
        scopes_raw = str(
            os.getenv("TWITCH_TOKEN_SCOPES")
            or os.getenv("TWITCH_SCOPES")
            or ""
        ).strip()
        scopes = sorted(
            {
                token.strip()
                for token in re.split(r"[,\s]+", scopes_raw)
                if token and token.strip()
            }
        )
        return {
            "scopes": scopes,
            "scopes_present": {
                "chat:read": ("chat:read" in scopes),
                "chat:edit": ("chat:edit" in scopes),
            },
        }

    def _twitch_account_status_locked(
        self,
        account: str,
        state: Dict[str, Any],
        *,
        checked_at: str,
        runtime_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        accounts = state.get("accounts", {}) if isinstance(state, dict) else {}
        row = accounts.get(account, {}) if isinstance(accounts, dict) else {}
        if not isinstance(row, dict):
            row = {}

        disconnected = bool(row.get("disconnected", False))
        local_raw = row.get("token")
        local_token = local_raw.strip() if isinstance(local_raw, str) else ""
        env_token = self._twitch_env_value(self._twitch_token_env_names(account))
        token = "" if disconnected else (local_token or env_token)
        scopes_raw = row.get("scopes", [])
        scopes = [str(item).strip() for item in scopes_raw if str(item).strip()] if isinstance(scopes_raw, list) else []
        configured_name = (
            runtime_config.get("bot_account_name")
            if account == "bot"
            else runtime_config.get("broadcaster_account_name")
        )
        display_name = str(row.get("display_name", "")).strip() or str(configured_name or self._twitch_account_display(account))
        auth_flow = str(runtime_config.get("auth_flow", "authorization_code")).strip() or "authorization_code"
        primary_channel = str(runtime_config.get("primary_channel", "")).strip()
        missing_fields = list(runtime_config.get("missing_config_fields", []))
        missing_set = {str(item).strip().upper() for item in missing_fields if str(item).strip()}
        pending_device_code = str(row.get("pending_device_code") or "").strip()
        pending_user_code = str(row.get("pending_user_code") or "").strip()
        pending_verification_uri = str(row.get("pending_verification_uri") or "").strip()
        pending_device_expires_at = str(row.get("pending_device_expires_at") or "").strip()
        pending_poll_interval_seconds_raw = row.get("pending_poll_interval_seconds")
        pending_poll_interval_seconds = (
            int(pending_poll_interval_seconds_raw)
            if isinstance(pending_poll_interval_seconds_raw, int) and pending_poll_interval_seconds_raw > 0
            else None
        )
        pending_expires_dt = _parse_iso(pending_device_expires_at)
        pending_expired = bool(
            pending_expires_dt is not None and pending_expires_dt <= datetime.now(timezone.utc)
        )
        pending_auth_active = bool(
            pending_device_code and pending_user_code and pending_verification_uri and not pending_expired
        )

        reason = "NO_TOKEN"
        connected = False
        token_source = "none"
        reason_detail: Optional[str] = None

        if "PRIMARY_CHANNEL" in missing_set:
            reason = "MISSING_PRIMARY_CHANNEL"
            reason_detail = "Set twitch_config.primary_channel (or TWITCH_CHANNEL) before connecting."
            token_source = "none" if not token else ("local" if local_token else "env")
        elif not token:
            reason = "PENDING_AUTH" if pending_auth_active else "NO_TOKEN"
            if pending_auth_active:
                reason_detail = "Open Twitch device verification page and approve the code to finish connecting."
            token_source = "none"
        elif not self._token_looks_valid(token):
            reason = "INVALID_TOKEN"
            token_source = "local" if local_token else "env"
        else:
            token_source = "local" if local_token else "env"
            reason = ""
            if account == "bot":
                nick = self._twitch_env_value(["TWITCH_BOT_NICK", "TWITCH_NICK"])
                if not nick:
                    derived_nick = str(row.get("display_name") or self._twitch_account_display("bot")).strip()
                    if derived_nick:
                        os.environ.setdefault("TWITCH_BOT_NICK", derived_nick)
                        os.environ.setdefault("TWITCH_NICK", derived_nick)
                    else:
                        reason = "CONFIG_MISSING"
                        reason_detail = "TWITCH_NICK required for bot account."

            oauth_required = [field for field in missing_fields if field != "PRIMARY_CHANNEL"]
            if not reason and oauth_required:
                reason = "CONFIG_MISSING"
                reason_detail = f"Missing required config: {', '.join(oauth_required)}"

            expires_at = _parse_iso(str(row.get("expires_at", "")).strip())
            if not reason and expires_at is not None and expires_at <= datetime.now(timezone.utc):
                reason = "EXPIRED"

            if not reason and self._twitch_remote_validation_enabled():
                validation = self._validate_twitch_token_remote(token)
                if not validation.get("ok"):
                    reason = str(validation.get("error") or "INVALID_TOKEN")
                    reason_detail = str(validation.get("detail") or "Token validation failed.")
                else:
                    login = validation.get("login")
                    if isinstance(login, str) and login.strip():
                        display_name = login.strip()
                    remote_scopes = validation.get("scopes")
                    if isinstance(remote_scopes, list):
                        scopes = [str(item).strip() for item in remote_scopes if str(item).strip()]

            if not reason:
                connected = True

        return {
            "account": account,
            "auth_flow": auth_flow,
            "display_name": display_name,
            "role": self._twitch_account_role(account),
            "connected": connected,
            "reason": (reason or None),
            "reason_detail": reason_detail,
            "token_source": token_source,
            "last_checked_ts": checked_at,
            "last_updated_at": row.get("updated_at"),
            "scopes": scopes,
            "connect_available": len(missing_fields) == 0,
            "disconnect_available": bool(local_token or env_token) and not disconnected,
            "primary_channel": primary_channel or None,
            "pending_auth": (
                {
                    "active": pending_auth_active,
                    "user_code": pending_user_code or None,
                    "verification_uri": pending_verification_uri or None,
                    "expires_at": pending_device_expires_at or None,
                    "poll_interval_seconds": pending_poll_interval_seconds,
                }
                if pending_auth_active
                else None
            ),
        }

    def twitch_connect_start(self, account: str) -> Dict[str, Any]:
        acc = str(account or "").strip().lower()
        if acc not in self._twitch_account_names():
            raise ValueError("account must be one of: bot, broadcaster")

        config = self._twitch_required_config()
        auth_flow = str(config.get("auth_flow", "authorization_code")).strip() or "authorization_code"
        client_id = str(config.get("client_id", "")).strip()
        redirect_uri = str(config.get("redirect_uri", "")).strip()
        missing = list(config.get("missing_config_fields", []))
        if missing:
            return {
                "ok": False,
                "account": acc,
                "flow": auth_flow,
                "error": "CONFIG_MISSING",
                "detail": f"Missing required config: {', '.join(missing)}",
                "missing": missing,
                "auth_url": None,
                "redirect_uri_used": redirect_uri or None,
                "state": None,
            }

        checked_at = datetime.now(timezone.utc).isoformat()
        if auth_flow == "device_code":
            started = self._start_twitch_device_code(
                client_id=client_id,
                scopes=self._twitch_device_scope_list(),
            )
            if not bool(started.get("ok", False)):
                return {
                    "ok": False,
                    "account": acc,
                    "flow": auth_flow,
                    "error": str(started.get("error") or "DEVICE_START_FAILED"),
                    "detail": str(started.get("detail") or "Could not start Twitch device authorization."),
                }
            interval_seconds = int(started.get("interval_seconds") or 5)
            interval_seconds = max(1, min(interval_seconds, 60))
            poll_next_at = datetime.now(timezone.utc).isoformat()
            with self._lock:
                state = self._load_twitch_auth_state_locked()
                accounts = state.setdefault("accounts", {})
                row = accounts.setdefault(acc, {})
                # Preserve explicit disconnect state until device auth is actually completed.
                # This prevents stale env tokens from making status look connected mid-flow.
                self._clear_twitch_pending_auth_locked(row)
                row["pending_device_code"] = str(started.get("device_code") or "").strip() or None
                row["pending_user_code"] = str(started.get("user_code") or "").strip() or None
                row["pending_verification_uri"] = str(started.get("verification_uri") or "").strip() or None
                row["pending_device_expires_at"] = str(started.get("expires_at") or "").strip() or None
                row["pending_poll_interval_seconds"] = interval_seconds
                row["pending_poll_next_at"] = poll_next_at
                row["updated_at"] = checked_at
                self._save_twitch_auth_state_locked(state)
                self._invalidate_twitch_status_cache_locked()
            return {
                "ok": True,
                "account": acc,
                "flow": "device_code",
                "user_code": str(started.get("user_code") or "").strip() or None,
                "verification_uri": str(started.get("verification_uri") or "").strip() or None,
                "verification_uri_complete": str(started.get("verification_uri_complete") or "").strip() or None,
                "expires_at": str(started.get("expires_at") or "").strip() or None,
                "poll_interval_seconds": interval_seconds,
                "detail": "Open Twitch verification URL and enter the code to connect this account.",
            }

        scopes = str(os.getenv("TWITCH_REQUEST_SCOPES", self._DEFAULT_TWITCH_SCOPES)).strip() or self._DEFAULT_TWITCH_SCOPES
        state_token = secrets.token_urlsafe(24)
        with self._lock:
            state = self._load_twitch_auth_state_locked()
            accounts = state.setdefault("accounts", {})
            row = accounts.setdefault(acc, {})
            # Preserve explicit disconnect state until callback completes.
            self._clear_twitch_pending_auth_locked(row)
            row["pending_state"] = state_token
            row["updated_at"] = checked_at
            self._save_twitch_auth_state_locked(state)
            self._invalidate_twitch_status_cache_locked()

        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": scopes,
            "state": state_token,
            "force_verify": "true",
        }
        auth_url = "https://id.twitch.tv/oauth2/authorize?" + urlencode(params, quote_via=quote_plus)
        return {
            "ok": True,
            "account": acc,
            "flow": "authorization_code",
            "auth_url": auth_url,
            "redirect_uri_used": redirect_uri,
            "state": state_token,
            "detail": "Auth URL generated.",
        }

    def twitch_connect_finish(self, *, code: str, state_token: str) -> Dict[str, Any]:
        code_text = str(code or "").strip()
        state_text = str(state_token or "").strip()
        if not code_text:
            return {"ok": False, "error": "BAD_REQUEST", "detail": "Missing code parameter."}
        if not state_text:
            return {"ok": False, "error": "BAD_REQUEST", "detail": "Missing state parameter."}

        config = self._twitch_required_config()
        auth_flow = str(config.get("auth_flow", "authorization_code")).strip() or "authorization_code"
        if auth_flow != "authorization_code":
            return {
                "ok": False,
                "error": "FLOW_NOT_ENABLED",
                "detail": "OAuth callback flow is disabled when ROONIE_TWITCH_AUTH_FLOW=device_code.",
            }
        client_id = str(config.get("client_id", "")).strip()
        client_secret = str(config.get("client_secret", "")).strip()
        redirect_uri = str(config.get("redirect_uri", "")).strip()
        missing = list(config.get("missing_config_fields", []))
        if missing:
            return {
                "ok": False,
                "error": "CONFIG_MISSING",
                "detail": f"Missing required config: {', '.join(missing)}",
                "missing": missing,
            }

        account: Optional[str] = None
        with self._lock:
            current = self._load_twitch_auth_state_locked()
            accounts = current.get("accounts", {}) if isinstance(current, dict) else {}
            if isinstance(accounts, dict):
                for candidate in self._twitch_account_names():
                    row = accounts.get(candidate, {})
                    if isinstance(row, dict) and str(row.get("pending_state", "")).strip() == state_text:
                        account = candidate
                        break
        if account is None:
            return {"ok": False, "error": "INVALID_STATE", "detail": "Unknown or expired OAuth state."}

        exchanged = self._exchange_twitch_code(
            code=code_text,
            redirect_uri=redirect_uri,
            client_id=client_id,
            client_secret=client_secret,
        )
        if not exchanged.get("ok"):
            return {
                "ok": False,
                "error": str(exchanged.get("error") or "TOKEN_EXCHANGE_FAILED"),
                "detail": str(exchanged.get("detail") or "Token exchange failed."),
            }

        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock:
            current = self._load_twitch_auth_state_locked()
            accounts = current.setdefault("accounts", {})
            row = accounts.setdefault(account, {})
            row["token"] = str(exchanged.get("access_token", "")).strip() or None
            row["token_enc"] = None
            row["refresh_token"] = str(exchanged.get("refresh_token", "")).strip() or None
            row["refresh_token_enc"] = None
            row["expires_at"] = exchanged.get("expires_at")
            row["scopes"] = list(exchanged.get("scopes", []))
            row["display_name"] = row.get("display_name") or self._twitch_account_display(account)
            row["disconnected"] = False
            row["updated_at"] = now_iso
            self._clear_twitch_pending_auth_locked(row)
            self._save_twitch_auth_state_locked(current)
            self._invalidate_twitch_status_cache_locked()

            # Restore env vars that twitch_disconnect may have popped.
            # Without this the TwitchOutputAdapter cannot find the token
            # even though the LiveChatBridge IRC read socket is still alive.
            new_token = str(row.get("token") or "").strip()
            if new_token:
                oauth_val = new_token if new_token.lower().startswith("oauth:") else f"oauth:{new_token}"
                for env_name in self._twitch_token_env_names(account):
                    os.environ[env_name] = oauth_val

        status_payload = self.get_twitch_status(force_refresh=True)
        account_status = status_payload.get("accounts", {}).get(account, {})
        return {
            "ok": True,
            "account": account,
            "flow": "authorization_code",
            "connected": bool(account_status.get("connected", False)),
            "status": status_payload,
            "detail": "Connected.",
        }

    def twitch_connect_poll(self, account: str) -> Dict[str, Any]:
        acc = str(account or "").strip().lower()
        if acc not in self._twitch_account_names():
            raise ValueError("account must be one of: bot, broadcaster")

        checked_at = datetime.now(timezone.utc).isoformat()
        config = self._twitch_required_config()
        auth_flow = str(config.get("auth_flow", "authorization_code")).strip() or "authorization_code"
        client_id = str(config.get("client_id", "")).strip()
        if auth_flow != "device_code":
            return {
                "ok": False,
                "account": acc,
                "flow": auth_flow,
                "error": "FLOW_NOT_ENABLED",
                "detail": "Device-code polling is only available when ROONIE_TWITCH_AUTH_FLOW=device_code.",
            }
        if not client_id:
            return {
                "ok": False,
                "account": acc,
                "flow": auth_flow,
                "error": "CONFIG_MISSING",
                "detail": "Missing required config: TWITCH_CLIENT_ID",
            }

        pending_device_code = ""
        pending_user_code = ""
        pending_verification_uri = ""
        pending_device_expires_at = ""
        poll_interval_seconds = 5
        poll_next_at_raw = ""
        with self._lock:
            state = self._load_twitch_auth_state_locked()
            accounts = state.setdefault("accounts", {})
            row = accounts.setdefault(acc, {})
            pending_device_code = str(row.get("pending_device_code") or "").strip()
            pending_user_code = str(row.get("pending_user_code") or "").strip()
            pending_verification_uri = str(row.get("pending_verification_uri") or "").strip()
            pending_device_expires_at = str(row.get("pending_device_expires_at") or "").strip()
            poll_interval_raw = row.get("pending_poll_interval_seconds")
            if isinstance(poll_interval_raw, int) and poll_interval_raw > 0:
                poll_interval_seconds = poll_interval_raw
            poll_next_at_raw = str(row.get("pending_poll_next_at") or "").strip()

        if not pending_device_code:
            return {
                "ok": False,
                "account": acc,
                "flow": auth_flow,
                "error": "NO_PENDING_DEVICE_AUTH",
                "detail": "No pending device-code authorization for this account.",
            }

        now_utc = datetime.now(timezone.utc)
        expires_at_dt = _parse_iso(pending_device_expires_at)
        if expires_at_dt is not None and expires_at_dt <= now_utc:
            with self._lock:
                state = self._load_twitch_auth_state_locked()
                accounts = state.setdefault("accounts", {})
                row = accounts.setdefault(acc, {})
                self._clear_twitch_pending_auth_locked(row)
                row["updated_at"] = checked_at
                self._save_twitch_auth_state_locked(state)
                self._invalidate_twitch_status_cache_locked()
            return {
                "ok": False,
                "account": acc,
                "flow": auth_flow,
                "error": "DEVICE_CODE_EXPIRED",
                "detail": "Device code expired. Start connection again.",
            }

        poll_next_at_dt = _parse_iso(poll_next_at_raw)
        if poll_next_at_dt is not None and now_utc < poll_next_at_dt:
            retry_after = int(max(1, (poll_next_at_dt - now_utc).total_seconds()))
            return {
                "ok": True,
                "account": acc,
                "flow": auth_flow,
                "pending": True,
                "connected": False,
                "retry_after_seconds": retry_after,
                "user_code": pending_user_code or None,
                "verification_uri": pending_verification_uri or None,
                "expires_at": pending_device_expires_at or None,
                "detail": "Authorization still pending.",
            }

        exchanged = self._exchange_twitch_device_code(client_id=client_id, device_code=pending_device_code)
        if not bool(exchanged.get("ok", False)):
            oauth_error = str(exchanged.get("oauth_error") or "").strip().lower()
            if not oauth_error:
                # Twitch may return only `message: authorization_pending` without an `error` field.
                detail_text = str(exchanged.get("detail") or "").strip().lower()
                if detail_text:
                    detail_norm = re.sub(r"[\s\-]+", "_", detail_text)
                    if "authorization_pending" in detail_norm:
                        oauth_error = "authorization_pending"
                    elif "slow_down" in detail_norm:
                        oauth_error = "slow_down"
                    elif "expired_token" in detail_norm:
                        oauth_error = "expired_token"
                    elif "access_denied" in detail_norm:
                        oauth_error = "access_denied"
                    elif "invalid_device_code" in detail_norm:
                        oauth_error = "invalid_device_code"
            if oauth_error in {"authorization_pending", "slow_down"}:
                next_interval = poll_interval_seconds
                if oauth_error == "slow_down":
                    next_interval = min(60, poll_interval_seconds + 5)
                next_poll_at = (datetime.now(timezone.utc) + timedelta(seconds=next_interval)).isoformat()
                with self._lock:
                    state = self._load_twitch_auth_state_locked()
                    accounts = state.setdefault("accounts", {})
                    row = accounts.setdefault(acc, {})
                    if str(row.get("pending_device_code") or "").strip() == pending_device_code:
                        row["pending_poll_interval_seconds"] = next_interval
                        row["pending_poll_next_at"] = next_poll_at
                        row["updated_at"] = checked_at
                        self._save_twitch_auth_state_locked(state)
                        self._invalidate_twitch_status_cache_locked()
                return {
                    "ok": True,
                    "account": acc,
                    "flow": auth_flow,
                    "pending": True,
                    "connected": False,
                    "retry_after_seconds": next_interval,
                    "user_code": pending_user_code or None,
                    "verification_uri": pending_verification_uri or None,
                    "expires_at": pending_device_expires_at or None,
                    "detail": "Authorization still pending.",
                }

            terminal_error = oauth_error in {"expired_token", "access_denied", "invalid_device_code"}
            if terminal_error:
                with self._lock:
                    state = self._load_twitch_auth_state_locked()
                    accounts = state.setdefault("accounts", {})
                    row = accounts.setdefault(acc, {})
                    self._clear_twitch_pending_auth_locked(row)
                    row["updated_at"] = checked_at
                    self._save_twitch_auth_state_locked(state)
                    self._invalidate_twitch_status_cache_locked()
            return {
                "ok": False,
                "account": acc,
                "flow": auth_flow,
                "error": str(exchanged.get("error") or "DEVICE_TOKEN_EXCHANGE_FAILED"),
                "detail": str(exchanged.get("detail") or "Device authorization failed."),
            }

        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock:
            current = self._load_twitch_auth_state_locked()
            accounts = current.setdefault("accounts", {})
            row = accounts.setdefault(acc, {})
            row["token"] = str(exchanged.get("access_token", "")).strip() or None
            row["token_enc"] = None
            row["refresh_token"] = str(exchanged.get("refresh_token", "")).strip() or None
            row["refresh_token_enc"] = None
            row["expires_at"] = exchanged.get("expires_at")
            row["scopes"] = list(exchanged.get("scopes", []))
            row["display_name"] = row.get("display_name") or self._twitch_account_display(acc)
            row["disconnected"] = False
            row["updated_at"] = now_iso
            self._clear_twitch_pending_auth_locked(row)
            self._save_twitch_auth_state_locked(current)
            self._invalidate_twitch_status_cache_locked()

            new_token = str(row.get("token") or "").strip()
            if new_token:
                oauth_val = new_token if new_token.lower().startswith("oauth:") else f"oauth:{new_token}"
                for env_name in self._twitch_token_env_names(acc):
                    os.environ[env_name] = oauth_val

        status_payload = self.get_twitch_status(force_refresh=True)
        account_status = status_payload.get("accounts", {}).get(acc, {})
        return {
            "ok": True,
            "account": acc,
            "flow": auth_flow,
            "pending": False,
            "connected": bool(account_status.get("connected", False)),
            "status": status_payload,
            "detail": "Connected.",
        }

    def twitch_disconnect(
        self,
        account: str,
        *,
        revoke_remote: bool = True,
        include_env_tokens: bool = False,
    ) -> Dict[str, Any]:
        acc = str(account or "").strip().lower()
        if acc not in self._twitch_account_names():
            raise ValueError("account must be one of: bot, broadcaster")

        local_tokens_to_revoke: List[Dict[str, str]] = []
        client_id = ""
        checked_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            state = self._load_twitch_auth_state_locked()
            runtime_config = self._twitch_runtime_config_locked()
            client_id = str(runtime_config.get("client_id", "")).strip()
            accounts = state.setdefault("accounts", {})
            row = accounts.setdefault(acc, {})
            local_token = str(row.get("token") or "").strip()
            local_refresh_token = str(row.get("refresh_token") or "").strip()
            if local_token:
                local_tokens_to_revoke.append({"source": "local_access", "token": local_token})
            if local_refresh_token:
                local_tokens_to_revoke.append({"source": "local_refresh", "token": local_refresh_token})
            if include_env_tokens:
                for env_name in self._twitch_token_env_names(acc):
                    env_token = str(os.getenv(env_name, "")).strip()
                    if env_token:
                        local_tokens_to_revoke.append({"source": f"env:{env_name}", "token": env_token})
            row["token"] = None
            row["token_enc"] = None
            row["refresh_token"] = None
            row["refresh_token_enc"] = None
            row["expires_at"] = None
            row["scopes"] = []
            row["disconnected"] = True
            row["updated_at"] = checked_at
            row["display_name"] = self._twitch_account_display(acc)
            self._clear_twitch_pending_auth_locked(row)
            self._save_twitch_auth_state_locked(state)
            for env_name in self._twitch_token_env_names(acc):
                if env_name in os.environ:
                    os.environ.pop(env_name, None)
            self._invalidate_twitch_status_cache_locked()

        revocation: Dict[str, Any] = {
            "enabled": bool(revoke_remote),
            "attempted": 0,
            "revoked": 0,
            "failed": 0,
            "details": [],
        }
        if not revoke_remote:
            revocation["skipped_reason"] = "disabled"
        else:
            seen_tokens: set[str] = set()
            for item in local_tokens_to_revoke:
                token = self._token_without_prefix(item.get("token"))
                source = str(item.get("source") or "unknown")
                if not token or token in seen_tokens:
                    continue
                seen_tokens.add(token)
                revocation["attempted"] = int(revocation.get("attempted", 0)) + 1
                if not client_id:
                    revocation["failed"] = int(revocation.get("failed", 0)) + 1
                    revocation["details"].append(
                        {
                            "source": source,
                            "ok": False,
                            "error": "CONFIG_MISSING",
                            "detail": "TWITCH_CLIENT_ID is required for token revocation.",
                        }
                    )
                    continue
                result = self._revoke_twitch_token_remote(token=token, client_id=client_id)
                ok = bool(result.get("ok"))
                if ok:
                    revocation["revoked"] = int(revocation.get("revoked", 0)) + 1
                else:
                    revocation["failed"] = int(revocation.get("failed", 0)) + 1
                revocation["details"].append(
                    {
                        "source": source,
                        "ok": ok,
                        "error": result.get("error"),
                        "detail": result.get("detail"),
                    }
                )
            if revocation["attempted"] == 0:
                revocation["skipped_reason"] = "no_local_tokens"

        return {
            "status": self.get_twitch_status(force_refresh=True),
            "revocation": revocation,
        }

    def refresh_twitch_tokens_if_needed(self, *, force: bool = False) -> Dict[str, Any]:
        checked_at = datetime.now(timezone.utc).isoformat()
        enabled = self._twitch_auto_refresh_enabled()
        if not enabled and not force:
            return {
                "ok": True,
                "enabled": False,
                "checked_at": checked_at,
                "refreshed_any": False,
                "accounts": {},
            }

        lead_seconds = self._twitch_refresh_lead_seconds()
        result: Dict[str, Any] = {
            "ok": True,
            "enabled": True,
            "checked_at": checked_at,
            "lead_seconds": lead_seconds,
            "refreshed_any": False,
            "accounts": {},
        }
        changed = False
        now_utc = datetime.now(timezone.utc)
        with self._lock:
            runtime_config = self._twitch_runtime_config_locked()
            state = self._load_twitch_auth_state_locked()
            accounts = state.get("accounts", {}) if isinstance(state, dict) else {}
            if not isinstance(accounts, dict):
                accounts = {}
                state["accounts"] = accounts
            auth_flow = str(runtime_config.get("auth_flow", "authorization_code")).strip() or "authorization_code"
            client_id = str(runtime_config.get("client_id", "")).strip()
            client_secret = str(runtime_config.get("client_secret", "")).strip()
            for account in self._twitch_account_names():
                row = accounts.get(account, {})
                if not isinstance(row, dict):
                    row = {}
                    accounts[account] = row
                token = str(row.get("token") or "").strip()
                refresh_token = str(row.get("refresh_token") or "").strip()
                expires_at_text = str(row.get("expires_at") or "").strip()
                expires_at_dt = _parse_iso(expires_at_text)
                if expires_at_dt is not None and expires_at_dt.tzinfo is None:
                    expires_at_dt = expires_at_dt.replace(tzinfo=timezone.utc)
                seconds_until_expiry = (
                    (expires_at_dt.astimezone(timezone.utc) - now_utc).total_seconds()
                    if expires_at_dt is not None
                    else None
                )
                account_result: Dict[str, Any] = {
                    "account": account,
                    "attempted": False,
                    "refreshed": False,
                    "error": None,
                    "detail": None,
                    "expires_at_before": expires_at_text or None,
                    "seconds_until_expiry": seconds_until_expiry,
                }
                disconnected = bool(row.get("disconnected", False))
                if disconnected:
                    account_result["skip_reason"] = "DISCONNECTED"
                    result["accounts"][account] = account_result
                    continue
                if not refresh_token:
                    account_result["skip_reason"] = "NO_REFRESH_TOKEN"
                    result["accounts"][account] = account_result
                    continue
                if not force:
                    if expires_at_dt is None:
                        account_result["skip_reason"] = "NO_EXPIRY"
                        result["accounts"][account] = account_result
                        continue
                    if seconds_until_expiry is not None and seconds_until_expiry > float(lead_seconds):
                        account_result["skip_reason"] = "NOT_DUE"
                        result["accounts"][account] = account_result
                        continue
                if not token and not self._token_looks_valid(self._twitch_env_value(self._twitch_token_env_names(account))):
                    account_result["skip_reason"] = "NO_TOKEN"
                    result["accounts"][account] = account_result
                    continue
                needs_client_secret = auth_flow == "authorization_code"
                if (not client_id) or (needs_client_secret and not client_secret):
                    account_result["attempted"] = True
                    account_result["error"] = "CONFIG_MISSING"
                    missing = []
                    if not client_id:
                        missing.append("TWITCH_CLIENT_ID")
                    if needs_client_secret and not client_secret:
                        missing.append("TWITCH_CLIENT_SECRET")
                    account_result["detail"] = f"Missing required config: {', '.join(missing)}"
                    result["accounts"][account] = account_result
                    result["ok"] = False
                    continue

                account_result["attempted"] = True
                refreshed = self._refresh_twitch_access_token(
                    refresh_token=refresh_token,
                    client_id=client_id,
                    client_secret=(client_secret if (needs_client_secret and client_secret) else None),
                )
                if not bool(refreshed.get("ok", False)):
                    account_result["error"] = str(refreshed.get("error") or "TOKEN_REFRESH_FAILED")
                    account_result["detail"] = str(refreshed.get("detail") or "Token refresh failed.")
                    result["accounts"][account] = account_result
                    result["ok"] = False
                    os.environ["TWITCH_LAST_ERROR"] = f"{account}:{account_result['error']}"
                    continue

                new_access_token = str(refreshed.get("access_token", "")).strip()
                if not new_access_token:
                    account_result["error"] = "TOKEN_REFRESH_FAILED"
                    account_result["detail"] = "Missing access token in refresh response."
                    result["accounts"][account] = account_result
                    result["ok"] = False
                    continue

                new_refresh_token = str(refreshed.get("refresh_token") or "").strip() or refresh_token
                new_expires_at = str(refreshed.get("expires_at") or "").strip() or None
                new_scopes_raw = refreshed.get("scopes", [])
                new_scopes = (
                    [str(item).strip() for item in new_scopes_raw if str(item).strip()]
                    if isinstance(new_scopes_raw, list)
                    else []
                )
                row["token"] = new_access_token
                row["token_enc"] = None
                row["refresh_token"] = new_refresh_token
                row["refresh_token_enc"] = None
                row["expires_at"] = new_expires_at
                if new_scopes:
                    row["scopes"] = new_scopes
                row["disconnected"] = False
                row["updated_at"] = checked_at
                self._clear_twitch_pending_auth_locked(row)
                oauth_value = (
                    new_access_token
                    if new_access_token.lower().startswith("oauth:")
                    else f"oauth:{new_access_token}"
                )
                for env_name in self._twitch_token_env_names(account):
                    os.environ[env_name] = oauth_value
                if account == "bot" and new_expires_at:
                    os.environ["TWITCH_TOKEN_EXPIRES_AT"] = new_expires_at
                os.environ["TWITCH_LAST_ERROR"] = ""
                account_result["refreshed"] = True
                account_result["expires_at_after"] = new_expires_at
                result["accounts"][account] = account_result
                changed = True
                result["refreshed_any"] = True
            if changed:
                self._save_twitch_auth_state_locked(state)
                self._invalidate_twitch_status_cache_locked()
        return result

    def get_twitch_status(self, *, force_refresh: bool = False) -> Dict[str, Any]:
        checked_at = datetime.now(timezone.utc).isoformat()
        scopes_payload = self._twitch_scopes_payload()
        with self._lock:
            now_monotonic = time.monotonic()
            if (
                not force_refresh
                and isinstance(self._twitch_status_cache, dict)
                and self._twitch_status_cache_expiry_ts > now_monotonic
            ):
                return deepcopy(self._twitch_status_cache)
            state = self._load_twitch_auth_state_locked()
            runtime_config = self._twitch_runtime_config_locked()
            bot_status = self._twitch_account_status_locked(
                "bot",
                state,
                checked_at=checked_at,
                runtime_config=runtime_config,
            )
            broadcaster_status = self._twitch_account_status_locked(
                "broadcaster",
                state,
                checked_at=checked_at,
                runtime_config=runtime_config,
            )
        accounts = {
            "bot": bot_status,
            "broadcaster": broadcaster_status,
        }
        scope_set = set(scopes_payload["scopes"])
        for account_status in accounts.values():
            account_scopes = account_status.get("scopes", [])
            if isinstance(account_scopes, list):
                for scope in account_scopes:
                    text = str(scope or "").strip()
                    if text:
                        scope_set.add(text)
        merged_scopes = sorted(scope_set)
        auth_flow = str(runtime_config.get("auth_flow", "authorization_code")).strip() or "authorization_code"
        missing_fields = list(runtime_config.get("missing_config_fields", []))
        primary_channel = str(runtime_config.get("primary_channel", "")).strip() or None
        encryption_enabled = self._twitch_auth_state_encryption_enabled()
        payload = {
            # Legacy compatibility fields.
            "connected": bool(bot_status.get("connected", False) or broadcaster_status.get("connected", False)),
            "scopes": merged_scopes,
            "scopes_present": {
                "chat:read": ("chat:read" in merged_scopes),
                "chat:edit": ("chat:edit" in merged_scopes),
            },
            "token_expiry": os.getenv("TWITCH_TOKEN_EXPIRES_AT"),
            "last_error": os.getenv("TWITCH_LAST_ERROR"),
            "auth_flow": auth_flow,
            "primary_channel": primary_channel,
            "missing_config_fields": missing_fields,
            "config_ready": len(missing_fields) == 0,
            "auth_state_encryption": {
                "enabled": encryption_enabled,
                "backend": ("dpapi" if encryption_enabled else "plaintext"),
            },
            # D15 truthful account-level status.
            "last_checked_ts": checked_at,
            "accounts": accounts,
        }
        payload["setup"] = self._setup_wizard_status(
            runtime_config=runtime_config,
            accounts=accounts,
        )
        with self._lock:
            self._twitch_status_cache = deepcopy(payload)
            self._twitch_status_cache_expiry_ts = time.monotonic() + self._twitch_status_cache_ttl_seconds()
        return payload

    def get_live_twitch_credentials(self, account: str = "bot") -> Dict[str, Any]:
        if self._twitch_auto_refresh_enabled():
            try:
                self.refresh_twitch_tokens_if_needed(force=False)
            except Exception:
                pass
        acc = str(account or "").strip().lower() or "bot"
        if acc not in self._twitch_account_names():
            return {
                "ok": False,
                "account": acc,
                "error": "INVALID_ACCOUNT",
                "detail": "account must be one of: bot, broadcaster",
            }

        checked_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            runtime_config = self._twitch_runtime_config_locked()
            state = self._load_twitch_auth_state_locked()
            account_status = self._twitch_account_status_locked(
                acc,
                state,
                checked_at=checked_at,
                runtime_config=runtime_config,
            )
            accounts = state.get("accounts", {}) if isinstance(state, dict) else {}
            row = accounts.get(acc, {}) if isinstance(accounts, dict) else {}
            if not isinstance(row, dict):
                row = {}

            disconnected = bool(row.get("disconnected", False))
            local_raw = row.get("token")
            local_token = local_raw.strip() if isinstance(local_raw, str) else ""
            env_token = self._twitch_env_value(self._twitch_token_env_names(acc))
            token = "" if disconnected else (local_token or env_token)
            channel = str(runtime_config.get("primary_channel", "")).strip().lstrip("#").lower()
            if acc == "bot":
                nick = self._twitch_env_value(["TWITCH_BOT_NICK", "TWITCH_NICK"]).strip()
            else:
                nick = self._twitch_env_value(["TWITCH_BROADCASTER_NICK", "TWITCH_NICK"]).strip()
            if not nick:
                nick = str(account_status.get("display_name") or self._twitch_account_display(acc)).strip()

        if not bool(account_status.get("connected", False)):
            reason = str(account_status.get("reason") or "DISCONNECTED").strip() or "DISCONNECTED"
            return {
                "ok": False,
                "account": acc,
                "error": reason,
                "detail": str(account_status.get("reason_detail") or "Twitch account is not connected."),
                "status": account_status,
            }
        if not channel:
            return {
                "ok": False,
                "account": acc,
                "error": "MISSING_PRIMARY_CHANNEL",
                "detail": "Set twitch_config.primary_channel (or TWITCH_CHANNEL).",
                "status": account_status,
            }
        if not nick:
            return {
                "ok": False,
                "account": acc,
                "error": "CONFIG_MISSING",
                "detail": "Missing Twitch nick for live chat runtime.",
                "status": account_status,
            }
        if not self._token_looks_valid(token):
            return {
                "ok": False,
                "account": acc,
                "error": "INVALID_TOKEN",
                "detail": "Twitch token is missing or invalid for live chat runtime.",
                "status": account_status,
            }

        oauth_token = token if str(token).lower().startswith("oauth:") else f"oauth:{token}"
        return {
            "ok": True,
            "account": acc,
            "channel": channel,
            "nick": nick,
            "oauth_token": oauth_token,
            "status": account_status,
            "checked_at": checked_at,
        }

    @staticmethod
    def _event_decision_bucket(event: EventResponse) -> str:
        text = str(event.decision_type or "").strip().lower()
        if text in {"speak", "suppress", "noop"}:
            return text
        if event.suppression_reason:
            return "suppress"
        return "noop"

    @staticmethod
    def _event_ts_dt(event: EventResponse) -> Optional[datetime]:
        return _parse_iso(event.ts)

    def _filter_events(
        self,
        *,
        events: List[EventResponse],
        q: Optional[str] = None,
        decision_type: Optional[str] = None,
        suppression_reason: Optional[str] = None,
        since_ts: Optional[str] = None,
        until_ts: Optional[str] = None,
        suppressed_only: bool = False,
    ) -> List[EventResponse]:
        q_norm = str(q or "").strip().lower()
        decision_norm = str(decision_type or "").strip().lower()
        suppression_norm = str(suppression_reason or "").strip().lower()
        since_dt = _parse_iso(str(since_ts)) if since_ts else None
        until_dt = _parse_iso(str(until_ts)) if until_ts else None

        out: List[EventResponse] = []
        for event in events:
            if suppressed_only and not event.suppression_reason:
                continue
            if q_norm:
                hay = f"{event.user_handle} {event.message_text}".lower()
                if q_norm not in hay:
                    continue
            if decision_norm:
                bucket = self._event_decision_bucket(event)
                if decision_norm not in {"speak", "suppress", "noop"}:
                    continue
                if bucket != decision_norm:
                    continue
            if suppression_norm:
                reason = str(event.suppression_reason or "").strip().lower()
                if reason != suppression_norm:
                    continue
            if since_dt is not None or until_dt is not None:
                evt_dt = self._event_ts_dt(event)
                if evt_dt is None:
                    continue
                if since_dt is not None and evt_dt < since_dt:
                    continue
                if until_dt is not None and evt_dt > until_dt:
                    continue
            out.append(event)
        return out

    def query_events(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        q: Optional[str] = None,
        decision_type: Optional[str] = None,
        decision: Optional[str] = None,
        suppression_reason: Optional[str] = None,
        since_ts: Optional[str] = None,
        until_ts: Optional[str] = None,
        suppressed_only: bool = False,
    ) -> Tuple[List[EventResponse], int]:
        lim = max(1, min(int(limit), 500))
        off = max(0, int(offset))
        runs = self._load_recent_runs()
        all_events: List[EventResponse] = []
        for run_data in runs:
            all_events.extend(self._events_from_run(run_data))

        def _sort_key(evt: EventResponse) -> float:
            dt = self._event_ts_dt(evt)
            return dt.timestamp() if dt is not None else 0.0

        all_events.sort(key=_sort_key, reverse=True)
        filtered = self._filter_events(
            events=all_events,
            q=q,
            decision_type=(decision_type or decision),
            suppression_reason=suppression_reason,
            since_ts=since_ts,
            until_ts=until_ts,
            suppressed_only=suppressed_only,
        )
        total_count = len(filtered)
        return filtered[off : off + lim], total_count

    def get_events(self, limit: int = 5) -> List[EventResponse]:
        items, _ = self.query_events(limit=limit, offset=0)
        return items

    def get_suppressions(self, limit: int = 5) -> List[EventResponse]:
        items, _ = self.query_events(limit=limit, offset=0, suppressed_only=True)
        return items

    def _read_all_operator_log_records(self) -> List[OperatorLogResponse]:
        self._apply_retention_policy()
        if not self._audit_log_path.exists():
            return []
        records: List[OperatorLogResponse] = []
        try:
            lines = self._audit_log_path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            return []
        for line in lines:
            raw = line.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            actor_raw = obj.get("actor")
            username_raw = obj.get("username")
            role_raw = obj.get("role")
            auth_mode_raw = obj.get("auth_mode")
            actor_text = str(actor_raw).strip().lower() if actor_raw is not None else ""
            username_text = str(username_raw).strip().lower() if username_raw is not None else ""
            if username_text:
                actor_text = username_text
            if not actor_text:
                actor_text = self.normalize_actor(actor_raw)
            auth_mode_text = str(auth_mode_raw).strip().lower() if auth_mode_raw is not None else None
            if auth_mode_text not in {None, "session", "legacy_key"}:
                auth_mode_text = None
            records.append(
                OperatorLogResponse(
                    ts=str(obj.get("ts", "")),
                    operator=str(obj.get("operator", "Operator")),
                    action=str(obj.get("action", "")),
                    payload_summary=(str(obj["payload_summary"]) if obj.get("payload_summary") is not None else None),
                    result=(str(obj["result"]) if obj.get("result") is not None else None),
                    actor=actor_text,
                    username=(username_text or None),
                    role=(self.normalize_role(role_raw) if role_raw is not None else None),
                    auth_mode=auth_mode_text,
                )
            )
        records.reverse()
        return records

    def query_operator_log(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        actor: Optional[str] = None,
        action: Optional[str] = None,
        since_ts: Optional[str] = None,
        until_ts: Optional[str] = None,
    ) -> Tuple[List[OperatorLogResponse], int]:
        lim = max(1, min(int(limit), 500))
        off = max(0, int(offset))
        actor_norm = self.normalize_actor(actor) if actor is not None and str(actor).strip() else ""
        action_norm = str(action or "").strip().lower()
        since_dt = _parse_iso(str(since_ts)) if since_ts else None
        until_dt = _parse_iso(str(until_ts)) if until_ts else None

        records = self._read_all_operator_log_records()
        filtered: List[OperatorLogResponse] = []
        for rec in records:
            if actor_norm and rec.actor != actor_norm:
                continue
            if action_norm and action_norm not in rec.action.lower():
                continue
            if since_dt is not None or until_dt is not None:
                rec_dt = _parse_iso(rec.ts)
                if rec_dt is None:
                    continue
                if since_dt is not None and rec_dt < since_dt:
                    continue
                if until_dt is not None and rec_dt > until_dt:
                    continue
            filtered.append(rec)

        total_count = len(filtered)
        return filtered[off : off + lim], total_count

    def get_operator_log(self, limit: int = 5) -> List[OperatorLogResponse]:
        items, _ = self.query_operator_log(limit=limit, offset=0)
        return items

    @staticmethod
    def event_to_ui_text(event: EventResponse) -> str:
        msg = event.message_text.strip()
        decision = (event.final_text or event.decision or "").strip()
        if msg and decision:
            return f"{msg} -> {decision}"
        if msg:
            return msg
        return decision or ""

    @staticmethod
    def event_time(event: EventResponse) -> str:
        return _hms(event.ts)
