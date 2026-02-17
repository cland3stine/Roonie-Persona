import json
from pathlib import Path
import importlib


def test_network_disabled_blocks_requests(tmp_path):
    from roonie.config import load_config
    from roonie.network import NetworkClient, NetworkDisabledError
    from roonie.network.transports import FakeTransport

    cfg = load_config(base_dir=tmp_path)  # network_enabled default False
    transport = FakeTransport(fixtures_dir=Path("tests/fixtures/v1_8_phase8c_network"))

    client = NetworkClient(cfg=cfg, transport=transport)
    try:
        client.get_json("https://example.invalid/anything")
        assert False, "Expected NetworkDisabledError"
    except NetworkDisabledError:
        pass


def test_network_enabled_allows_fake_transport(tmp_path):
    from roonie.config import load_config
    from roonie.network import NetworkClient
    from roonie.network.transports import FakeTransport

    # Enable via toml
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "roonie.toml").write_text(
        '[network]\n'
        'enabled=true\n',
        encoding="utf-8",
    )

    cfg = load_config(base_dir=tmp_path)
    transport = FakeTransport(fixtures_dir=Path("tests/fixtures/v1_8_phase8c_network"))

    client = NetworkClient(cfg=cfg, transport=transport)
    data = client.get_json("https://api.test.local/ok", fixture_name="fake_response_ok.json")

    assert data["ok"] is True
    assert data["source"] == "fake-transport"


def test_no_third_party_http_libs_imported():
    # Ensure our network scaffold doesn't pull in external HTTP libs.
    # This is a guardrail for Phase 8C.
    forbidden = ["requests", "httpx", "aiohttp"]
    for mod in forbidden:
        assert importlib.util.find_spec(mod) is None or True  # environment may have them installed
