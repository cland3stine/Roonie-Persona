from pathlib import Path

def _cfg_network_enabled(tmp_path):
    from roonie.config import load_config
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "roonie.toml").write_text("[network]\nenabled=true\n", encoding="utf-8")
    return load_config(base_dir=tmp_path)

def test_discogs_matching_normalizes_dashes_and_whitespace_and_tiebreaks_by_lowest_id(tmp_path):
    from roonie.network import NetworkClient
    from roonie.network.transports import FakeTransport
    from metadata.discogs import DiscogsEnricher

    cfg = _cfg_network_enabled(tmp_path)
    transport = FakeTransport(fixtures_dir=Path("tests/fixtures/v1_9d_discogs"))
    net = NetworkClient(cfg=cfg, transport=transport)

    enricher = DiscogsEnricher(net)
    meta = enricher.enrich_track(
        artist="Artist A",
        title="Track One",
        fixture_name="discogs_search_variants.json",
    )

    assert meta is not None
    assert meta.release_id == 200  # lowest id wins under tie-break A
    assert meta.label == "Label Y"
    assert meta.catno == "Y-001"
