from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from src.metadata.discogs import DiscogsEnricher
from src.nowplaying.daemon import run_nowplaying_daemon
from src.roonie.config import load_config
from src.roonie.network import NetworkClient
from src.roonie.network.transports import FakeTransport, UrllibTransport


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nowplaying-daemon",
        description="Phase 10C.1: Run the nowplaying poller that bridges nowplaying.txt -> chat files.",
    )
    p.add_argument(
        "--overlay",
        required=True,
        help="Overlay directory containing nowplaying.txt (e.g. T:\\ on Windows or /mnt/overlay).",
    )
    p.add_argument(
        "--poll",
        type=float,
        default=0.25,
        help="Polling interval in seconds (default: 0.25).",
    )
    p.add_argument(
        "--discogs-fixture-dir",
        default=None,
        help="Optional: path to Discogs fixture directory for deterministic local runs (uses FakeTransport).",
    )
    p.add_argument(
        "--discogs-fixture-name",
        default=None,
        help="Optional: fixture name to use inside the Discogs enricher (e.g. discogs_search_ok.json).",
    )
    return p


def _build_enricher(*, base_dir: Path, fixture_dir: Optional[Path]) -> DiscogsEnricher:
    cfg = load_config(base_dir=base_dir)

    if fixture_dir is not None:
        transport = FakeTransport(fixture_dir)
    else:
        transport = UrllibTransport()

    net = NetworkClient(cfg=cfg, transport=transport)
    return DiscogsEnricher(net)


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    overlay_dir = Path(args.overlay)
    # Use overlay_dir as base_dir for config resolution (deterministic path rules already exist).
    base_dir = overlay_dir

    fixture_dir = Path(args.discogs_fixture_dir) if args.discogs_fixture_dir else None
    enricher = _build_enricher(base_dir=base_dir, fixture_dir=fixture_dir)

    run_nowplaying_daemon(
        overlay_dir=overlay_dir,
        enricher=enricher,
        discogs_fixture_name=args.discogs_fixture_name,
        poll_interval_seconds=args.poll,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
