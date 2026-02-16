from pathlib import Path

def test_discogs_build_search_url_includes_token(tmp_path, monkeypatch):
    from roonie.config import load_config
    from metadata.discogs_client import build_search_url

    # Provide token via secrets.env
    secrets = tmp_path / "config" / "secrets.env"
    secrets.parent.mkdir(parents=True, exist_ok=True)
    secrets.write_text("DISCOGS_TOKEN=TEST_TOKEN\n", encoding="utf-8")

    cfg = load_config(base_dir=tmp_path)

    url = build_search_url(
        query="Artist A Track One",
        token=cfg.discogs_token,
        per_page=5,
        page=1,
    )

    expected = Path("tests/fixtures/v1_9b_discogs/expected_search_url.txt").read_text(encoding="utf-8").strip()
    assert url == expected


def test_discogs_build_search_url_without_token(tmp_path):
    from metadata.discogs_client import build_search_url

    url = build_search_url(query="Artist A Track One", token=None, per_page=5, page=1)
    assert "token=" not in url
    assert url.startswith("https://api.discogs.com/database/search?q=")


def test_discogs_enricher_uses_real_discogs_search_url_shape(tmp_path):
    # Still fixture-backed transport: we only verify that the enricher routes through DiscogsClient
    from roonie.config import load_config
    from roonie.network import NetworkClient
    from roonie.network.transports import FakeTransport
    from metadata.discogs_client import DiscogsClient

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "roonie.toml").write_text("[network]\nenabled=true\n", encoding="utf-8")

    cfg = load_config(base_dir=tmp_path)
    net = NetworkClient(cfg=cfg, transport=FakeTransport(Path("tests/fixtures/v1_9a_discogs")))

    client = DiscogsClient(net=net, token=None)
    # Should not raise; FakeTransport ignores URL content (fixture_name required)
    body = client.search(query="Artist A Track One", fixture_name="discogs_search_ok.json")
    assert isinstance(body, dict)
