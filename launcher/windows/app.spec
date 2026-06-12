# launcher/windows/app.spec
# PyInstaller spec for MrijaArchive.exe (Docker-free build)
# Run from launcher/windows/ with: pyinstaller app.spec
#
# PHP runtime expected at launcher/windows/php/ before building.
# Download NTS x64 from https://windows.php.net/download/ and unzip there.

import os
from pathlib import Path
block_cipher = None

REPO_ROOT = Path(SPECPATH).parent.parent
PHP_DIR   = Path(SPECPATH) / 'php'

# Build app_bundle.zip at spec-time.
# Contents: web/ PHP app (without secrets), nothing Docker-related.
import zipfile

bundle_zip = os.path.join(SPECPATH, 'app_bundle.zip')
with zipfile.ZipFile(bundle_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
    for dirpath, dirnames, filenames in os.walk(str(REPO_ROOT / 'web')):
        # Skip local.php (runtime secret) — local.php.client is included
        dirnames[:] = [d for d in dirnames if d not in ['__pycache__', '.git']]
        for fn in filenames:
            if fn == 'local.php':
                continue
            fpath = os.path.join(dirpath, fn)
            arcname = os.path.relpath(fpath, str(REPO_ROOT))
            zf.write(fpath, arcname)

# Bundle the PHP runtime as a sibling directory inside the frozen package.
php_datas = []
if PHP_DIR.exists():
    for dirpath, dirnames, filenames in os.walk(str(PHP_DIR)):
        dirnames[:] = [d for d in dirnames if d not in ['__pycache__']]
        for fn in filenames:
            fpath = os.path.join(dirpath, fn)
            arcname = os.path.relpath(fpath, str(Path(SPECPATH)))
            php_datas.append((fpath, str(Path(arcname).parent)))

a = Analysis(
    ['app.py'],
    pathex=[str(REPO_ROOT)],
    binaries=[],
    datas=[
        (bundle_zip, '.'),   # app_bundle.zip alongside exe in _MEIPASS
    ] + php_datas,           # php/ directory tree
    hiddenimports=['webview'],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
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
