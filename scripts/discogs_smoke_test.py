from pathlib import Path

from roonie.config import load_config
from roonie.network import NetworkClient
from roonie.network.transports_urllib import UrllibJsonTransport
from metadata.discogs_client import DiscogsClient

def main():
    base_dir = Path(".").resolve()
    cfg = load_config(base_dir=base_dir)

    if not cfg.network_enabled:
        raise SystemExit("Network disabled. Set [network].enabled=true in config/roonie.toml")

    if not cfg.discogs_token:
        raise SystemExit("Missing DISCOGS_TOKEN in config/secrets.env")

    transport = UrllibJsonTransport(user_agent="ROONIE-AI/0.1 (contact: local)")
    net = NetworkClient(cfg=cfg, transport=transport)

    client = DiscogsClient(net=net, token=cfg.discogs_token)
    body = client.search(query="Artist A Track One", fixture_name=None, per_page=3, page=1)
    print(body)

if __name__ == "__main__":
    main()
