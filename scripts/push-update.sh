#!/usr/bin/env bash
# push-update.sh — Dump the local MariaDB archive and push to the DO relay.
#
# Usage:
#   ./scripts/push-update.sh
#   ./scripts/push-update.sh --check
#   ./scripts/push-update.sh --dry-run
#
# Requires:
#   - Docker Compose stack running locally (or .env with DB creds)
#   - DO_RELAY_HOST set in environment or .env.push (e.g. "root@<ip>")
#   - ~/.ssh/do_mrija SSH key for the droplet
#   - scp / ssh on PATH

set -euo pipefail

MODE="publish"
case "${1:-}" in
    --check) MODE="check" ;;
    --dry-run) MODE="dry-run" ;;
    -h|--help)
        sed -n '1,20p' "$0"
        exit 0
        ;;
    "")
        ;;
    *)
        echo "ERROR: unknown argument: $1" >&2
        exit 1
        ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Load .env.push if present (keys: DO_RELAY_HOST, DO_SSH_KEY)
if [[ -f "$REPO_ROOT/.env.push" ]]; then
    # shellcheck disable=SC1090
    set -a; source "$REPO_ROOT/.env.push"; set +a
fi

DO_RELAY_HOST="${DO_RELAY_HOST:-}"
DO_SSH_KEY="${DO_SSH_KEY:-$HOME/.ssh/do_mrija}"
KEEP_DUMPS="${KEEP_DUMPS:-3}"

if [[ -z "$DO_RELAY_HOST" ]]; then
    echo "ERROR: DO_RELAY_HOST is not set. Export it or add to .env.push." >&2
    exit 1
fi

# Load DB creds from .env
if [[ -f "$REPO_ROOT/.env" ]]; then
    # shellcheck disable=SC1090
    set -a; source "$REPO_ROOT/.env"; set +a
fi

DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-3306}"
DB_NAME="${MRIJA_DB_NAME:-mailreview}"
DB_USER="${MRIJA_DB_USER:-mailreview}"
DB_PASS="${MRIJA_DB_PASSWORD:-}"

check_requirements() {
    local missing=0
    for cmd in mysqldump gzip sha256sum scp ssh; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            echo "ERROR: missing command: $cmd" >&2
            missing=1
        fi
    done
    if [[ ! -f "$DO_SSH_KEY" ]]; then
        echo "ERROR: SSH key not found: $DO_SSH_KEY" >&2
        missing=1
    fi
    if [[ -z "$DB_PASS" ]]; then
        echo "WARN: MRIJA_DB_PASSWORD is empty; this is only valid for socket/auth-local setups." >&2
    fi
    if [[ "$missing" -ne 0 ]]; then
        exit 1
    fi
    echo "OK: requirements present"
    echo "    relay: $DO_RELAY_HOST"
    echo "    ssh key: $DO_SSH_KEY"
    echo "    database: $DB_NAME at $DB_HOST:$DB_PORT as $DB_USER"
}

if [[ "$MODE" == "check" ]]; then
    check_requirements
    exit 0
fi

check_requirements

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DUMP_NAME="mrija-${TIMESTAMP}.sql.gz"
TMP_DUMP="/tmp/${DUMP_NAME}"

echo "==> Dumping ${DB_NAME} from ${DB_HOST}:${DB_PORT}…"
mysqldump \
    --host="$DB_HOST" \
    --port="$DB_PORT" \
    --user="$DB_USER" \
    --password="$DB_PASS" \
    --single-transaction \
    --skip-lock-tables \
    --no-tablespaces \
    "$DB_NAME" \
  | gzip -9 > "$TMP_DUMP"

DUMP_SIZE="$(du -sh "$TMP_DUMP" | cut -f1)"
SHA256="$(sha256sum "$TMP_DUMP" | awk '{print $1}')"
echo "    Size: $DUMP_SIZE  SHA-256: $SHA256"

if [[ "$MODE" == "dry-run" ]]; then
    echo "==> Dry run: dump created and verified locally, not uploading."
    echo "    Would upload: ${DUMP_NAME}"
    rm -f "$TMP_DUMP"
    exit 0
fi

echo "==> Uploading to ${DO_RELAY_HOST}…"
scp -i "$DO_SSH_KEY" -q "$TMP_DUMP" "${DO_RELAY_HOST}:/var/www/mrija/updates/${DUMP_NAME}"
rm -f "$TMP_DUMP"

echo "==> Updating manifest and rotating old dumps…"
# shellcheck disable=SC2087
ssh -i "$DO_SSH_KEY" "$DO_RELAY_HOST" bash <<EOF
set -e
cd /var/www/mrija/updates

# Write fresh manifest.json
cat > manifest.json <<JSON
{
  "version":    "${TIMESTAMP}",
  "filename":   "${DUMP_NAME}",
  "sha256":     "${SHA256}",
  "url":        "/updates/${DUMP_NAME}",
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
JSON

# Keep only the $KEEP_DUMPS newest dumps (by filename, which is date-sorted)
ls -1t mrija-*.sql.gz 2>/dev/null | tail -n +$((KEEP_DUMPS + 1)) | xargs -r rm -f

echo "Manifest updated. Current dumps:"
ls -lh mrija-*.sql.gz 2>/dev/null || echo "(none)"
EOF

echo "==> Done. Manifest at http://${DO_RELAY_HOST##*@}/updates/manifest.json"
