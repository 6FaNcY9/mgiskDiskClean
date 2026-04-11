# launcher/windows/app.spec
# PyInstaller spec for MrijaArchive.exe
# Run from launcher/windows/ with: pyinstaller app.spec

import os
from pathlib import Path
block_cipher = None

# Root of repo (two levels up from launcher/windows/)
REPO_ROOT = Path(SPECPATH).parent.parent

# Build the app_bundle.zip at spec-time so it's fresh
import zipfile, shutil, tempfile

bundle_zip = os.path.join(SPECPATH, 'app_bundle.zip')
with zipfile.ZipFile(bundle_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
    for pattern, base in [
        (REPO_ROOT / 'docker-compose.yml',     REPO_ROOT),
        (REPO_ROOT / 'Dockerfile',              REPO_ROOT),
        (REPO_ROOT / 'pyproject.toml',          REPO_ROOT),
    ]:
        if Path(pattern).exists():
            zf.write(str(pattern), str(Path(pattern).relative_to(base)))
    for dirpath, dirnames, filenames in os.walk(str(REPO_ROOT / 'web')):
        # skip config/local.php (secrets) — local.php.docker is included
        for fn in filenames:
            if fn == 'local.php':
                continue
            fpath = os.path.join(dirpath, fn)
            arcname = os.path.relpath(fpath, str(REPO_ROOT))
            zf.write(fpath, arcname)
    for dirpath, dirnames, filenames in os.walk(str(REPO_ROOT / 'src')):
        for fn in filenames:
            fpath = os.path.join(dirpath, fn)
            arcname = os.path.relpath(fpath, str(REPO_ROOT))
            zf.write(fpath, arcname)

a = Analysis(
    ['app.py'],
    pathex=[str(REPO_ROOT)],
    binaries=[],
    datas=[
        (bundle_zip, '.'),  # bundled as app_bundle.zip alongside exe in _MEIPASS
    ],
    hiddenimports=['webview', 'clr'],
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
    console=False,          # no terminal window
    icon=None,              # add icon path here if you have one
)
