#!/usr/bin/env bash
# dev/droplet/push-data.sh — compress local SQLite archive and push to droplet
#
# Usage:
#   ./dev/droplet/push-data.sh                        # auto-find newest *.sqlite in data/
#   ./dev/droplet/push-data.sh path/to/archive.sqlite # explicit file
#
# Result: /var/www/mrija/updates/<timestamped>.sqlite.gz + manifest.json updated

set -euo pipefail

DROPLET="root@104.248.242.243"
SSH_KEY="$HOME/.ssh/digitalOcean"
UPDATES_DIR="/var/www/mrija/updates"

# ── find source SQLite ────────────────────────────────────────────────────────
if [[ "${1:-}" != "" ]]; then
    SRC="$1"
else
    SRC=$(find data/ -name "*.sqlite" -not -path "*/\.*" \
          | xargs ls -t 2>/dev/null | head -1)
fi

if [[ -z "$SRC" || ! -f "$SRC" ]]; then
    echo "ERROR: no SQLite found. Pass path as argument or put one under data/" >&2
    exit 1
fi

echo "source: $SRC"

# ── compress ──────────────────────────────────────────────────────────────────
TS=$(date -u +%Y%m%dT%H%M%SZ)
GZ_NAME="mrija-archive-${TS}.sqlite.gz"
TMP_GZ="/tmp/${GZ_NAME}"

echo "compressing → $GZ_NAME"
gzip -c "$SRC" > "$TMP_GZ"

SHA256=$(sha256sum "$TMP_GZ" | cut -d' ' -f1)
SIZE=$(stat -c%s "$TMP_GZ")

echo "sha256: $SHA256  size: $SIZE bytes"

# ── upload ────────────────────────────────────────────────────────────────────
echo "uploading to droplet..."
SSH_AUTH_SOCK="" scp -i "$SSH_KEY" "$TMP_GZ" "${DROPLET}:${UPDATES_DIR}/${GZ_NAME}"
rm "$TMP_GZ"

# ── update manifest.json ──────────────────────────────────────────────────────
echo "updating manifest.json..."
SSH_AUTH_SOCK="" ssh -i "$SSH_KEY" "$DROPLET" "
python3 - <<'PYEOF'
import json, pathlib, datetime

updates = pathlib.Path('$UPDATES_DIR')
manifest_path = updates / 'manifest.json'

manifest = {
    'version': '$TS',
    'url': '/updates/$GZ_NAME',
    'sha256': '$SHA256',
    'size': $SIZE,
    'created_at': datetime.datetime.now(datetime.UTC).isoformat(),
}

manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
print('manifest.json updated')
PYEOF
"

# ── extract for live service ──────────────────────────────────────────────────
echo "extracting SQLite for live service..."
SSH_AUTH_SOCK="" ssh -i "$SSH_KEY" "$DROPLET" "
    mkdir -p /opt/mrija/data
    zcat ${UPDATES_DIR}/${GZ_NAME} > /opt/mrija/data/current.sqlite
    echo 'extracted → /opt/mrija/data/current.sqlite'
"

# ── reload running service via API ────────────────────────────────────────────
KEY=$(SSH_AUTH_SOCK="" ssh -i "$SSH_KEY" "$DROPLET" \
    "grep MRIJA_API_KEY /opt/mrija/mrija.env | cut -d= -f2" 2>/dev/null)

echo "reloading service..."
curl -sf -X POST http://104.248.242.243:8080/api/open \
    -H "X-Api-Key: $KEY" \
    -H "Content-Type: application/json" \
    -d '{"path":"/opt/mrija/data/current.sqlite"}' | python3 -m json.tool

echo ""
echo "done — droplet serving $GZ_NAME, live API reloaded"
