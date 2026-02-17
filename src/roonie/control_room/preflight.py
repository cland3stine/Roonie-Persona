from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from providers.router import get_provider_runtime_status, get_routing_runtime_status


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


@dataclass(frozen=True)
class RuntimePaths:
    repo_root: Path
    runtime_root: Path
    data_dir: Path
    logs_dir: Path
    runs_dir: Path
    persona_policy_path: Path

    @property
    def control_log_path(self) -> Path:
        return self.logs_dir / "control_room.log"

    @property
    def preflight_json_path(self) -> Path:
        return self.logs_dir / "preflight.json"


def _resolve_path(base: Path, raw: str) -> Path:
    p = Path(raw)
    if p.is_absolute():
        return p
    return (base / p).resolve()


def resolve_runtime_paths(
    *,
    repo_root: Path,
    runs_dir: str = "runs",
    log_dir: str = "logs",
) -> RuntimePaths:
    repo = repo_root.resolve()
    runtime_root = repo

    data_env = (os.getenv("ROONIE_DASHBOARD_DATA_DIR") or "").strip()
    logs_env = (os.getenv("ROONIE_DASHBOARD_LOGS_DIR") or "").strip()
    runs_env = (os.getenv("ROONIE_DASHBOARD_RUNS_DIR") or "").strip()
    persona_env = (os.getenv("ROONIE_PERSONA_POLICY_PATH") or "").strip()

    data_dir = _resolve_path(runtime_root, data_env) if data_env else (runtime_root / "data")
    logs_dir = _resolve_path(runtime_root, logs_env) if logs_env else _resolve_path(runtime_root, log_dir)
    runs_path_text = runs_env or runs_dir
    runs_path = _resolve_path(runtime_root, runs_path_text)

    if persona_env:
        persona_path = _resolve_path(repo, persona_env)
    else:
        persona_candidate = repo / "persona" / "persona_policy.yaml"
        if persona_candidate.exists():
            persona_path = persona_candidate
        else:
            persona_path = repo / "persona_policy.yaml"

    return RuntimePaths(
        repo_root=repo,
        runtime_root=runtime_root,
        data_dir=data_dir,
        logs_dir=logs_dir,
        runs_dir=runs_path,
        persona_policy_path=persona_path,
    )


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def _default_senses_config() -> Dict[str, Any]:
    return {
        "enabled": False,
        "local_only": True,
        "whitelist": ["Art", "Jen"],
        "purpose": "avoid_interrupting_hosts",
        "never_initiate": True,
        "never_publicly_reference_detection": True,
        "no_viewer_recognition": True,
    }


def _default_studio_profile() -> Dict[str, Any]:
    now = _utc_now_iso()
    return {
        "version": 1,
        "updated_at": now,
        "updated_by": "system",
        "location": {"display": "Washington DC area"},
        "social_links": [],
        "gear": [],
        "faq": [{"q": "Where are you based?", "a": "Washington DC area."}],
        "approved_emotes": [],
    }


def _default_twitch_config() -> Dict[str, Any]:
    return {
        "version": 1,
        "primary_channel": "",
        "bot_account_name": "RoonieTheCat",
        "broadcaster_account_name": "RuleOfRune",
    }


def _load_persona_policy(path: Path) -> Tuple[bool, str]:
    if not path.exists():
        return False, f"missing file: {path}"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"read failed: {exc}"

    lines = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(stripped)
    if not lines:
        return False, "file contains no YAML content"
    if not any(":" in line for line in lines):
        return False, "invalid YAML-like structure (missing key:value lines)"

    top_keys = []
    for line in lines:
        if line.startswith("-") or line.startswith(" "):
            continue
        if ":" not in line:
            continue
        key = line.split(":", 1)[0].strip()
        if key:
            top_keys.append(key)
    if not top_keys:
        return False, "invalid YAML-like structure (no top-level keys)"
    return True, f"loaded ({len(top_keys)} top-level keys)"


