# launcher/windows/app.spec
# PyInstaller spec for MrijaArchive.exe (pure Python — no PHP, no Docker)
# Run from launcher/windows/ with: pyinstaller app.spec

from pathlib import Path

block_cipher = None

REPO_ROOT = Path(SPECPATH).parent.parent
SRC_DIR   = REPO_ROOT / 'src'

a = Analysis(
    ['app.py'],
    pathex=[str(REPO_ROOT), str(SRC_DIR)],
    binaries=[],
    datas=[
        (str(SRC_DIR / 'mrija_client' / 'static'),    'mrija_client/static'),
        (str(SRC_DIR / 'mrija_client' / 'templates'),  'mrija_client/templates'),
    ],
    hiddenimports=[
        # mrija_client (lazy imports in server.py not visible to PyInstaller)
        'mrija_client',
        'mrija_client.api',
        'mrija_client.api.data',
        'mrija_client.api.control',
        'mrija_client.db',
        'mrija_client.server',
        'mrija_client.state',
        'mrija_client.updater',
        'mrija_client.tui',
        # pywebview Windows backend
        'webview',
        'webview.platforms.winforms',
        # uvicorn internals (dynamic imports)
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        # async backend
        'anyio',
        'anyio._backends._asyncio',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=['textual'],  # Linux TUI only — not needed in the Windows exe
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='MrijaArchive',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=None,
)
