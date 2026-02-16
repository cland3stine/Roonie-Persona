from pathlib import Path

def _cfg_network_enabled(tmp_path):
    from roonie.config import load_config
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "roonie.toml").write_text("[network]\nenabled=true\n", encoding="utf-8")
    return load_config(base_dir=tmp_path)

def test_nowplaying_oneshot_writes_chat_files_atomically(tmp_path):
    from roonie.network import NetworkClient
    from roonie.network.transports import FakeTransport
    from metadata.discogs import DiscogsEnricher
    from nowplaying.oneshot import run_nowplaying_oneshot

    # Arrange: create a fake "T:\" directory structure in tmp_path
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir(parents=True, exist_ok=True)

    (overlay_dir / "nowplaying.txt").write_text(
        Path("tests/fixtures/v1_10b_nowplaying_oneshot/nowplaying.txt").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    cfg = _cfg_network_enabled(tmp_path)
    net = NetworkClient(cfg=cfg, transport=FakeTransport(Path("tests/fixtures/v1_9a_discogs")))
    enricher = DiscogsEnricher(net)

    # Act
    run_nowplaying_oneshot(
        overlay_dir=overlay_dir,
        enricher=enricher,
        discogs_fixture_name="discogs_search_ok.json",
    )

    # Assert
    exp_cur = Path("tests/fixtures/v1_10b_nowplaying_oneshot/expected_nowplaying_chat.txt").read_text(encoding="utf-8").strip()
    exp_prev = Path("tests/fixtures/v1_10b_nowplaying_oneshot/expected_previous_chat.txt").read_text(encoding="utf-8").strip()

    got_cur = (overlay_dir / "nowplaying_chat.txt").read_text(encoding="utf-8").strip()
    got_prev = (overlay_dir / "previous_chat.txt").read_text(encoding="utf-8").strip()

    assert got_cur == exp_cur
    assert got_prev == exp_prev
