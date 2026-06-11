#!/usr/bin/env bash
# Create a small read-only-share-friendly copy for a Windows VM.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TARGET="${1:-$(cd "$REPO_ROOT/.." && pwd)/mrijaWindowsClientShare}"

mkdir -p "$TARGET"

rsync -a --delete --delete-excluded \
  --exclude '.git/' \
  --exclude '.env' \
  --exclude '.env.*' \
  --exclude '.envrc' \
  --exclude '.claude/' \
  --exclude '.sisyphus/' \
  --exclude '.superpowers/' \
  --exclude '.idea/' \
  --exclude '.devenv/' \
  --exclude '.worktrees/' \
  --exclude 'data/' \
  --exclude 'logs/' \
  --exclude 'reports/' \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  --exclude '.mypy_cache/' \
  --exclude '.ruff_cache/' \
  --exclude '.venv/' \
  --exclude 'venv/' \
  --exclude '*.pyc' \
  --exclude '*.pyo' \
  --exclude '*.zip' \
  "$REPO_ROOT/" "$TARGET/"

cat > "$TARGET/VM-SHARE-README.txt" <<'EOF'
This is a small read-only VM share copy of the Docker-free Windows client branch.

Inside Windows:
  1. Open PowerShell.
  2. cd into this shared folder.
  3. Run:
       .\dev\windows\copy-from-readonly-share.ps1
  4. Then work from:
       C:\Dev\mrijaPageClean

Do not run the app directly from this read-only share.
EOF

echo "Windows VM share refreshed:"
echo "  $TARGET"
du -sh "$TARGET"
