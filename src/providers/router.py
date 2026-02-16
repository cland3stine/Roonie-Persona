from __future__ import annotations

import json
import os
import re
import threading
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from providers.base import Provider
from providers.registry import ProviderRegistry

_SUPPORTED_PROVIDERS = ("openai", "grok", "anthropic")
_DEFAULT_APPROVED_PROVIDERS = ("openai", "grok")
_ROUTING_OVERRIDE_MODES = ("default", "force_openai", "force_grok")
_ROUTING_MUSIC_KEYWORDS = (
    "track",
    "id",
    "rekordbox",
    "bpm",
    "key",
    "producer",
    "dj",
    "label",
    "remix",
    "mix",
    "release",
    "set",
    "tune",
)
_ARTIST_TITLE_PATTERN = re.compile(r"\b[\w][\w .&'â€™]{1,80}\s-\s[\w][\w .&'â€™]{1,120}\b")
_ARTIST_TITLE_HINT_PATTERN = re.compile(r"\b(track|id|song|tune|mix|remix)\b")
_MODERATION_BLOCK_PHRASES = (
    "phone number",
    "home address",
    "dox",
    "doxx",
    "suicidal",
    "kill myself",
)
try:
    _NY_TZ = ZoneInfo("America/New_York")
except ZoneInfoNotFoundError:
    # Fallback for environments without IANA tzdata installed.
    _NY_TZ = timezone(timedelta(hours=-5))
_CFG_LOCK = threading.Lock()
_CFG_CACHE: Optional[Dict[str, Any]] = None
_CFG_CACHE_MTIME_NS: Optional[int] = None
_CFG_CACHE_PATH: Optional[str] = None
_ROUTING_CFG_LOCK = threading.Lock()
_ROUTING_CFG_CACHE: Optional[Dict[str, Any]] = None
_ROUTING_CFG_CACHE_MTIME_NS: Optional[int] = None
_ROUTING_CFG_CACHE_PATH: Optional[str] = None
_METRICS_LOCK = threading.Lock()
_REAL_PROVIDER_TRANSPORT: Any = None
_REAL_PROVIDER_TRANSPORT_LOCK = threading.Lock()
_SECRETS_CACHE_LOCK = threading.Lock()
_SECRETS_CACHE: Optional[Dict[str, str]] = None
_SECRETS_CACHE_MTIME_NS: Optional[int] = None
_SECRETS_CACHE_PATH: Optional[str] = None


def _new_provider_metrics() -> Dict[str, Any]:
    return {
        "requests": 0,
        "success": 0,
        "failures": 0,
        "moderation_blocks": 0,
        "avg_latency_ms": 0,
        "last_error": None,
    }


def _default_runtime_metrics() -> Dict[str, Any]:
    providers: Dict[str, Dict[str, Any]] = {}
    for name in _SUPPORTED_PROVIDERS:
        providers[name] = _new_provider_metrics()
    return {
        "providers": providers,
        "routing": {
            "music_culture_hits": 0,
            "general_hits": 0,
            "override_hits": 0,
        },
        "last_error": None,
    }


_RUNTIME_METRICS: Dict[str, Any] = _default_runtime_metrics()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _providers_config_path() -> Path:
    configured = (os.getenv("ROONIE_PROVIDERS_CONFIG_PATH") or "").strip()
    if configured:
        return Path(configured)
    dashboard_data_dir = (os.getenv("ROONIE_DASHBOARD_DATA_DIR") or "").strip()
    if dashboard_data_dir:
        return Path(dashboard_data_dir) / "providers_config.json"
    return _repo_root() / "data" / "providers_config.json"


def _routing_config_path() -> Path:
    configured = (os.getenv("ROONIE_ROUTING_CONFIG_PATH") or "").strip()
    if configured:
        return Path(configured)
    dashboard_data_dir = (os.getenv("ROONIE_DASHBOARD_DATA_DIR") or "").strip()
    if dashboard_data_dir:
        return Path(dashboard_data_dir) / "routing_config.json"
    return _repo_root() / "data" / "routing_config.json"


def get_runtime_config_paths() -> Dict[str, Path]:
    return {
        "providers_config": _providers_config_path(),
        "routing_config": _routing_config_path(),
    }


def _today_ny() -> str:
    return datetime.now(_NY_TZ).date().isoformat()


