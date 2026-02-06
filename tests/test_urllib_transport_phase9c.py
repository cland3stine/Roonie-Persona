import pytest

def test_urllib_transport_rejects_fixture_name(tmp_path):
    from src.roonie.network.transports_urllib import UrllibJsonTransport

    t = UrllibJsonTransport(user_agent="ROONIE-AI-Test/1.0", timeout_seconds=5)
    with pytest.raises(ValueError):
        t.get_json("https://example.invalid", fixture_name="anything.json")
