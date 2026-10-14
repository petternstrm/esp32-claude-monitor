# Build with: python -m PyInstaller --noconfirm ClaudeUsage.spec
a = Analysis(
    ['claude_monitor_mac.py'],
    pathex=[],
    binaries=[],
    datas=[],
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
    [],
    exclude_binaries=True,
    name='Claude Usage',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Claude Usage')
app = BUNDLE(
    coll,
    name='Claude Usage.app',
    bundle_identifier='com.internal.claude-usage-monitor',
    info_plist={
        'LSUIElement': True,
        'CFBundleShortVersionString': '1.0.0',
    },
)
