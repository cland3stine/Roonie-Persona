from pathlib import Path

def _cfg_network_enabled(tmp_path):
    from roonie.config import load_config
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "roonie.toml").write_text("[network]\nenabled=true\n", encoding="utf-8")
    return load_config(base_dir=tmp_path)

def test_discogs_enrich_exact_match(tmp_path):
    from roonie.network import NetworkClient
    from roonie.network.transports import FakeTransport
    from metadata.discogs import DiscogsEnricher

    cfg = _cfg_network_enabled(tmp_path)
    transport = FakeTransport(fixtures_dir=Path("tests/fixtures/v1_9a_discogs"))
    net = NetworkClient(cfg=cfg, transport=transport)

    enricher = DiscogsEnricher(net)
    meta = enricher.enrich_track(
        artist="Artist A",
        title="Track One",
        fixture_name="discogs_search_ok.json",
    )

    assert meta is not None
    assert meta.release_id == 111
    assert meta.year == 2020
    assert meta.label == "Label One"
    assert meta.catno == "CAT001"
    assert "Electronic" in meta.genres
    assert "Progressive House" in meta.styles

def test_discogs_enrich_no_results(tmp_path):
    from roonie.network import NetworkClient
    from roonie.network.transports import FakeTransport
    from metadata.discogs import DiscogsEnricher

    cfg = _cfg_network_enabled(tmp_path)
    transport = FakeTransport(fixtures_dir=Path("tests/fixtures/v1_9a_discogs"))
    net = NetworkClient(cfg=cfg, transport=transport)

    enricher = DiscogsEnricher(net)
    meta = enricher.enrich_track(
        artist="Nope",
        title="Nothing",
        fixture_name="discogs_search_empty.json",
    )
    assert meta is None

def test_discogs_enrich_deterministic_exact_only(tmp_path):
    # Ensures we do NOT pick a near-match (Remix) in Phase 9A
    from roonie.network import NetworkClient
    from roonie.network.transports import FakeTransport
    from metadata.discogs import DiscogsEnricher

    cfg = _cfg_network_enabled(tmp_path)
    transport = FakeTransport(fixtures_dir=Path("tests/fixtures/v1_9a_discogs"))
    net = NetworkClient(cfg=cfg, transport=transport)

    enricher = DiscogsEnricher(net)
    meta = enricher.enrich_track(
        artist="Artist A",
        title="Track One (Remix)",
        fixture_name="discogs_search_ok.json",
    )

    assert meta is not None
    assert meta.release_id == 222
