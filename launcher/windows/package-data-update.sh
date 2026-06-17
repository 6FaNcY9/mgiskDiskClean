#!/usr/bin/env bash
# launcher/windows/package-data-update.sh
# Export updated mail_index.sqlite as a small zip for the boss.
# Run on Linux after sync-all has refreshed the data.
# Usage: bash launcher/windows/package-data-update.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SQLITE="$REPO_ROOT/data/index/mail_index.sqlite"
OUT="$REPO_ROOT/MrijaArchive-data-update.zip"

if [ ! -f "$SQLITE" ]; then
    echo "ERROR: $SQLITE not found. Run sync-all first."
    exit 1
fi

rm -f "$OUT"
zip -j "$OUT" "$SQLITE"
echo "Data update package: $OUT"
echo "Send to boss -> they drop mail_index.sqlite into:"
echo "  %APPDATA%\\MrijaArchive\\data\\index\\"
echo "Then click Stop + Start in the app to reimport."
