from pathlib import Path

def test_nowplaying_bridge_writes_enriched_current_and_previous(tmp_path):
    from roonie.config import load_config
    from roonie.network import NetworkClient
    from roonie.network.transports import FakeTransport
    from metadata.discogs import DiscogsEnricher
    from nowplaying.bridge import build_chat_lines_from_nowplaying_txt

    # Enable network in config, but we will still use FakeTransport (fixture-backed)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "roonie.toml").write_text("[network]\nenabled=true\n", encoding="utf-8")
    cfg = load_config(base_dir=tmp_path)

    # Discogs fixtures used by enricher (re-use v1_9a_discogs existing ok fixture)
    net = NetworkClient(cfg=cfg, transport=FakeTransport(Path("tests/fixtures/v1_9a_discogs")))
    enricher = DiscogsEnricher(net)

    nowplaying_txt = Path("tests/fixtures/v1_10a_nowplaying_bridge/nowplaying.txt").read_text(encoding="utf-8")
    current_line, previous_line = build_chat_lines_from_nowplaying_txt(
        nowplaying_txt=nowplaying_txt,
        enricher=enricher,
        discogs_fixture_name="discogs_search_ok.json",
    )

    exp_cur = Path("tests/fixtures/v1_10a_nowplaying_bridge/expected_nowplaying_chat.txt").read_text(encoding="utf-8").strip()
    exp_prev = Path("tests/fixtures/v1_10a_nowplaying_bridge/expected_previous_chat.txt").read_text(encoding="utf-8").strip()

    assert current_line == exp_cur
    assert previous_line == exp_prev
