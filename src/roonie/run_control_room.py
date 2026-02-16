from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from roonie.control_room.preflight import resolve_runtime_paths, run_preflight
from roonie.dashboard_api.app import create_server


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = f"{_utc_now_iso()} {message}\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="roonie-control-room")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=int(os.getenv("ROONIE_DASHBOARD_PORT", "8787")))
    p.add_argument("--runs-dir", default="runs")
    p.add_argument("--log-dir", default="logs")
    p.add_argument("--open-browser", action="store_true")
    p.add_argument("--start-live-chat", action="store_true")
    p.add_argument("--live-account", default=os.getenv("ROONIE_LIVE_ACCOUNT", "bot"), choices=["bot", "broadcaster"])
    return p


def _browser_url(host: str, port: int) -> str:
    show_host = host.strip() or "127.0.0.1"
    if show_host == "0.0.0.0":
        show_host = "127.0.0.1"
    return f"http://{show_host}:{int(port)}"


def _apply_safe_start_defaults(storage: Any) -> None:
    if storage is None:
        return
    if hasattr(storage, "force_safe_start_defaults"):
        storage.force_safe_start_defaults()
        return
    if hasattr(storage, "set_armed"):
        storage.set_armed(False)


def _port_is_in_use(host: str, port: int) -> bool:
    bind_host = str(host or "").strip() or "0.0.0.0"
    if bind_host in {"localhost", "127.0.0.1"}:
        bind_host = "127.0.0.1"
    if bind_host == "::":
        bind_host = "0.0.0.0"
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind((bind_host, int(port)))
        return False
    except OSError:
        return True
    finally:
        try:
            sock.close()
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    args = _arg_parser().parse_args(argv)
    repo_root = Path.cwd()

    paths = resolve_runtime_paths(
        repo_root=repo_root,
        runs_dir=args.runs_dir,
        log_dir=args.log_dir,
    )
    os.environ["ROONIE_DASHBOARD_DATA_DIR"] = str(paths.data_dir)
    os.environ["ROONIE_DASHBOARD_LOGS_DIR"] = str(paths.logs_dir)
    os.environ["ROONIE_DASHBOARD_RUNS_DIR"] = str(paths.runs_dir)
    os.environ["ROONIE_DASHBOARD_PORT"] = str(int(args.port))

    _append_log(paths.control_log_path, "PRE-FLIGHT: starting")
    preflight = run_preflight(paths)
    _write_json(paths.preflight_json_path, preflight)
    if not bool(preflight.get("ready", False)):
        _append_log(paths.control_log_path, f"PRE-FLIGHT: failed {preflight.get('blocking_reasons', [])}")
        print("Control room preflight failed:")
        for reason in preflight.get("blocking_reasons", []):
            print(f"- {reason}")
        return 2

    _append_log(paths.control_log_path, "PRE-FLIGHT: passed")
    if _port_is_in_use(args.host, int(args.port)):
        message = (
            f"PORT_IN_USE: {args.host}:{int(args.port)} is already listening. "
            "Refusing to start a second control-room instance."
        )
        _append_log(paths.control_log_path, message)
        print(message)
        return 3

    _append_log(
        paths.control_log_path,
        f"RUNTIME: pid={os.getpid()} python={sys.executable}",
    )
    server = create_server(
        host=args.host,
        port=int(args.port),
        runs_dir=paths.runs_dir,
        readiness_state=preflight,
    )
    storage = getattr(server, "_roonie_storage", None)
    _apply_safe_start_defaults(storage)
    _append_log(paths.control_log_path, "SAFE-START: forced disarmed/output-disabled defaults")
    if storage is not None and hasattr(storage, "get_status"):
        try:
            status = storage.get_status().to_dict()
            active_director = str(status.get("active_director", "ProviderDirector"))
            routing_enabled = bool(status.get("routing_enabled", True))
            _append_log(
                paths.control_log_path,
                f"DIRECTOR: active={active_director} routing_enabled={routing_enabled}",
            )
        except Exception:
            pass
    if storage is not None and hasattr(storage, "set_readiness_state"):
        readiness = dict(preflight)
        items = list(readiness.get("items", []))
        items.append(
            {
                "name": "dashboard_api",
                "ok": True,
                "detail": f"listening on {_browser_url(args.host, int(args.port))}",
            }
        )
        readiness["items"] = items
        readiness["ready"] = True
        readiness["checked_at"] = _utc_now_iso()
        readiness["blocking_reasons"] = []
        storage.set_readiness_state(readiness)

    _append_log(paths.control_log_path, f"READY: {_browser_url(args.host, int(args.port))}")
    print(f"Roonie Control Room READY at {_browser_url(args.host, int(args.port))}")
    print(f"Data dir: {paths.data_dir}")
    print(f"Logs dir: {paths.logs_dir}")

    live_bridge = None
    eventsub_bridge = None
    if bool(args.start_live_chat) and storage is not None:
        from roonie.control_room.live_chat import LiveChatBridge
        from roonie.control_room.eventsub_bridge import EventSubBridge

        live_bridge = LiveChatBridge(
            storage=storage,
            account=str(args.live_account or "bot"),
            logger=lambda line: _append_log(paths.control_log_path, line),
        )
        live_bridge.start()
        _append_log(paths.control_log_path, f"LIVE-CHAT: started account={args.live_account}")
        print(f"Live chat bridge started (account={args.live_account}).")
        eventsub_bridge = EventSubBridge(
            storage=storage,
            live_bridge=live_bridge,
            logger=lambda line: _append_log(paths.control_log_path, line),
        )
        eventsub_bridge.start()
        _append_log(paths.control_log_path, "EVENTSUB: started (websocket transport)")
        print("EventSub bridge started.")
    elif bool(args.start_live_chat):
        _append_log(paths.control_log_path, "LIVE-CHAT: not started (storage unavailable)")
        print("Live chat bridge not started: storage unavailable.")

    if bool(args.open_browser):
        try:
            webbrowser.open(_browser_url(args.host, int(args.port)))
        except Exception:
            pass

    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        if eventsub_bridge is not None:
            eventsub_bridge.stop()
            eventsub_bridge.join(timeout=2.0)
        if live_bridge is not None:
            live_bridge.stop()
            live_bridge.join(timeout=2.0)
        _append_log(paths.control_log_path, "SHUTDOWN")
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
