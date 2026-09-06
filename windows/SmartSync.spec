# -*- mode: python ; coding: utf-8 -*-
# PyWebView 不使用 — 標準ライブラリ + rsa（ライセンス検証）のみ、--onefile ビルド
a = Analysis(
    ['launch.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['licensing', 'rsa', 'rsa.pkcs1', 'rsa.key', 'rsa.core',
                   'rsa.common', 'rsa.transform', 'rsa.randnum', 'pyasn1',
                   'pyasn1.codec.der.decoder', 'pyasn1.codec.der.encoder'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['webview', 'pywebview', 'tkinter'],
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
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
