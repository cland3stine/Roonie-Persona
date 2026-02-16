from pathlib import Path

def test_paths_default_resolve_to_data_dir(tmp_path):
    from roonie.config import load_config
    from roonie.paths import resolve_paths

    cfg = load_config(base_dir=tmp_path)
    paths = resolve_paths(base_dir=tmp_path, cfg=cfg)

    assert paths.base_dir == tmp_path
    assert paths.config_dir == tmp_path / "config"
    assert paths.data_dir == tmp_path / "data"
    assert paths.memory_db_path == tmp_path / "data" / "memory.sqlite"


def test_paths_respect_config_override(tmp_path):
    from roonie.config import load_config
    from roonie.paths import resolve_paths

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "roonie.toml").write_text(
        '[memory]\n'
        'db_path="data/custom.sqlite"\n'
        '\n'
        '[network]\n'
        'enabled=false\n',
        encoding="utf-8",
    )

    cfg = load_config(base_dir=tmp_path)
    paths = resolve_paths(base_dir=tmp_path, cfg=cfg)

    # load_config resolves db_path already; resolve_paths must preserve it
    assert paths.memory_db_path == (tmp_path / "data" / "custom.sqlite").resolve()
