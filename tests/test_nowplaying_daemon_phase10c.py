from pathlib import Path

def _cfg_network_enabled(tmp_path):
    from src.roonie.config import load_config
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "roonie.toml").write_text("[network]\nenabled=true\n", encoding="utf-8")
    return load_config(base_dir=tmp_path)

def test_nowplaying_daemon_processes_change_once_and_writes_chat_files(tmp_path):
    from src.roonie.network import NetworkClient
    from src.roonie.network.transports import FakeTransport
    from src.metadata.discogs import DiscogsEnricher
    from src.nowplaying.daemon import run_nowplaying_daemon

    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir(parents=True, exist_ok=True)

    # Start at state A
    (overlay_dir / "nowplaying.txt").write_text(
        Path("tests/fixtures/v1_10c_nowplaying_daemon/nowplaying_A.txt").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    cfg = _cfg_network_enabled(tmp_path)
    net = NetworkClient(cfg=cfg, transport=FakeTransport(Path("tests/fixtures/v1_9a_discogs")))
    enricher = DiscogsEnricher(net)

    def no_sleep(_seconds: float) -> None:
        return

    # Run first tick
    run_nowplaying_daemon(
        overlay_dir=overlay_dir,
        enricher=enricher,
        discogs_fixture_name="discogs_search_ok.json",
        poll_interval_seconds=0.25,
        max_ticks=1,
        sleep_fn=no_sleep,
    )

    # Update to state B
    (overlay_dir / "nowplaying.txt").write_text(
        Path("tests/fixtures/v1_10c_nowplaying_daemon/nowplaying_B.txt").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    # Run second tick
    run_nowplaying_daemon(
        overlay_dir=overlay_dir,
        enricher=enricher,
        discogs_fixture_name="discogs_search_ok.json",
        poll_interval_seconds=0.25,
        max_ticks=1,
        sleep_fn=no_sleep,
    )

    exp_cur = Path("tests/fixtures/v1_10c_nowplaying_daemon/expected_nowplaying_chat_after_B.txt").read_text(encoding="utf-8").strip()
    exp_prev = Path("tests/fixtures/v1_10c_nowplaying_daemon/expected_previous_chat_after_B.txt").read_text(encoding="utf-8").strip()

    got_cur = (overlay_dir / "nowplaying_chat.txt").read_text(encoding="utf-8").strip()
    got_prev = (overlay_dir / "previous_chat.txt").read_text(encoding="utf-8").strip()

    assert got_cur == exp_cur
    assert got_prev == exp_prev