def run_preflight(paths: RuntimePaths) -> Dict[str, Any]:
    checked_at = _utc_now_iso()
    items: List[Dict[str, Any]] = []
    blocking_reasons: List[str] = []

    def add(name: str, ok: bool, detail: str, *, blocking: bool = False) -> None:
        items.append({"name": name, "ok": bool(ok), "detail": str(detail)})
        if blocking and not ok:
            blocking_reasons.append(f"{name}: {detail}")

    paths.data_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    paths.runs_dir.mkdir(parents=True, exist_ok=True)
    add("runtime_paths", True, f"data={paths.data_dir} logs={paths.logs_dir} runs={paths.runs_dir}")

    ok_policy, detail_policy = _load_persona_policy(paths.persona_policy_path)
    add("persona_policy", ok_policy, detail_policy, blocking=True)

    os.environ["ROONIE_DASHBOARD_DATA_DIR"] = str(paths.data_dir)
    os.environ["ROONIE_DASHBOARD_LOGS_DIR"] = str(paths.logs_dir)
    os.environ["ROONIE_DASHBOARD_RUNS_DIR"] = str(paths.runs_dir)
    os.environ.setdefault("ROONIE_PROVIDERS_CONFIG_PATH", str(paths.data_dir / "providers_config.json"))
    os.environ.setdefault("ROONIE_ROUTING_CONFIG_PATH", str(paths.data_dir / "routing_config.json"))

    memory_db_path = paths.data_dir / "memory.sqlite"
    try:
        with sqlite3.connect(str(memory_db_path)) as conn:
            conn.execute("SELECT 1").fetchone()
        add("memory_db", True, f"reachable at {memory_db_path}")
    except sqlite3.Error as exc:
        add("memory_db", False, f"sqlite error: {exc}", blocking=True)

    try:
        provider_status = get_provider_runtime_status()
        providers_path = Path(str(os.getenv("ROONIE_PROVIDERS_CONFIG_PATH", str(paths.data_dir / "providers_config.json"))))
        add("providers_config", providers_path.exists(), f"path={providers_path} active={provider_status.get('active_provider')}", blocking=True)
    except Exception as exc:
        add("providers_config", False, f"seed failed: {exc}", blocking=True)

    try:
        routing_status = get_routing_runtime_status()
        routing_path = Path(str(os.getenv("ROONIE_ROUTING_CONFIG_PATH", str(paths.data_dir / "routing_config.json"))))
        add(
            "routing_config",
            routing_path.exists(),
            f"path={routing_path} enabled={bool(routing_status.get('enabled', False))}",
            blocking=True,
        )
        if bool(routing_status.get("enabled", False)):
            add("routing_default_safety", True, "routing enabled by persisted Director config")
        else:
            add("routing_default_safety", True, "routing disabled")
    except Exception as exc:
        add("routing_config", False, f"seed failed: {exc}", blocking=True)

    senses_path = paths.data_dir / "senses_config.json"
    try:
        if not senses_path.exists():
            _write_json_atomic(senses_path, _default_senses_config())
        senses_raw = _read_json(senses_path)
        senses_enabled = bool(senses_raw.get("enabled", False)) if isinstance(senses_raw, dict) else False
        add("senses_config", True, f"path={senses_path} enabled={senses_enabled}")
        add("senses_hard_lock", not senses_enabled, "enabled must remain false", blocking=True)
    except Exception as exc:
        add("senses_config", False, f"seed/read failed: {exc}", blocking=True)

    studio_path = paths.data_dir / "studio_profile.json"
    try:
        if not studio_path.exists():
            _write_json_atomic(studio_path, _default_studio_profile())
        _ = _read_json(studio_path)
        add("studio_profile", True, f"path={studio_path}")
    except Exception as exc:
        add("studio_profile", False, f"seed/read failed: {exc}", blocking=True)

    twitch_config_path = paths.data_dir / "twitch_config.json"
    try:
        if not twitch_config_path.exists():
            _write_json_atomic(twitch_config_path, _default_twitch_config())
        twitch_cfg = _read_json(twitch_config_path)
        primary = ""
        if isinstance(twitch_cfg, dict):
            primary = str(twitch_cfg.get("primary_channel", "")).strip().lstrip("#")
        env_primary = str(os.getenv("TWITCH_CHANNEL") or os.getenv("PRIMARY_CHANNEL") or "").strip().lstrip("#")
        if not primary and env_primary:
            primary = env_primary.lower()
            payload = dict(twitch_cfg) if isinstance(twitch_cfg, dict) else _default_twitch_config()
            payload["primary_channel"] = primary
            _write_json_atomic(twitch_config_path, payload)
        add("twitch_config", True, f"path={twitch_config_path} primary_channel={primary or '<unset>'}")
    except Exception as exc:
        add("twitch_config", False, f"seed/read failed: {exc}", blocking=True)

    kill_keys = ["ROONIE_KILL_SWITCH", "KILL_SWITCH", "ROONIE_KILL_SWITCH_ON"]
    kill_present = any(name in os.environ for name in kill_keys)
    kill_value = _to_bool(os.getenv("ROONIE_KILL_SWITCH"), True)
    if not kill_present:
        add("kill_switch_default", True, "env unset -> defaults to ON (safe)")
    else:
        add("kill_switch_default", True, f"env present -> interpreted as {kill_value}")

    armed_keys = ["ROONIE_ARMED", "ROONIE_ARM", "ARMED"]
    armed_detected = any(_to_bool(os.getenv(name), False) for name in armed_keys)
    output_enabled = _to_bool(os.getenv("TWITCH_OUTPUT_ENABLED"), False)
    output_disabled = _to_bool(os.getenv("ROONIE_OUTPUT_DISABLED"), True)
    write_path_armed = armed_detected or (output_enabled and not output_disabled)
    if write_path_armed:
        add(
            "startup_armed_guard",
            False,
            "startup appears armed via env (ROONIE_ARM* / TWITCH_OUTPUT_ENABLED / ROONIE_OUTPUT_DISABLED)",
            blocking=True,
        )
    else:
        add("startup_armed_guard", True, "startup write path is disarmed/suppressed")

    ready = len(blocking_reasons) == 0
    return {
        "ready": ready,
        "checked_at": checked_at,
        "items": items,
        "blocking_reasons": blocking_reasons,
    }
