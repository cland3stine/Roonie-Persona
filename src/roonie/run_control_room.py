from __future__ import annotations

import argparse
import json
import os
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
    return p


def _browser_url(host: str, port: int) -> str:
    show_host = host.strip() or "127.0.0.1"
    if show_host == "0.0.0.0":
        show_host = "127.0.0.1"
    return f"http://{show_host}:{int(port)}"


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
    server = create_server(
        host=args.host,
        port=int(args.port),
        runs_dir=paths.runs_dir,
        readiness_state=preflight,
    )
    storage = getattr(server, "_roonie_storage", None)
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
        _append_log(paths.control_log_path, "SHUTDOWN")
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

