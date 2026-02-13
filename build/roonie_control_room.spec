# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

repo_root = Path.cwd()
src_dir = repo_root / "src"
datas = []
policy_path = repo_root / "persona" / "persona_policy.yaml"
if policy_path.exists():
    datas.append((str(policy_path), "persona"))

a = Analysis(
    [str(src_dir / "roonie" / "run_control_room.py")],
    pathex=[str(src_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="RoonieControlRoom",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