def _default_providers_config() -> Dict[str, Any]:
    return {
        "version": 1,
        "active_provider": "openai",
        "approved_providers": list(_DEFAULT_APPROVED_PROVIDERS),
        "caps": {
            "daily_requests_max": 500,
            "daily_tokens_max": 0,
            "hard_stop_on_cap": True,
        },
        "usage": {
            "day": _today_ny(),
            "requests": 0,
            "tokens": 0,
        },
    }


def _default_routing_config() -> Dict[str, Any]:
    return {
        "version": 1,
        "enabled": True,
        "default_provider": "openai",
        "music_route_provider": "grok",
        "moderation_provider": "openai",
        "manual_override": "default",
        "classification_rules": {
            "music_culture_keywords": list(_ROUTING_MUSIC_KEYWORDS),
            "artist_title_pattern": True,
        },
    }


def _normalize_non_negative_int(value: Any, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return max(0, int(default))


def _normalize_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    base = _default_providers_config()
    out = deepcopy(base)
    if not isinstance(raw, dict):
        return out

    active = str(raw.get("active_provider", out["active_provider"])).strip().lower()
    approved_raw = raw.get("approved_providers", out["approved_providers"])
    approved: list[str] = []
    if isinstance(approved_raw, list):
        for item in approved_raw:
            name = str(item).strip().lower()
            if name in _SUPPORTED_PROVIDERS and name not in approved:
                approved.append(name)
    if not approved:
        approved = list(_DEFAULT_APPROVED_PROVIDERS)
    if active not in approved:
        active = approved[0]
    out["active_provider"] = active
    out["approved_providers"] = approved

    caps_raw = raw.get("caps", {})
    if not isinstance(caps_raw, dict):
        caps_raw = {}
    out["caps"] = {
        "daily_requests_max": _normalize_non_negative_int(
            caps_raw.get("daily_requests_max"), base["caps"]["daily_requests_max"]
        ),
        "daily_tokens_max": _normalize_non_negative_int(
            caps_raw.get("daily_tokens_max"), base["caps"]["daily_tokens_max"]
        ),
        "hard_stop_on_cap": bool(caps_raw.get("hard_stop_on_cap", base["caps"]["hard_stop_on_cap"])),
    }

    usage_raw = raw.get("usage", {})
    if not isinstance(usage_raw, dict):
        usage_raw = {}
    day = str(usage_raw.get("day", "")).strip() or _today_ny()
    out["usage"] = {
        "day": day,
        "requests": _normalize_non_negative_int(usage_raw.get("requests"), 0),
        "tokens": _normalize_non_negative_int(usage_raw.get("tokens"), 0),
    }
    return out


def _normalize_routing_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    base = _default_routing_config()
    out = deepcopy(base)
    if not isinstance(raw, dict):
        return out

    out["enabled"] = bool(raw.get("enabled", base["enabled"]))
    default_provider = str(raw.get("default_provider", base["default_provider"])).strip().lower()
    if default_provider not in _SUPPORTED_PROVIDERS:
        default_provider = base["default_provider"]
    out["default_provider"] = default_provider

    music_provider = str(raw.get("music_route_provider", base["music_route_provider"])).strip().lower()
    if music_provider not in _SUPPORTED_PROVIDERS:
        music_provider = base["music_route_provider"]
    out["music_route_provider"] = music_provider

    moderation_provider = str(raw.get("moderation_provider", base["moderation_provider"])).strip().lower()
    if moderation_provider != "openai":
        moderation_provider = "openai"
    out["moderation_provider"] = moderation_provider

    manual_override = str(raw.get("manual_override", base["manual_override"])).strip().lower()
    if manual_override not in _ROUTING_OVERRIDE_MODES:
        manual_override = "default"
    out["manual_override"] = manual_override
    # Routing rules are fixed in v1 to avoid drift.
    out["classification_rules"] = deepcopy(base["classification_rules"])
    return out


def _write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def _read_or_seed_config_locked(path: Path) -> Dict[str, Any]:
    cfg = _default_providers_config()
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            cfg = _normalize_config(raw if isinstance(raw, dict) else {})
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            cfg = _default_providers_config()
    _roll_usage_day_inplace(cfg)
    _write_json_atomic(path, cfg)
    return cfg


def _read_or_seed_routing_config_locked(path: Path) -> Dict[str, Any]:
    cfg = _default_routing_config()
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            cfg = _normalize_routing_config(raw if isinstance(raw, dict) else {})
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            cfg = _default_routing_config()
    _write_json_atomic(path, cfg)
    return cfg


def _load_config_locked(path: Path) -> Dict[str, Any]:
    global _CFG_CACHE, _CFG_CACHE_MTIME_NS, _CFG_CACHE_PATH
    path_key = str(path.resolve())
    try:
        mtime_ns = path.stat().st_mtime_ns if path.exists() else None
    except OSError:
        mtime_ns = None
    if _CFG_CACHE is not None and _CFG_CACHE_MTIME_NS == mtime_ns and _CFG_CACHE_PATH == path_key:
        return deepcopy(_CFG_CACHE)
    cfg = _read_or_seed_config_locked(path)
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        mtime_ns = None
    _CFG_CACHE = deepcopy(cfg)
    _CFG_CACHE_MTIME_NS = mtime_ns
    _CFG_CACHE_PATH = path_key
    return cfg


def _load_routing_config_locked(path: Path) -> Dict[str, Any]:
    global _ROUTING_CFG_CACHE, _ROUTING_CFG_CACHE_MTIME_NS, _ROUTING_CFG_CACHE_PATH
    path_key = str(path.resolve())
    try:
        mtime_ns = path.stat().st_mtime_ns if path.exists() else None
    except OSError:
        mtime_ns = None
    if (
        _ROUTING_CFG_CACHE is not None
        and _ROUTING_CFG_CACHE_MTIME_NS == mtime_ns
        and _ROUTING_CFG_CACHE_PATH == path_key
    ):
        return deepcopy(_ROUTING_CFG_CACHE)
    cfg = _read_or_seed_routing_config_locked(path)
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        mtime_ns = None
    _ROUTING_CFG_CACHE = deepcopy(cfg)
    _ROUTING_CFG_CACHE_MTIME_NS = mtime_ns
    _ROUTING_CFG_CACHE_PATH = path_key
    return cfg


def _save_config_locked(path: Path, cfg: Dict[str, Any]) -> Dict[str, Any]:
    global _CFG_CACHE, _CFG_CACHE_MTIME_NS, _CFG_CACHE_PATH
    path_key = str(path.resolve())
    normalized = _normalize_config(cfg)
    _roll_usage_day_inplace(normalized)
    _write_json_atomic(path, normalized)
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        mtime_ns = None
    _CFG_CACHE = deepcopy(normalized)
    _CFG_CACHE_MTIME_NS = mtime_ns
    _CFG_CACHE_PATH = path_key
    return deepcopy(normalized)


def _save_routing_config_locked(path: Path, cfg: Dict[str, Any]) -> Dict[str, Any]:
    global _ROUTING_CFG_CACHE, _ROUTING_CFG_CACHE_MTIME_NS, _ROUTING_CFG_CACHE_PATH
    path_key = str(path.resolve())
    normalized = _normalize_routing_config(cfg)
    _write_json_atomic(path, normalized)
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        mtime_ns = None
    _ROUTING_CFG_CACHE = deepcopy(normalized)
    _ROUTING_CFG_CACHE_MTIME_NS = mtime_ns
    _ROUTING_CFG_CACHE_PATH = path_key
    return deepcopy(normalized)


def _roll_usage_day_inplace(cfg: Dict[str, Any]) -> None:
    usage = cfg.get("usage", {})
    if not isinstance(usage, dict):
        usage = {}
        cfg["usage"] = usage
    today = _today_ny()
    if str(usage.get("day", "")).strip() != today:
        usage["day"] = today
        usage["requests"] = 0
        usage["tokens"] = 0


def _is_cost_cap_blocked(cfg: Dict[str, Any]) -> bool:
    caps = cfg.get("caps", {})
    usage = cfg.get("usage", {})
    if not isinstance(caps, dict) or not isinstance(usage, dict):
        return False
    if not bool(caps.get("hard_stop_on_cap", True)):
        return False
    req_max = _normalize_non_negative_int(caps.get("daily_requests_max"), 0)
    tok_max = _normalize_non_negative_int(caps.get("daily_tokens_max"), 0)
    req_used = _normalize_non_negative_int(usage.get("requests"), 0)
    tok_used = _normalize_non_negative_int(usage.get("tokens"), 0)
    if req_max > 0 and req_used >= req_max:
        return True
    if tok_max > 0 and tok_used >= tok_max:
        return True
    return False


def get_provider_runtime_status() -> Dict[str, Any]:
    path = _providers_config_path()
    with _CFG_LOCK:
        cfg = _load_config_locked(path)
        # Persist day rollover deterministically.
        cfg = _save_config_locked(path, cfg)
    return {
        "active_provider": cfg["active_provider"],
        "approved_providers": list(cfg["approved_providers"]),
        "caps": deepcopy(cfg["caps"]),
        "usage": deepcopy(cfg["usage"]),
        "cost_cap_blocked": _is_cost_cap_blocked(cfg),
    }


def get_routing_runtime_status() -> Dict[str, Any]:
    path = _routing_config_path()
    with _ROUTING_CFG_LOCK:
        cfg = _load_routing_config_locked(path)
        cfg = _save_routing_config_locked(path, cfg)
    return deepcopy(cfg)


def set_provider_active(provider: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    requested = str(provider or "").strip().lower()
    path = _providers_config_path()
    with _CFG_LOCK:
        cfg = _load_config_locked(path)
        approved = list(cfg.get("approved_providers", []))
        if requested not in approved:
            raise ValueError("provider must be in approved_providers.")
        old = deepcopy(cfg)
        cfg["active_provider"] = requested
        new = _save_config_locked(path, cfg)
    return old, new


def update_routing_runtime_controls(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("routing payload must be an object.")
    allowed = {"enabled", "manual_override"}
    invalid = [key for key in payload.keys() if key not in allowed]
    if invalid:
        raise ValueError(f"unsupported routing fields: {', '.join(sorted(invalid))}")
    if not payload:
        raise ValueError("No supported routing fields provided.")

    path = _routing_config_path()
    with _ROUTING_CFG_LOCK:
        cfg = _load_routing_config_locked(path)
        old = deepcopy(cfg)
        if "enabled" in payload:
            cfg["enabled"] = bool(payload.get("enabled"))
        if "manual_override" in payload:
            mode = str(payload.get("manual_override", "")).strip().lower()
            if mode not in _ROUTING_OVERRIDE_MODES:
                raise ValueError("manual_override must be one of: default, force_openai, force_grok.")
            cfg["manual_override"] = mode
        new = _save_routing_config_locked(path, cfg)
    return old, new


def update_provider_caps(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("caps payload must be an object.")
    allowed = {"daily_requests_max", "daily_tokens_max", "hard_stop_on_cap"}
    invalid = [key for key in payload.keys() if key not in allowed]
    if invalid:
        raise ValueError(f"unsupported cap fields: {', '.join(sorted(invalid))}")

    path = _providers_config_path()
    with _CFG_LOCK:
        cfg = _load_config_locked(path)
        old = deepcopy(cfg)
        caps = dict(cfg.get("caps", {}))
        if "daily_requests_max" in payload:
            caps["daily_requests_max"] = _normalize_non_negative_int(payload.get("daily_requests_max"), 0)
        if "daily_tokens_max" in payload:
            caps["daily_tokens_max"] = _normalize_non_negative_int(payload.get("daily_tokens_max"), 0)
        if "hard_stop_on_cap" in payload:
            caps["hard_stop_on_cap"] = bool(payload.get("hard_stop_on_cap"))
        cfg["caps"] = caps
        new = _save_config_locked(path, cfg)
    return old, new


def _increment_usage_request() -> Dict[str, Any]:
    path = _providers_config_path()
    with _CFG_LOCK:
        cfg = _load_config_locked(path)
        usage = dict(cfg.get("usage", {}))
        usage["requests"] = _normalize_non_negative_int(usage.get("requests"), 0) + 1
        cfg["usage"] = usage
        return _save_config_locked(path, cfg)


def _ensure_provider_metrics_locked(provider_name: str) -> Dict[str, Any]:
    providers = _RUNTIME_METRICS.setdefault("providers", {})
    if not isinstance(providers, dict):
        providers = {}
        _RUNTIME_METRICS["providers"] = providers
    key = str(provider_name or "").strip().lower()
    if not key:
        key = "openai"
    metric = providers.get(key)
    if not isinstance(metric, dict):
        metric = _new_provider_metrics()
        providers[key] = metric
    return metric


def _record_provider_request_start(provider_name: str) -> None:
    with _METRICS_LOCK:
        metric = _ensure_provider_metrics_locked(provider_name)
        metric["requests"] = int(metric.get("requests", 0)) + 1


def _record_provider_result(
    provider_name: str,
    *,
    latency_ms: int,
    success: bool,
    error: Optional[str] = None,
) -> None:
    with _METRICS_LOCK:
        metric = _ensure_provider_metrics_locked(provider_name)
        if success:
            metric["success"] = int(metric.get("success", 0)) + 1
            metric["last_error"] = None
        else:
            metric["failures"] = int(metric.get("failures", 0)) + 1
            err_text = str(error or "").strip() or "provider_error"
            metric["last_error"] = err_text
            _RUNTIME_METRICS["last_error"] = err_text

        completed = int(metric.get("success", 0)) + int(metric.get("failures", 0))
        latency_prev = int(metric.get("avg_latency_ms", 0))
        if completed <= 1:
            metric["avg_latency_ms"] = max(0, int(latency_ms))
        else:
            total_before = latency_prev * (completed - 1)
            metric["avg_latency_ms"] = max(0, int((total_before + max(0, int(latency_ms))) / completed))


def _record_moderation_block(provider_name: str) -> None:
    with _METRICS_LOCK:
        metric = _ensure_provider_metrics_locked(provider_name)
        metric["moderation_blocks"] = int(metric.get("moderation_blocks", 0)) + 1


def _record_routing_hit(routing_class: str, override_mode: str) -> None:
    with _METRICS_LOCK:
        routing = _RUNTIME_METRICS.setdefault("routing", {})
        if not isinstance(routing, dict):
            routing = {}
            _RUNTIME_METRICS["routing"] = routing
        if str(routing_class).strip().lower() == "music_culture":
            routing["music_culture_hits"] = int(routing.get("music_culture_hits", 0)) + 1
        else:
            routing["general_hits"] = int(routing.get("general_hits", 0)) + 1
        if str(override_mode).strip().lower() != "default":
            routing["override_hits"] = int(routing.get("override_hits", 0)) + 1


def get_provider_runtime_metrics() -> Dict[str, Any]:
    with _METRICS_LOCK:
        providers_src = _RUNTIME_METRICS.get("providers", {})
        routing_src = _RUNTIME_METRICS.get("routing", {})
        providers_out: Dict[str, Dict[str, Any]] = {}
        if isinstance(providers_src, dict):
            for name in _SUPPORTED_PROVIDERS:
                item = providers_src.get(name, {})
                if not isinstance(item, dict):
                    item = {}
                providers_out[name] = {
                    "requests": int(item.get("requests", 0)),
                    "success": int(item.get("success", 0)),
                    "failures": int(item.get("failures", 0)),
                    "moderation_blocks": int(item.get("moderation_blocks", 0)),
                    "avg_latency_ms": int(item.get("avg_latency_ms", 0)),
                    "last_error": (str(item.get("last_error")).strip() if item.get("last_error") is not None else None),
                }
        routing_out = {
            "music_culture_hits": int(routing_src.get("music_culture_hits", 0)) if isinstance(routing_src, dict) else 0,
            "general_hits": int(routing_src.get("general_hits", 0)) if isinstance(routing_src, dict) else 0,
            "override_hits": int(routing_src.get("override_hits", 0)) if isinstance(routing_src, dict) else 0,
        }
        last_error = _RUNTIME_METRICS.get("last_error")
        return {
            "providers": providers_out,
            "routing": routing_out,
            "last_error": (str(last_error).strip() if last_error is not None else None),
        }


def reset_provider_runtime_metrics_for_tests() -> None:
    global _RUNTIME_METRICS
    with _METRICS_LOCK:
        _RUNTIME_METRICS = _default_runtime_metrics()


def classify_request(text: str, category: Optional[str], utility_source: Optional[str]) -> str:
    utility = str(utility_source or "").strip().lower()
    if utility in {"library_index", "utility_track_id"}:
        return "music_culture"

    category_norm = str(category or "").strip().lower()
    if category_norm in {"utility_track_id", "utility_library"}:
        return "music_culture"

    normalized = " ".join(str(text or "").strip().lower().split())
    if not normalized:
        return "general"

    for keyword in _ROUTING_MUSIC_KEYWORDS:
        if re.search(rf"\b{re.escape(keyword)}\b", normalized):
            return "music_culture"

    if _ARTIST_TITLE_PATTERN.search(normalized) and _ARTIST_TITLE_HINT_PATTERN.search(normalized):
        return "music_culture"
    return "general"


def _mk_openai_provider(enabled: bool) -> Provider:
    class _OpenAIStub(Provider):
        def generate(self, *, prompt: str, context: Dict[str, Any]) -> Optional[str]:
            return f"[openai stub] {prompt}"

    return _OpenAIStub(name="openai", enabled=enabled)


def _truthy_env(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def _read_secrets_env() -> Dict[str, str]:
    global _SECRETS_CACHE, _SECRETS_CACHE_MTIME_NS, _SECRETS_CACHE_PATH
    path = (_repo_root() / "config" / "secrets.env").resolve()
    try:
        stat = path.stat()
        mtime_ns = int(stat.st_mtime_ns)
    except OSError:
        return {}
    path_str = str(path)
    with _SECRETS_CACHE_LOCK:
        if (
            _SECRETS_CACHE is not None
            and _SECRETS_CACHE_PATH == path_str
            and _SECRETS_CACHE_MTIME_NS == mtime_ns
        ):
            return dict(_SECRETS_CACHE)
        parsed: Dict[str, str] = {}
        try:
            for raw_line in path.read_text(encoding="utf-8").splitlines():
                line = str(raw_line or "").strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if len(value) >= 2 and (
                    (value[0] == '"' and value[-1] == '"')
                    or (value[0] == "'" and value[-1] == "'")
                ):
                    value = value[1:-1]
                if key:
                    parsed[key] = value
        except OSError:
            parsed = {}
        _SECRETS_CACHE = dict(parsed)
        _SECRETS_CACHE_MTIME_NS = mtime_ns
        _SECRETS_CACHE_PATH = path_str
        return parsed


def _resolve_secret_or_env(name: str) -> str:
    direct = str(os.getenv(name, "")).strip()
    if direct:
        return direct
    return str(_read_secrets_env().get(name, "")).strip()


def _provider_api_key(provider_name: str) -> str:
    selected = str(provider_name or "").strip().lower()
    if selected == "openai":
        return _resolve_secret_or_env("OPENAI_API_KEY")
    if selected == "grok":
        return _resolve_secret_or_env("GROK_API_KEY") or _resolve_secret_or_env("XAI_API_KEY")
    if selected == "anthropic":
        return _resolve_secret_or_env("ANTHROPIC_API_KEY")
    return ""


def _real_provider_network_enabled(context: Optional[Dict[str, Any]]) -> bool:
    if context and "allow_live_provider_network" in context:
        return bool(context.get("allow_live_provider_network"))
    return _truthy_env("ROONIE_ENABLE_LIVE_PROVIDER_NETWORK", False)


def _real_provider_transport():
    global _REAL_PROVIDER_TRANSPORT
    with _REAL_PROVIDER_TRANSPORT_LOCK:
        if _REAL_PROVIDER_TRANSPORT is not None:
            return _REAL_PROVIDER_TRANSPORT
        from roonie.network.transports_urllib import UrllibJsonTransport

        _REAL_PROVIDER_TRANSPORT = UrllibJsonTransport(
            user_agent="roonie-control-room/1.0",
            timeout_seconds=12,
        )
        return _REAL_PROVIDER_TRANSPORT


def _provider_for_name(
    name: str,
    registry_default: Provider,
    *,
    context: Optional[Dict[str, Any]] = None,
) -> Provider:
    selected = str(name or "").strip().lower()
    if selected == "openai":
        if _real_provider_network_enabled(context):
            api_key = _provider_api_key("openai")
            if api_key:
                from providers.openai_real import OpenAIProvider

                if context is not None and not context.get("model"):
                    context["model"] = str(os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
                return OpenAIProvider(
                    enabled=True,
                    transport=_real_provider_transport(),
                    api_key=api_key,
                )
        return _mk_openai_provider(enabled=True)
    if selected == "grok":
        if _real_provider_network_enabled(context):
            api_key = _provider_api_key("grok")
            if api_key:
                from providers.grok_real import GrokProvider

                if context is not None and not context.get("model"):
                    context["model"] = str(
                        os.getenv("GROK_MODEL", "grok-4-1-fast-non-reasoning")
                    )
                return GrokProvider(
                    enabled=True,
                    transport=_real_provider_transport(),
                    api_key=api_key,
                )
        return _mk_shadow_provider("grok", enabled=True)
    if selected == "anthropic":
        if _real_provider_network_enabled(context):
            api_key = _provider_api_key("anthropic")
            if api_key:
                from providers.anthropic_real import AnthropicProvider

                if context is not None and not context.get("model"):
                    context["model"] = str(os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet"))
                return AnthropicProvider(
                    enabled=True,
                    transport=_real_provider_transport(),
                    api_key=api_key,
                )
        return _mk_shadow_provider(selected, enabled=True)
    return registry_default


def _select_provider_from_routing(
    *,
    routing_cfg: Dict[str, Any],
    fallback_provider: str,
    routing_class: str,
) -> str:
    # Canon hard-stop: if routing is OFF, never call Grok.
    if not bool(routing_cfg.get("enabled", False)):
        return "openai"
    override_mode = str(routing_cfg.get("manual_override", "default")).strip().lower()
    if override_mode == "force_openai":
        return "openai"
    if override_mode == "force_grok":
        return "grok"
    if bool(routing_cfg.get("enabled", False)) and routing_class == "music_culture":
        return str(routing_cfg.get("music_route_provider", "grok")).strip().lower() or "grok"
    return str(fallback_provider).strip().lower() or "openai"


def _moderation_allows_grok_output(
    *,
    text: str,
    context: Dict[str, Any],
    test_overrides: Optional[Dict[str, Any]],
) -> bool:
    if test_overrides and "moderation_behavior" in test_overrides:
        mode = str(test_overrides.get("moderation_behavior", "")).strip().lower()
        if mode == "block":
            return False
        if mode == "allow":
            return True

    forced = str((context or {}).get("force_moderation_result", "")).strip().lower()
    if forced == "block":
        return False
    if forced == "allow":
        return True

    normalized = str(text or "").strip().lower()
    if not normalized:
        return True
    return not any(phrase in normalized for phrase in _MODERATION_BLOCK_PHRASES)


def _mk_shadow_provider(name: str, enabled: bool) -> Provider:
    """
    Deterministic shadow provider stubs for Phase 10F.
    Shadow outputs are intentionally ignored; execution is for validation only.
    """
    name = name.strip().lower()
    if name == "anthropic":
        class _AnthropicStub(Provider):
            def generate(self, *, prompt: str, context: Dict[str, Any]) -> Optional[str]:
                # deterministic, but will never be returned by router
                return f"[anthropic stub] {prompt}"
        return _AnthropicStub(name="anthropic", enabled=enabled)
    if name == "grok":
        class _GrokStub(Provider):
            def generate(self, *, prompt: str, context: Dict[str, Any]) -> Optional[str]:
                return f"[grok stub] {prompt}"
        return _GrokStub(name="grok", enabled=enabled)
    return Provider(name=name, enabled=enabled)


def route_generate(
    *,
    registry: ProviderRegistry,
    routing_cfg: Dict[str, Any],
    prompt: str,
    context: Dict[str, Any],
    test_overrides: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """
    Phase 10F: Deterministic routing + failure containment.

    Rules:
      - Primary result is returned (or None). Shadow never changes return value.
      - Shadow execution is optional and best-effort; failures are contained.
      - If primary throws, return None (do not crash).
    """
    if context is None:
        context = {}

    # ---- Primary ----
    primary = registry.get_default()
    active_provider_name = primary.name
    routing_runtime_cfg: Optional[Dict[str, Any]] = None
    provider_runtime_status: Optional[Dict[str, Any]] = None
    routing_class = "general"
    override_mode = "default"
    use_provider_config = bool((context or {}).get("use_provider_config", False))
    if use_provider_config:
        provider_runtime_status = get_provider_runtime_status()
        routing_runtime_cfg = get_routing_runtime_status()
        approved = set(str(item).strip().lower() for item in provider_runtime_status.get("approved_providers", []))
        active_from_provider_cfg = str(provider_runtime_status.get("active_provider", "")).strip().lower()
        default_provider = active_from_provider_cfg or str(
            routing_runtime_cfg.get("default_provider", active_provider_name)
        ).strip().lower()
        routing_class = classify_request(
            str(context.get("message_text", "")),
            str(context.get("category", "")),
            str(context.get("utility_source", "")),
        )
        override_mode = str(routing_runtime_cfg.get("manual_override", "default")).strip().lower()
        active_provider_name = _select_provider_from_routing(
            routing_cfg=routing_runtime_cfg,
            fallback_provider=default_provider,
            routing_class=routing_class,
        )
        if active_provider_name not in approved:
            active_provider_name = default_provider
        if active_provider_name not in approved:
            active_provider_name = primary.name

        context["routing_enabled"] = bool(routing_runtime_cfg.get("enabled", False))
        context["routing_class"] = routing_class
        context["provider_selected"] = active_provider_name
        context["moderation_provider_used"] = "openai" if active_provider_name == "grok" else None
        context["moderation_result"] = "not_applicable"
        context["override_mode"] = override_mode

        _record_routing_hit(routing_class, override_mode)
        primary = _provider_for_name(active_provider_name, primary, context=context)
        if _is_cost_cap_blocked(
            {
                "caps": provider_runtime_status.get("caps", {}),
                "usage": provider_runtime_status.get("usage", {}),
            }
        ):
            context["suppression_reason"] = "COST_CAP"
            context["provider_block_reason"] = "COST_CAP"
            context["active_provider"] = active_provider_name
            os.environ["ROONIE_ACTIVE_PROVIDER"] = active_provider_name
            return None
        _increment_usage_request()

    provider_name = str(primary.name or "").strip().lower()
    provider_call_tracked = provider_name in set(_SUPPORTED_PROVIDERS)
    start_monotonic = time.monotonic() if provider_call_tracked else None
    result_recorded = False
    if provider_call_tracked:
        _record_provider_request_start(provider_name)

    try:
        if test_overrides and test_overrides.get("primary_behavior") == "throw":
            raise RuntimeError("primary forced throw (test override)")
        out = primary.generate(prompt=prompt, context=context)
    except Exception as exc:
        context["suppression_reason"] = "PROVIDER_ERROR"
        context["provider_block_reason"] = "PROVIDER_ERROR"
        if provider_call_tracked and start_monotonic is not None:
            latency_ms = int((time.monotonic() - start_monotonic) * 1000)
            _record_provider_result(
                provider_name,
                latency_ms=latency_ms,
                success=False,
                error=str(exc),
            )
            result_recorded = True
        out = None

    if provider_call_tracked and start_monotonic is not None and not result_recorded:
        latency_ms = int((time.monotonic() - start_monotonic) * 1000)
        success = out is not None and primary.enabled and primary.name != "none"
        _record_provider_result(
            provider_name,
            latency_ms=latency_ms,
            success=success,
            error=(None if success else "empty_response"),
        )

    # If primary is "none" or disabled, treat as silent regardless of generate().
    if (primary.name == "none") or (not primary.enabled):
        out = None
    context["active_provider"] = primary.name
    os.environ["ROONIE_ACTIVE_PROVIDER"] = primary.name

    if use_provider_config and primary.name == "grok":
        context["moderation_provider_used"] = "openai"
        if out is None:
            context["moderation_result"] = "allow"
        else:
            allowed = _moderation_allows_grok_output(
                text=str(out),
                context=context,
                test_overrides=test_overrides,
            )
            if not allowed:
                context["moderation_result"] = "block"
                context["suppression_reason"] = "MODERATION_BLOCK"
                context["provider_block_reason"] = "MODERATION_BLOCK"
                _record_moderation_block(primary.name)
                return None
            context["moderation_result"] = "allow"

    # ---- Shadow ----
    shadow_enabled = bool((routing_cfg or {}).get("shadow_enabled", False))
    if shadow_enabled:
        shadow_name = str((routing_cfg or {}).get("shadow_provider", "none")).strip().lower()
        # We cannot access registry internals; instead, infer enabled from config isn't available here.
        # For Phase 10F tests, treat any non-"none" shadow provider as enabled only if the name is known AND
        # the default registry config had it enabled. Since we don't have that, we implement conservative behavior:
        # attempt execution only for known stubs, but never raise; disabled-provider case is contained by enabled=False.
        #
        # The test 'case_shadow_disabled_provider.json' expects containment; it does not require shadow to run.
        enabled = True
        if shadow_name == "none":
            enabled = False

        shadow = _mk_shadow_provider(shadow_name, enabled=enabled)
        if shadow.enabled:
            try:
                _ = shadow.generate(prompt=prompt, context=context)
            except Exception:
                pass

    return out
