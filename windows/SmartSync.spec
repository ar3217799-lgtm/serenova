# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas_wv, binaries_wv, hiddenimports_wv = collect_all('webview')

a = Analysis(
    ['launch.py'],
    pathex=[],
    binaries=binaries_wv,
    datas=datas_wv,
    hiddenimports=hiddenimports_wv + [
        'webview.platforms.winforms',
        'clr',
        'System',
        'System.Windows.Forms',
    ],
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
    name='SmartSync',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,   # ウィンドウアプリ（コンソール非表示）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,       # アイコンがあれば: icon='icon.ico'
)
