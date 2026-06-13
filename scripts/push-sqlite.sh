#!/usr/bin/env bash
# push-sqlite.sh — Build client SQLite and push to the DO relay.
#
# This is the Docker-free client update path.  Unlike push-update.sh (which
# pushes MySQL dumps for the Docker installer), this script produces a
# mail_archive.sqlite that MrijaArchive.exe can download and use directly.
#
# Usage:
#   devenv shell -- ./scripts/push-sqlite.sh
#   devenv shell -- ./scripts/push-sqlite.sh --check
#   devenv shell -- ./scripts/push-sqlite.sh --dry-run
#
# Requires:
#   - data/index/mail_index.sqlite (built by sync-all / index-all)
#   - DO_RELAY_HOST set in environment or .env.push (e.g. "root@104.248.242.243")
#   - ~/.ssh/digitalOcean (or set DO_SSH_KEY)
#   - php, gzip, sha256sum, rsync, ssh on PATH

set -euo pipefail

MODE="publish"
case "${1:-}" in
    --check)   MODE="check" ;;
    --dry-run) MODE="dry-run" ;;
    -h|--help)
        sed -n '1,20p' "$0"
        exit 0
        ;;
    "") ;;
    *) echo "ERROR: unknown argument: $1" >&2; exit 1 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -f "$REPO_ROOT/.env.push" ]]; then
    set -a; source "$REPO_ROOT/.env.push"; set +a
fi

DO_RELAY_HOST="${DO_RELAY_HOST:-root@104.248.242.243}"
DO_SSH_KEY="${DO_SSH_KEY:-$HOME/.ssh/digitalOcean}"
KEEP_DUMPS="${KEEP_DUMPS:-3}"

SOURCE_SQLITE="$REPO_ROOT/data/index/mail_index.sqlite"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

check_requirements() {
    local missing=0
    for cmd in php gzip sha256sum rsync ssh ssh-add; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            echo "ERROR: missing command: $cmd" >&2
            missing=1
        fi
    done
    if [[ ! -f "$SOURCE_SQLITE" ]]; then
        echo "ERROR: source SQLite not found: $SOURCE_SQLITE" >&2
        echo "       Run: devenv shell -- sync-all" >&2
        missing=1
    fi
    if [[ ! -f "$DO_SSH_KEY" ]]; then
        echo "ERROR: SSH key not found: $DO_SSH_KEY" >&2
        missing=1
    fi
    if [[ "$missing" -ne 0 ]]; then exit 1; fi
    echo "OK: requirements present"
    echo "    source: $SOURCE_SQLITE ($(du -sh "$SOURCE_SQLITE" | cut -f1))"
    echo "    relay:  $DO_RELAY_HOST"
    echo "    key:    $DO_SSH_KEY"
}

if [[ "$MODE" == "check" ]]; then
    check_requirements
    exit 0
fi

check_requirements

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
CLIENT_SQLITE="$TMP_DIR/mail_archive.sqlite"
GZ_NAME="mrija-archive-${TIMESTAMP}.sqlite.gz"
GZ_PATH="$TMP_DIR/$GZ_NAME"

echo "==> Building client SQLite from $SOURCE_SQLITE…"
php "$REPO_ROOT/web/src/cli/build_client_sqlite.php" \
    --source "$SOURCE_SQLITE" \
    --output "$CLIENT_SQLITE"

echo "==> Compressing with gzip…"
gzip -9 -c "$CLIENT_SQLITE" > "$GZ_PATH"

SIZE="$(du -sh "$GZ_PATH" | cut -f1)"
SHA256="$(sha256sum "$GZ_PATH" | awk '{print $1}')"
echo "    $GZ_NAME  $SIZE  sha256=$SHA256"

if [[ "$MODE" == "dry-run" ]]; then
    echo "==> Dry run: archive built and verified locally, not uploading."
    echo "    Would upload: $GZ_NAME"
    exit 0
fi

ssh-add "$DO_SSH_KEY" 2>/dev/null || true
_SSH="ssh -i $DO_SSH_KEY"
echo "==> Uploading to ${DO_RELAY_HOST}…"
rsync -az --partial --append-verify -e "$_SSH" \
    "$GZ_PATH" "${DO_RELAY_HOST}:/var/www/mrija/updates/${GZ_NAME}.part"
$_SSH "$DO_RELAY_HOST" \
    "mv /var/www/mrija/updates/${GZ_NAME}.part /var/www/mrija/updates/${GZ_NAME}"

echo "==> Updating manifest and rotating old archives…"
$_SSH "$DO_RELAY_HOST" bash <<EOF
set -e
cd /var/www/mrija/updates

cat > manifest.json <<JSON
{
  "version":    "${TIMESTAMP}",
  "type":       "sqlite",
  "filename":   "${GZ_NAME}",
  "sha256":     "${SHA256}",
  "url":        "/updates/${GZ_NAME}",
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
JSON

ls -1t mrija-archive-*.sqlite.gz 2>/dev/null | tail -n +$((KEEP_DUMPS + 1)) | xargs -r rm -f

echo "Manifest updated. Current archives:"
ls -lh mrija-archive-*.sqlite.gz 2>/dev/null || echo "(none)"
EOF

echo "==> Done. Manifest at http://${DO_RELAY_HOST##*@}/updates/manifest.json"
